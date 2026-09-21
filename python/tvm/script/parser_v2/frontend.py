# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Source acquisition and declaration/body execution for registered builders."""

import __future__

import ast
import copy
import inspect
import linecache
import textwrap
from dataclasses import dataclass, field
from functools import wraps
from types import SimpleNamespace
from typing import TypeVar

import tvm
from tvm import ir
from tvm.error import DiagnosticError
from tvm.script.ir_builder import IRBuilder, protocol
from tvm.script.ir_builder import ir as I

from .annotations import AnnotationScope
from .diagnostics import diagnostic_error
from .functions import (
    FunctionGroup,
    adapt_python_module,
    attach_python,
    declare_python,
    is_python_function,
)

_NAMESPACES = {}


def register_namespace(alias, namespace):
    """Let an entry module supply a source-level namespace without reverse imports."""
    _NAMESPACES[alias] = namespace


def _resolve(node, env, filename):
    expression = ast.Expression(copy.deepcopy(node))
    return eval(compile(ast.fix_missing_locations(expression), filename, "eval"), env)


def _closure_values(function):
    values = {}
    for name, cell in zip(function.__code__.co_freevars, function.__closure__ or ()):
        try:
            values[name] = cell.cell_contents
        except ValueError:
            # Recursive and later-bound locals are empty until the helper is used.
            pass
    return values


def _capture(obj):
    target = obj if inspect.isfunction(obj) else None
    module = inspect.getmodule(obj)
    env = dict(vars(module)) if module is not None else {}
    env.update(getattr(target, "__globals__", {}))
    if target is not None:
        env.update(_closure_values(target))
    filename = inspect.getsourcefile(obj)
    # Deferred annotations may be the only use of an enclosing local, so Python
    # need not put that value in the function's closure cells.
    frames = inspect.stack()
    try:
        for info in reversed(frames):
            if info.filename == filename:
                env.update(info.frame.f_locals)
    finally:
        del frames
    if inspect.isclass(obj):
        env.update(vars(obj))
    return env


def _inside_class(function):
    frame = inspect.currentframe().f_back
    try:
        while frame is not None:
            local = frame.f_locals
            if local.get("__module__") == function.__module__ and "__qualname__" in local:
                return True
            if frame.f_code.co_filename == function.__code__.co_filename:
                return False
            frame = frame.f_back
    finally:
        del frame
    return False


def make_decorator(builder, *, option_map=None, defaults=None):
    """Create a parsing decorator whose construction policy belongs to its caller."""
    mapping, default_options = dict(option_map or {}), dict(defaults or {})

    def decorator(function=None, **options):
        if function is not None and not inspect.isfunction(function):
            raise ValueError("Construction decorators require a function or keyword options")

        def apply(function):
            function.__tvm_function_kind__ = decorator.__tvm_function_kind__
            function.__tvm_function_options__ = options
            if _inside_class(function):
                return function
            return parse(function, _capture(function))

        return apply(function) if function is not None else apply

    return protocol.register_function(
        decorator, builder, option_map=mapping, defaults=default_options
    )


def make_helper(builder, *, preserve_return=True, late_binding=False):
    """Create a construction helper, optionally retaining live Python closure cells."""

    def decorator(function=None, **options):
        if function is not None and not inspect.isfunction(function):
            raise ValueError("Construction decorators require a function or keyword options")

        def apply(function):
            definition_env = _capture(function)

            @wraps(function)
            def invoke(*args, **kwargs):
                bound = inspect.signature(function).bind(*args, **kwargs)
                bound.apply_defaults()
                environment = (
                    {**definition_env, **(_closure_values(function) if late_binding else {})}
                    if options.get("hygienic", True)
                    else _capture(function)
                )
                compiler = Compiler(function, environment)
                node = compiler.tree.body[0]
                return compiler.run_statements(
                    node.body,
                    builder,
                    {**compiler.env, **bound.arguments},
                    set(bound.arguments),
                    preserve_return=preserve_return,
                )

            invoke.__tvm_construction_helper__ = (builder, options)
            return invoke

        return apply(function) if function is not None else apply

    return decorator


def pyfunc(function):
    """Mark a function whose body and execution remain ordinary Python."""
    function.__tvm_python_function__ = True
    return function


protocol.register_function(pyfunc, None, python=True)


@dataclass
class Signature:
    node: ast.FunctionDef
    kind: protocol.FunctionKind
    options: dict
    scope: AnnotationScope
    params: dict = field(default_factory=dict)
    result_type: object = protocol.MISSING
    reference: object = None


class Compiler:
    """Build from a copied original AST, retaining file and range information."""

    def __init__(self, source, env=None, filename=None):
        self.env = {"TypeVar": TypeVar, "tvm": tvm, **_NAMESPACES, **(env or {})}
        self.original = source
        members = vars(source).values() if inspect.isclass(source) else (source,)
        self.compile_flags = 0
        for member in members:
            code = getattr(member, "__code__", None)
            if code is not None:
                self.compile_flags |= code.co_flags & __future__.annotations.compiler_flag
        if isinstance(source, str):
            text = source
            self.filename = filename or "<str>"
            start, indent = 1, 0
            linecache.cache[self.filename] = (
                len(text),
                None,
                text.splitlines(keepends=True),
                self.filename,
            )
        else:
            lines, start = inspect.getsourcelines(source)
            text = "".join(lines)
            self.filename = filename or inspect.getsourcefile(source)
            indent = len(lines[0]) - len(lines[0].lstrip())
        self.tree = ast.parse(textwrap.dedent(text), self.filename)
        if start != 1:
            ast.increment_lineno(self.tree, start - 1)
        if indent:
            for node in ast.walk(self.tree):
                if hasattr(node, "col_offset"):
                    node.col_offset += indent
                    node.end_col_offset += indent
        self.used_names = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        self.used_names.update(n.arg for n in ast.walk(self.tree) if isinstance(n, ast.arg))
        self.used_names.update(
            n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)
        )
        self.used_names.update(self.env)
        self.counter = 0
        self.function_kinds = {}
        self.spans = []
        self.span_indices = {}
        self.source_name = ir.SourceName(self.filename)
        self.span_name = self.fresh("spans")
        self.builder_name = self.fresh("builder")
        self.infrastructure_name = self.fresh("infrastructure")

    def fresh(self, prefix):
        while True:
            self.counter += 1
            name = f"__script_{prefix}_{self.counter}"
            if name not in self.used_names:
                self.used_names.add(name)
                return name

    def span(self, node):
        key = (node.lineno, node.end_lineno, node.col_offset, node.end_col_offset)
        if key not in self.span_indices:
            self.span_indices[key] = len(self.spans)
            self.spans.append(ir.Span(self.source_name, *key))
        return self.spans[self.span_indices[key]]

    def span_ast(self, node):
        self.span(node)
        index = self.span_indices[
            (node.lineno, node.end_lineno, node.col_offset, node.end_col_offset)
        ]
        return ast.copy_location(
            ast.Subscript(ast.Name(self.span_name, ast.Load()), ast.Constant(index), ast.Load()),
            node,
        )

    def function_kind(self, node, env, *, allow_python=False):
        if id(node) in self.function_kinds:
            return self.function_kinds[id(node)]
        if inspect.isfunction(self.original) and node is self.tree.body[0]:
            kind = protocol.function_kind(self.original)
            if kind is not None:
                options = {
                    **kind.metadata.get("defaults", {}),
                    **getattr(self.original, "__tvm_function_options__", {}),
                }
                mapping = kind.metadata.get("option_map", {})
                result = (
                    kind,
                    {
                        mapping.get(key, key): value
                        for key, value in options.items()
                        if key != "check_well_formed"
                    },
                )
                self.function_kinds[id(node)] = result
                return result
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            value = _resolve(target, env, self.filename)
            kind = protocol.function_kind(value)
            if kind is not None:
                options = dict(kind.metadata.get("defaults", {}))
                if isinstance(decorator, ast.Call):
                    if decorator.args:
                        raise SyntaxError("Function decorators accept keyword options only")
                    for item in decorator.keywords:
                        if item.arg is None:
                            options.update(_resolve(item.value, env, self.filename))
                        else:
                            options[item.arg] = _resolve(item.value, env, self.filename)
                mapping = kind.metadata.get("option_map", {})
                options = {
                    mapping.get(key, key): value
                    for key, value in options.items()
                    if key != "check_well_formed"
                }
                self.function_kinds[id(node)] = (kind, options)
                return kind, options
        if allow_python:
            return protocol.FunctionKind(None, {"python": True}), {}
        raise SyntaxError(f"Function {node.name!r} has no registered construction kind")

    def declare(self, node, env, *, local=False):
        kind, options = self.function_kind(node, env)
        scope = AnnotationScope(env, kind.builder, self.filename, self.span)
        spec = Signature(node, kind, options, scope)
        if kind.metadata.get("python"):
            return spec
        X = kind.builder
        mode = {"local": True} if local else {}
        with X.decl_function(**options, **mode, span=self.span(node)) as frame:
            X.func_name(node.name)
            scope.prepare_type_params(getattr(node, "type_params", []))
            scope.prepare_parameters(node.args)
            if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
                raise SyntaxError("IR signatures require ordinary named parameters")
            for argument in node.args.args:
                if argument.annotation is None:
                    raise SyntaxError(f"Parameter {argument.arg!r} requires an annotation")
                annotation = scope.evaluate(argument.annotation)
                value = X.arg(
                    argument.arg,
                    scope.symbols.get(argument.arg, annotation),
                    span=self.span(argument),
                )
                spec.params[argument.arg] = scope.env[argument.arg] = value
            if node.returns is not None:
                spec.result_type = scope.evaluate(node.returns, introduce=False)
                X.func_ret_type(spec.result_type)
        spec.reference = frame.reference
        scope.env[node.name] = spec.reference
        return spec

    def define(self, spec, env, *, local=False):
        X = spec.kind.builder
        mode = {"local": True, "reference": spec.reference} if local else {}
        with X.function(**spec.options, **mode, span=self.span(spec.node)) as frame:
            X.func_name(spec.node.name)
            for name, value in spec.params.items():
                X.arg(name, value)
            if spec.result_type is not protocol.MISSING:
                X.func_ret_type(spec.result_type)
            scope_env = {**env, **spec.scope.env, **spec.params}
            self.run_statements(
                spec.node.body,
                X,
                scope_env,
                set(spec.params) | set(spec.scope.symbols),
                scope=spec.scope,
            )
        result = frame.function
        result.__name__ = spec.node.name
        return result

    def run_statements(self, body, builder, env, bound_names, *, scope=None, preserve_return=False):
        from .transform import Transformer

        namespace = dict(env)
        namespace.update(
            {
                self.builder_name: builder,
                self.infrastructure_name: protocol,
                self.span_name: self.spans,
            }
        )
        nested = {}
        nested_name = self.fresh("nested")

        def nested_statement(node):
            kind, _ = self.function_kind(node, namespace, allow_python=True)
            if kind.metadata.get("python"):
                return [copy.deepcopy(node)]
            index = len(nested)
            nested[index] = node
            value = ast.Call(
                ast.Name(nested_name, ast.Load()),
                [
                    ast.Constant(index),
                    ast.Call(
                        ast.Attribute(
                            ast.Name(self.infrastructure_name, ast.Load()), "locals", ast.Load()
                        ),
                        [],
                        [],
                    ),
                ],
                [],
            )
            return [ast.copy_location(ast.Assign([ast.Name(node.name, ast.Store())], value), node)]

        def build_nested(index, values):
            node = nested[index]
            local_env = {**namespace, **values}
            group = FunctionGroup(self, [node], local_env, local=True)
            return group.define(node.name)

        namespace[nested_name] = build_nested
        injected = {}

        def rewrite_expression(node):
            node = scope.rewrite(node) if scope is not None else copy.deepcopy(node)
            method = None
            values = []
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                method, values = "logical_not", [node.operand]
            if method is not None:
                node = ast.copy_location(
                    ast.Call(
                        ast.Attribute(ast.Name(self.builder_name, ast.Load()), method, ast.Load()),
                        values,
                        [],
                    ),
                    node,
                )
            return node

        def rewrite_iterable(node):
            node = copy.deepcopy(node)
            if isinstance(node, ast.Call):
                target = scope._resolve(node.func) if scope is not None else None
                try:
                    replacement = getattr(builder, "__tvm_call_overrides__", {}).get(target)
                except TypeError:
                    replacement = None
                if replacement is not None:
                    name = self.fresh("call")
                    injected[name] = replacement
                    node.func = ast.copy_location(ast.Name(name, ast.Load()), node.func)
            return node

        signature_values = {}
        if scope is not None:
            for name, value in scope.symbols.items():
                alias = self.fresh("symbol")
                namespace[alias] = value
                signature_values[name] = ast.Name(alias, ast.Load())

        transformer = Transformer(
            filename=self.filename,
            environment=namespace,
            builder_name=self.builder_name,
            infrastructure_name=self.infrastructure_name,
            span=self.span_ast,
            fresh=self.fresh,
            signature_names=set(bound_names),
            signature_values=signature_values,
            expression_rewriter=rewrite_expression,
            iterable_rewriter=rewrite_iterable,
            nested_function=nested_statement,
            preserve_return=preserve_return,
        )
        statements = transformer.transform_statements(copy.deepcopy(body))
        namespace.update(injected)
        if scope is not None:
            namespace.update(scope.env)
            bound_names = set(bound_names) | set(scope.symbols)
        names = sorted(name for name in bound_names if name in namespace)
        helper_name = self.fresh("body")
        helper = ast.FunctionDef(
            name=helper_name,
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(name) for name in names],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=statements or [ast.Pass()],
            decorator_list=[],
        )
        ast.copy_location(helper, body[0])
        module = ast.fix_missing_locations(ast.Module([helper], []))
        exec(
            compile(module, self.filename, "exec", flags=self.compile_flags, dont_inherit=True),
            namespace,
        )
        return namespace[helper_name](*(namespace[name] for name in names))

    def build(self):
        nodes = self.tree.body
        env = dict(self.env)
        if nodes and isinstance(nodes[-1], ast.FunctionDef | ast.ClassDef):
            prefix, nodes = nodes[:-1], nodes[-1:]
            if any(isinstance(node, ast.FunctionDef | ast.ClassDef) for node in prefix):
                raise SyntaxError("Source must contain one function or module class")
            if prefix:
                setup = ast.fix_missing_locations(ast.Module(copy.deepcopy(prefix), []))
                exec(compile(setup, self.filename, "exec"), env)
        if len(nodes) == 1 and isinstance(nodes[0], ast.ClassDef):
            root = nodes[0]
            statements = root.body
        elif len(nodes) == 1 and isinstance(nodes[0], ast.FunctionDef):
            root = None
            statements = nodes
        else:
            raise SyntaxError("Source must contain one function or module class")
        functions = [node for node in statements if isinstance(node, ast.FunctionDef)]
        python_functions = []
        with IRBuilder() as builder:
            with I.ir_module():
                references = {node.name: I.reserve_function(node.name) for node in functions}
                env.update(references)
                if root is not None:
                    env[root.name] = SimpleNamespace(**references)
                    for statement in statements:
                        if not isinstance(statement, ast.FunctionDef):
                            exec(
                                compile(
                                    ast.fix_missing_locations(
                                        ast.Module([copy.deepcopy(statement)], [])
                                    ),
                                    self.filename,
                                    "exec",
                                ),
                                env,
                            )
                            if isinstance(statement, ast.Assign | ast.AnnAssign):
                                targets = (
                                    statement.targets
                                    if isinstance(statement, ast.Assign)
                                    else [statement.target]
                                )
                                for target in targets:
                                    if isinstance(target, ast.Name):
                                        value = env[target.id]
                                        if isinstance(value, ir.BaseFunc):
                                            reference = I.decl_function(target.id, value)
                                            I.def_function(target.id, value)
                                            env[target.id] = reference
                                        setattr(env[root.name], target.id, env[target.id])
                ir_functions = []
                for node in functions:
                    if is_python_function(self, node, env):
                        original = self.env.get(node.name)
                        original = (
                            original
                            if getattr(original, "__tvm_python_function__", False)
                            else None
                        )
                        record = declare_python(self, node, env, original=original)
                        python_functions.append(record)
                        references[node.name] = env[node.name] = record.reference
                    else:
                        ir_functions.append(node)
                group = FunctionGroup(self, ir_functions, env)
                results = group.define_all()
            module = builder.get()
        if python_functions:
            attach_python(module, python_functions)
        if root is not None:
            module.__name__ = root.name
            original = self.original if inspect.isclass(self.original) else None
            bases = tuple(_resolve(base, env, self.filename) for base in root.bases)
            return adapt_python_module(module, original=original, bases=bases)
        return results[functions[0].name]


def parse(source, extra_vars=None, *, filename=None, **options):
    """Construct from source using only entry-module registered construction policies."""
    env = {} if isinstance(source, str) else _capture(source)
    env.update(extra_vars or {})
    compiler = Compiler(source, env, filename)
    try:
        return compiler.build()
    except DiagnosticError:
        raise
    except Exception as error:
        raise diagnostic_error(error, compiler) from error


def ir_module(module=None, **options):
    """Build a module after its registered function signatures have been declared."""

    def apply(module):
        return parse(module, _capture(module), **options)

    return apply(module) if module is not None else apply


from_source = parse
