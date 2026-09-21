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
"""Annotation evaluation and callable-owned expression-argument syntax.

Constructors receive concrete values. This scope prepares designated symbols,
rewrites only registered expression fields, and evaluates each annotation once.
"""

import ast
import builtins
import copy
import inspect
import linecache
import re
from typing import TypeVar


class AnnotationScope:
    """A function's canonical symbols and once-evaluated annotation values."""

    def __init__(self, env, builder, filename, span):
        self.env = dict(env)
        self.builder = builder
        self.filename = filename
        self.span = span
        self.symbols = {}
        self._evaluated = {}
        self._prepared = {}
        self._pending_parameters = {}
        self._parameter_annotations = {}
        self._unbound_parameters = set()
        self._shape_declarations = {}
        self._type_vars = {}
        self._counter = 0
        self._used_names = set(env)

    def _error(self, node, message):
        raise SyntaxError(
            message,
            (
                self.filename,
                node.lineno,
                node.col_offset + 1,
                linecache.getline(self.filename, node.lineno),
            ),
        )

    def _symbol(self, name, node, dtype=None, *, shadow=False):
        if not shadow and name in self.symbols:
            return self.symbols[name]
        value = self.env.get(name)
        if not shadow and name in self.env and not isinstance(value, TypeVar):
            if getattr(self.builder, "is_type_var", lambda value: False)(value):
                self.symbols[name] = value
            return value
        value = self.builder.type_var(name, dtype=dtype, span=self.span(node))
        self.symbols[name] = self.env[name] = value
        return value

    def _canonical_type_var(self, name, node, dtype=None):
        value = self.env.get(name)
        if not isinstance(value, TypeVar):
            return
        if value.__constraints__ or value.__bound__ is not None:
            self._error(node, "A symbolic TypeVar cannot have constraints or a bound")
        if value.__name__ != name:
            self._error(node, "A symbolic TypeVar binding must match its declared name")
        if value not in self._type_vars:
            self._type_vars[value] = self._symbol(name, node, dtype)
        else:
            self.symbols[name] = self.env[name] = self._type_vars[value]

    def prepare_type_params(self, type_params):
        """Introduce explicit host-supported PEP 695 parameters, shadowing outer names."""
        type_var_node = getattr(ast, "TypeVar", ())
        for parameter in type_params:
            if not isinstance(parameter, type_var_node):
                self._error(parameter, "Only scalar TypeVar parameters are supported")
            bound = getattr(parameter, "bound", None)
            if bound is not None and not (
                isinstance(bound, ast.Name)
                and self.env.get(bound.id, getattr(builtins, bound.id, None)) is int
            ):
                self._error(parameter, "A symbolic TypeVar bound must be int")
            if getattr(parameter, "default_value", None) is not None:
                self._error(parameter, "A symbolic TypeVar cannot have a default")
            self._symbol(parameter.name, parameter, shadow=True)

    def _resolve(self, node):
        # Inspect callable identity without executing the annotation or its arguments.
        if isinstance(node, ast.Name):
            return self.env.get(node.id, getattr(builtins, node.id, None))
        if isinstance(node, ast.Attribute):
            owner = self._resolve(node.value)
            if owner is None:
                return None
            value = inspect.getattr_static(owner, node.attr, None)
            if isinstance(value, staticmethod):
                return value.__func__
            # Descriptor execution belongs to evaluation, not metadata discovery.
            return None if isinstance(value, property) else value
        return None

    @staticmethod
    def _arguments(call, constructor):
        try:
            parameters = list(inspect.signature(constructor).parameters.values())
        except (TypeError, ValueError):
            return {}
        positional = [
            p
            for p in parameters
            if p.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        fields = {
            parameter.name: value
            for parameter, value in zip(positional, call.args)
            if not isinstance(value, ast.Starred)
        }
        fields.update(
            {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
        )
        return fields

    def _cache_expression(self, node):
        name = f"__tvm_annotation_value_{self._counter}"
        self._counter += 1
        while name in self.env or name in self._used_names:
            name = f"__tvm_annotation_value_{self._counter}"
            self._counter += 1
        self.env[name] = self._eval(node)
        return ast.copy_location(ast.Name(name, ast.Load()), node)

    def prepare_parameters(self, arguments):
        """Prepare declared symbols and sequential scalar parameter annotations.

        Declaration constructors reserve identities before dependent annotations.
        Type constructors introduce their parameters in signature order. Cached
        dtype expressions are evaluated once. Bare symbolic strings declare shape
        names across the signature; compound expressions only reference them.
        """
        parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        self._used_names.update(
            node.id for node in ast.walk(arguments) if isinstance(node, ast.Name)
        )
        self._used_names.update(parameter.arg for parameter in parameters)
        for parameter in parameters:
            annotation = parameter.annotation
            if annotation is None:
                continue
            prepared = copy.deepcopy(annotation)
            if isinstance(prepared, ast.Constant) and isinstance(prepared.value, str):
                prepared = self._string_expression(prepared)
            self._prepared[id(annotation)] = prepared
            constructor = self._resolve(
                prepared.func if isinstance(prepared, ast.Call) else prepared
            )
            declaration = getattr(constructor, "__tvm_declaration_args__", None)
            dtype_field = getattr(constructor, "__tvm_parameter_dtype__", None)
            if declaration is not None:
                dtype = declaration.dtype
            elif dtype_field is not None and isinstance(prepared, ast.Call):
                dtype_node = self._arguments(prepared, constructor).get(dtype_field)
                if dtype_node is None:
                    dtype = inspect.signature(constructor).parameters[dtype_field].default
                    if dtype is inspect.Parameter.empty:
                        continue
                else:
                    cached = self._cache_expression(dtype_node)
                    dtype = self.env[cached.id]
                    for index, argument in enumerate(prepared.args):
                        if argument is dtype_node:
                            prepared.args[index] = cached
                    for keyword in prepared.keywords:
                        if keyword.value is dtype_node:
                            keyword.value = cached
            else:
                continue
            if declaration is not None:
                self._symbol(parameter.arg, parameter, dtype, shadow=True)
                self._parameter_annotations[id(annotation)] = parameter.arg
                self._unbound_parameters.add(parameter.arg)
            else:
                self._pending_parameters[id(annotation)] = (parameter, dtype)
        for prepared in self._prepared.values():
            self.rewrite(prepared, introduce=True, collect_declarations=True)
        return self.symbols

    def _eval(self, node):
        expression = ast.Expression(body=node)
        ast.fix_missing_locations(expression)
        return eval(compile(expression, self.filename, "eval"), self.env)  # pylint: disable=eval-used

    def evaluate(self, node, introduce=True):
        """Evaluate an annotation once in the prepared signature scope."""
        key = id(node)
        if key not in self._evaluated:
            self._unbound_parameters.discard(self._parameter_annotations.get(key))
            if key in self._pending_parameters:
                parameter, dtype = self._pending_parameters[key]
                if parameter.arg in self.symbols:
                    self._error(
                        parameter, "A later parameter cannot adopt an existing shape symbol"
                    )
                self._symbol(parameter.arg, parameter, dtype, shadow=True)
            prepared = self._prepared.get(key, node)
            # A quoted whole annotation is ordinary Python annotation syntax.
            if isinstance(prepared, ast.Constant) and isinstance(prepared.value, str):
                prepared = self._string_expression(prepared)
            self._evaluated[key] = self._eval(self.rewrite(prepared, introduce=introduce))
        return self._evaluated[key]

    def _string_expression(self, node):
        try:
            expression = ast.parse(node.value, mode="eval").body
        except SyntaxError as error:
            self._error(node, f"Invalid annotation expression: {error.msg}")
        source = "".join(linecache.getlines(self.filename))
        literal = ast.get_source_segment(source, node) if source else None
        positions = self._literal_positions(literal, node) if literal else None
        lines = node.value.splitlines(keepends=True)
        for inner in ast.walk(expression):
            if not hasattr(inner, "lineno"):
                continue
            for line_field, column_field in (
                ("lineno", "col_offset"),
                ("end_lineno", "end_col_offset"),
            ):
                line, column = getattr(inner, line_field), getattr(inner, column_field)
                offset = len("".join(lines[: line - 1]).encode("utf-8")) + column
                if positions is not None and offset in positions:
                    line, column = positions[offset]
                else:
                    column += node.col_offset + 1 if line == 1 else 0
                    line += node.lineno - 1
                setattr(inner, line_field, line)
                setattr(inner, column_field, column)
        return expression

    @staticmethod
    def _literal_positions(literal, node):
        """Map decoded expression byte offsets back through the literal's escapes."""
        match = re.match("(?i:([rub]*))([\"'])", literal)
        if match is None:
            return None
        prefix, quote = match.groups()
        width = 3 if literal[len(prefix) :].startswith(quote * 3) else 1
        start, stop = len(prefix) + width, len(literal) - width
        delimiter = quote * width
        positions, decoded, offset = {}, "", 0
        index = start

        def location(raw_index):
            before = literal[:raw_index]
            line = node.lineno + before.count("\n")
            column = len(before.rsplit("\n", 1)[-1].encode("utf-8"))
            return line, column + (node.col_offset if line == node.lineno else 0)

        while index < stop:
            end = index + 1
            if literal[index] == "\\" and "r" not in prefix.lower():
                escape = re.match(
                    r"\\(?:N\{[^}]*\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|\r?\n|.)",
                    literal[index:stop],
                )
                if escape:
                    end = index + len(escape.group())
            piece = literal[index:end]
            try:
                value = ast.literal_eval(prefix + delimiter + piece + delimiter)
            except (SyntaxError, ValueError):
                return None
            positions[offset] = location(index)
            for char in value:
                offset += len(char.encode("utf-8"))
                positions[offset] = location(end)
            decoded += value
            index = end
        return positions if decoded == node.value else None

    def rewrite(self, node, *, introduce=False, collect_declarations=False):
        """Return a copied expression AST, registering new symbols in ``env``.

        Construction code must execute with this scope's updated environment.
        No assignment to a generated local is needed for newly introduced names.
        """
        scope = self

        class Rewrite(ast.NodeTransformer):
            def __init__(self):
                self.allow_names = False
                self.in_string = False
                self.dtype = None

            def visit_Name(self, current):
                if collect_declarations:
                    return current
                if isinstance(current.ctx, ast.Load):
                    if not self.in_string and current.id in scope._unbound_parameters:
                        scope._error(current, f"Parameter {current.id!r} is not yet bound")
                    if (
                        self.allow_names
                        and not self.in_string
                        and current.id not in scope.env
                        and not hasattr(builtins, current.id)
                    ):
                        scope._error(current, f"Name {current.id!r} is not defined")
                    if isinstance(scope.env.get(current.id), TypeVar) and not introduce:
                        scope._error(
                            current, "A TypeVar must be introduced in a signature or match scope"
                        )
                    scope._canonical_type_var(current.id, current, self.dtype)
                    if self.allow_names and current.id in scope.env:
                        scope._symbol(current.id, current, self.dtype)
                        if self.in_string:
                            scope._unbound_parameters.discard(current.id)
                    elif (
                        self.allow_names
                        and self.in_string
                        and current.id in scope._shape_declarations
                    ):
                        declaration, dtype = scope._shape_declarations[current.id]
                        scope._symbol(current.id, declaration, dtype)
                return current

            def visit_Attribute(self, current):
                old_allow = self.allow_names
                self.allow_names = False
                current.value = self.visit(current.value)
                self.allow_names = old_allow
                return current

            def expression_field(self, current, metadata, *, nested=False):
                old_allow, old_dtype, old_string = self.allow_names, self.dtype, self.in_string
                self.allow_names = introduce and metadata.introduce
                self.dtype = metadata.dtype
                try:
                    if isinstance(current, ast.List | ast.Tuple):
                        current.elts = [
                            self.expression_field(value, metadata, nested=True)
                            for value in current.elts
                        ]
                        return current
                    if isinstance(current, ast.Constant) and isinstance(current.value, str):
                        if nested or metadata.scalar_strings:
                            current = scope._string_expression(current)
                            self.in_string = True
                            if self.allow_names and isinstance(current, ast.Name):
                                scope._shape_declarations.setdefault(
                                    current.id,
                                    (
                                        current,
                                        self.dtype
                                        if self.dtype is not None
                                        else metadata.implicit_dtype,
                                    ),
                                )
                    return self.visit(current)
                finally:
                    self.allow_names, self.dtype, self.in_string = old_allow, old_dtype, old_string

            def visit_Call(self, current):
                constructor = scope._resolve(current.func)
                metadata = getattr(constructor, "__tvm_expression_args__", None)
                fields = scope._arguments(current, constructor) if metadata else {}
                marked = (
                    {id(value) for name, value in fields.items() if name in metadata.fields}
                    if metadata
                    else set()
                )
                old_allow = self.allow_names
                self.allow_names = False
                current.func = self.visit(current.func)
                self.allow_names = old_allow
                current.args = [
                    self.expression_field(value, metadata)
                    if id(value) in marked
                    else self.visit(value)
                    for value in current.args
                ]
                for keyword in current.keywords:
                    keyword.value = (
                        self.expression_field(keyword.value, metadata)
                        if id(keyword.value) in marked
                        else self.visit(keyword.value)
                    )
                return current

        return ast.fix_missing_locations(Rewrite().visit(copy.deepcopy(node)))
