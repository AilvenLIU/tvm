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
import sys
import textwrap
from functools import wraps
from types import SimpleNamespace
from typing import TypeVar

from tvm.error import DiagnosticError
from tvm.ir import SourceName
from tvm.script.ir_builder import base, construction

from . import protocol as syntax_protocol
from .diagnostics import diagnostic_error
from .transpile import IRBuilderTranspiler, NameCollector

# Runtime callables are injected by identity; this namespace owns no IR state.
_EXECUTION = SimpleNamespace(
    _at=base.at,
    MISSING=base.MISSING,
    require_defined=construction.require_defined,
    is_python_bool=construction.is_python_bool,
    slice=slice,
    locals=locals,
    globals=globals,
)
_NAMESPACES = {}


def register_namespace(alias, namespace):
    """Register a host namespace for source-text entry points.

    Parameters
    ----------
    alias : str
        Python identifier used to refer to the namespace in source text.
    namespace : object
        Module or object bound to ``alias``.

    Returns
    -------
    None

    Notes
    -----
    Registration replaces the process-wide alias entry. Each new `Compiler`
    copies this table; existing compilations are unaffected. This operation
    enters no builder frame.
    """
    _NAMESPACES[alias] = namespace


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
    """Create and register a function decorator for a construction namespace.

    Parameters
    ----------
    builder : object
        Namespace implementing the function construction protocol.
    option_map : mapping of str to str, optional
        Public option names mapped to builder keyword names. Default is None,
        interpreted as an empty mapping; supplied entries are copied.
    defaults : mapping of str to object, optional
        Default builder keyword values. Default is None, interpreted as an
        empty mapping; supplied entries are copied.

    Returns
    -------
    decorator : callable
        Callable supporting ``@decorator``, ``@decorator(**options)``, and
        ``decorator(function)``.

    Raises
    ------
    ValueError
        When the returned decorator receives a non-function positional value.
    DiagnosticError
        When standalone construction fails during `parse` execution.

    Notes
    -----
    Class members retain their Python functions until module construction.
    Standalone functions immediately transpile and execute a builder program.
    Annotations must be safe to re-evaluate: eager MissingType placeholders
    are not cached, and source annotations execute in declaration frames.
    Registration persists for the lifetime of the returned decorator.
    """
    mapping, default_options = dict(option_map or {}), dict(defaults or {})

    def decorator(function=None, **options):
        if function is not None and not inspect.isfunction(function):
            raise ValueError("Construction decorators require a function or keyword options")

        def apply(function):
            function.__tvm_function_info__ = decorator.__tvm_function_info__
            function.__tvm_function_options__ = options
            if _inside_class(function):
                return function
            return parse(function, _capture(function))

        return apply(function) if function is not None else apply

    return syntax_protocol.register_function(
        decorator, builder, option_map=mapping, defaults=default_options
    )


def make_helper(builder, *, preserve_return=True, late_binding=False):
    """Create a decorator for helpers executed in a caller's builder frames.

    Parameters
    ----------
    builder : object
        Namespace implementing construction operations for the helper body.
    preserve_return : bool, optional
        Keep helper returns as ordinary Python control flow. Default is True.
    late_binding : bool, optional
        Refresh captured closure cells on each call. Default is False.

    Returns
    -------
    decorator : callable
        Accepts a function directly or keyword options. The ``hygienic``
        option defaults to True and snapshots the definition environment;
        False captures the calling environment on each invocation. Other
        options remain metadata for the namespace consumer.

    Raises
    ------
    ValueError
        When the returned decorator receives a non-function positional value.
    TypeError
        When a helper invocation cannot bind its Python signature.

    Notes
    -----
    Each invocation binds arguments and defaults, transpiles the original
    body, and returns its result. It shares the caller's active construction
    frames instead of declaring an IR function. Source acquisition,
    compilation, and builder exceptions propagate to the caller.
    """

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
    """Mark a Python function for opaque registration in a module.

    Parameters
    ----------
    function : callable
        Python function supporting attribute assignment.

    Returns
    -------
    callable
        The same function, with its registration marker attached.

    Raises
    ------
    AttributeError
        If the supplied object does not support the marker attribute.

    Notes
    -----
    The function body remains ordinary Python. Module construction delegates
    registration to the builder runtime. This decorator enters no frame and
    preserves callable identity for the function's lifetime.
    """
    function.__tvm_python_function__ = True
    return function


syntax_protocol.register_function(pyfunc, None, python=True)


class Compiler:
    """Acquire source and execute a location-preserving builder program.

    Parameters
    ----------
    source : str or function or type
        Source text, Python function, or Python class to compile.
    env : mapping of str to object, optional
        Opaque lexical bindings overriding registered source aliases.
        Default is None, interpreted as an empty mapping.
    filename : str, optional
        Override the original filename. Default is None, which uses the
        inspected filename for objects and ``"<str>"`` for source text.
    track_span : bool, optional
        Emit shared source metadata and IR location instrumentation.
        Default is True. False preserves Python AST locations and tracebacks.

    Raises
    ------
    OSError
        If inspection cannot recover the object's source.
    TypeError
        If the source object cannot be inspected.
    SyntaxError
        If the acquired text is not valid Python syntax.

    Notes
    -----
    ``env``, ``original``, and the fresh source ``tree`` belong to this
    compilation. ``filename`` and ``compile_flags`` retain the source's file
    and annotation mode. ``name_map`` holds reserved identifiers and prefix
    counters shared by all generated functions. ``builder_name`` and
    ``infrastructure_name`` are reserved aliases.

    With tracking enabled, one SourceName metadata object is held in ``env``
    under ``source_name_binding`` for the complete source unit. Disabling
    tracking skips that object and generated span instrumentation. No
    concrete IR construction state is retained; `build` passes its result
    opaquely to the caller.
    """

    def __init__(self, source, env=None, filename=None, *, track_span: bool = True):
        self.env = {"TypeVar": TypeVar, "tvm": sys.modules.get("tvm"), **_NAMESPACES, **(env or {})}
        self.original = source
        self.track_span = track_span
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
        # env is the opaque execution namespace; the transformer receives only
        # host namespace/callable bindings needed to identify registered metadata.
        # name_map is shared by every nested factory/body in this compilation unit.
        self.name_map = dict.fromkeys(self.env, 0)
        NameCollector(self.name_map).visit(self.tree)
        self.builder_name = self.fresh()
        self.infrastructure_name = self.fresh()
        # One source metadata object is shared by this unit and nested factories.
        # This narrow metadata exception never constructs Expr, Type or Span.
        # The hygienic binding cannot collide with source or captured names.
        self.source_name_binding = self.fresh() if track_span else None
        if track_span:
            self.env[self.source_name_binding] = SourceName(self.filename)

    def fresh(self, prefix="_t"):
        """Allocate a generated identifier without changing any source name.

        Parameters
        ----------
        prefix : str, optional
            Identifier prefix. Default is ``"_t"``.

        Returns
        -------
        str
            An unused generated identifier.

        Notes
        -----
        Updates this compilation's shared ``name_map``. Reserved names have
        value zero; prefix entries hold the next counter. The allocator remains
        shared across every function in the source unit.
        """
        counter = self.name_map.get(prefix, 0)
        while f"{prefix}{counter}" in self.name_map:
            counter += 1
        name = f"{prefix}{counter}"
        self.name_map[prefix] = counter + 1
        self.name_map[name] = 0
        return name

    def span_ast(self, node):
        """Emit location data for builders to materialize during execution.

        Parameters
        ----------
        node : ast.AST
            Original node carrying line, end-line, and UTF-8 column ranges.

        Returns
        -------
        ast.expr
            Tuple expression referencing the unit's shared SourceName binding,
            or a None constant when span tracking is disabled.

        Notes
        -----
        The generated expression inherits the full source range through
        ``ast.copy_location``. This method constructs no IR Span object.
        """
        if not self.track_span:
            return ast.copy_location(ast.Constant(None), node)
        return ast.copy_location(
            ast.Tuple(
                [
                    ast.Name(self.source_name_binding, ast.Load()),
                    *[
                        ast.Constant(value)
                        for value in (
                            node.lineno,
                            node.end_lineno,
                            node.col_offset,
                            node.end_col_offset,
                        )
                    ],
                ],
                ast.Load(),
            ),
            node,
        )

    def transformer(self, builder_name=None, **options):
        """Create a syntax transformer sharing this unit's name allocator.

        Parameters
        ----------
        builder_name : str, optional
            Injected builder alias. Default is None, which selects this unit's
            ``builder_name``.
        **options
            Additional `IRBuilderTranspiler` syntax-handler configuration.

        Returns
        -------
        IRBuilderTranspiler
            New transformer with local binding and export analysis.

        Raises
        ------
        TypeError
            If options are unsupported or duplicate internally supplied options.

        Notes
        -----
        Only host namespace and callable metadata are exposed to the transformer.
        The name allocator is shared with this compiler; no IR state is shared.
        """
        metadata = {
            name: value
            for name, value in self.env.items()
            if inspect.ismodule(value)
            or inspect.isfunction(value)
            or inspect.isclass(value)
            or type(value).__module__ == "types"
        }
        return IRBuilderTranspiler(
            self.filename,
            metadata,
            builder_name or self.builder_name,
            self.infrastructure_name,
            self.span_ast,
            None,
            name_map=self.name_map,
            track_span=self.track_span,
            **options,
        )

    def function_kind(self, node, env=None, *, allow_python=False):
        """Read registered function metadata and unevaluated option syntax.

        Parameters
        ----------
        node : ast.FunctionDef
            Original function definition with construction decorator syntax.
        env : mapping, optional
            Unused compatibility argument. Default is None.
        allow_python : bool, optional
            Permit unregistered ordinary Python helpers. Default is False.

        Returns
        -------
        info : FunctionDecoratorInfo
            Registered metadata or an ordinary-Python fallback when permitted.
        options : ast.Dict
            Unevaluated construction options.

        Raises
        ------
        SyntaxError
            If construction metadata is absent and Python helpers are disallowed,
            or a construction decorator supplies positional options.

        Notes
        -----
        This lookup evaluates neither annotations nor option expressions.
        """
        return self.transformer().function_metadata(node, allow_python=allow_python)

    def run_statements(self, body, builder, env, bound_names, *, preserve_return=False):
        """Compile and execute a helper body through AST-only lowering.

        Parameters
        ----------
        body : list of ast.stmt
            Nonempty original helper body.
        builder : object
            Registered construction namespace for the helper.
        env : mapping of str to object
            Opaque execution bindings, copied for this invocation.
        bound_names : iterable of str
            Names of supplied Python parameters.
        preserve_return : bool, optional
            Retain ordinary Python return semantics. Default is False.

        Returns
        -------
        object
            Opaque result of the generated helper.

        Raises
        ------
        SyntaxError
            If the helper body cannot be translated or compiled.

        Notes
        -----
        Execution shares the caller's active builder frames and this unit's name
        allocator. Source nodes are copied before translation. Builder and host
        execution exceptions propagate unchanged.
        """
        namespace = dict(env)
        namespace.update({self.builder_name: builder, self.infrastructure_name: _EXECUTION})
        transformer = self.transformer(signature_names=bound_names, preserve_return=preserve_return)
        statements = transformer.transform_statements(body)
        names = sorted(name for name in bound_names if name in namespace)
        helper_name = self.fresh()
        helper = ast.copy_location(
            ast.FunctionDef(
                helper_name,
                ast.arguments(
                    posonlyargs=[],
                    args=[ast.arg(name) for name in names],
                    kwonlyargs=[],
                    kw_defaults=[],
                    defaults=[],
                ),
                statements or [ast.Pass()],
                [],
                None,
            ),
            body[0],
        )
        if "type_params" in ast.FunctionDef._fields:
            helper.type_params = []
        module = ast.fix_missing_locations(ast.Module([helper], []))
        exec(
            compile(module, self.filename, "exec", flags=self.compile_flags, dont_inherit=True),
            namespace,
        )
        return namespace[helper_name](*(namespace[name] for name in names))

    def build(self):
        """Compile original-location builder AST and execute it.

        Returns
        -------
        object
            Opaque result produced by the builder program.

        Raises
        ------
        SyntaxError
            If the source cannot be lowered to supported construction syntax
            or the generated program cannot be compiled.

        Notes
        -----
        Imports establish host namespace bindings before metadata lookup. The
        remaining source expressions execute in the generated builder program;
        execution exceptions propagate to `parse` for diagnostic conversion.

        Compilation uses the original filename and AST coordinates, never
        unparse/reparse. Replaced nodes inherit all four location fields, while
        ``fix_missing_locations`` fills only absent fields. Python exceptions
        therefore retain the source file and original expression range; column
        detail requires Python 3.11 or later. Generated names and injected
        bindings remain local to this compiler and its execution namespace.
        """
        # Source-text imports establish host namespace bindings before static
        # metadata lookup. Only import statements execute here; annotations and
        # construction expressions remain exclusively in the generated program.
        imports = [
            copy.deepcopy(node)
            for node in self.tree.body[:-1]
            if isinstance(node, ast.Import | ast.ImportFrom)
        ]
        if imports:
            exec(
                compile(ast.Module(imports, []), self.filename, "exec", dont_inherit=True), self.env
            )
        if (
            inspect.isfunction(self.original)
            and syntax_protocol.function_info(self.original) is not None
        ):
            # Direct decorator application (T.prim_func(host_function)) has no
            # decorator in source AST. Inject only its registered host metadata;
            # already evaluated option values stay opaque execution bindings.
            decorator_name = self.fresh()
            self.env[decorator_name] = self.original
            keywords = []
            for key, value in getattr(self.original, "__tvm_function_options__", {}).items():
                option_name = self.fresh()
                self.env[option_name] = value
                keywords.append(ast.keyword(key, ast.Name(option_name, ast.Load())))
            root = self.tree.body[-1]
            root.decorator_list = [
                ast.copy_location(
                    ast.Call(ast.Name(decorator_name, ast.Load()), [], keywords), root
                )
            ]
        runtime, original = self.fresh(), self.fresh()
        bindings = {
            runtime: construction,
            original: self.original if inspect.isclass(self.original) else None,
            self.infrastructure_name: _EXECUTION,
        }
        transformed, result = self.transformer().program(self.tree, runtime, original, bindings)
        namespace = {**self.env, **bindings}
        exec(
            compile(
                transformed, self.filename, "exec", flags=self.compile_flags, dont_inherit=True
            ),
            namespace,
        )
        return namespace[result]


def parse(source, extra_vars=None, *, filename=None, track_span: bool = True, **options):
    """Transpile and execute a source string, Python function, or Python class.

    Parameters
    ----------
    source : str or function or type
        Original source text or inspectable Python object.
    extra_vars : mapping of str to object, optional
        Lexical bindings overriding captured values. Default is None,
        interpreted as an empty mapping.
    filename : str, optional
        Source filename override. Default is None, which uses the inspected
        filename for objects and ``"<str>"`` for text.
    track_span : bool, optional
        Enable shared source metadata and IR location instrumentation.
        Default is True. False retains Python source locations only.
    **options
        Accepted for entry-point compatibility; construction policy comes
        from registered source decorators, not these options.

    Returns
    -------
    object
        Opaque result of the generated builder program.

    Raises
    ------
    DiagnosticError
        If transpilation or host/builder execution fails. An existing
        DiagnosticError is preserved; other execution errors gain original
        source ranges.
    OSError
        If source inspection cannot recover the supplied object's text.
    TypeError
        If the source object cannot be inspected.
    SyntaxError
        If initial source parsing fails before program execution.

    Notes
    -----
    Each call owns a fresh compiler and lexical environment. Declaration and
    definition frames are entered only during generated execution. Source
    acquisition errors propagate directly, before diagnostic conversion.
    """
    env = {} if isinstance(source, str) else _capture(source)
    env.update(extra_vars or {})
    compiler = Compiler(source, env, filename, track_span=track_span)
    try:
        return compiler.build()
    except DiagnosticError:
        raise
    except Exception as error:
        raise diagnostic_error(error, compiler) from error


def ir_module(module=None, **options):
    """Decorate a Python class with two-phase module construction.

    Parameters
    ----------
    module : type, optional
        Class to compile immediately. Default is None, which returns a
        decorator awaiting a class.
    **options
        Keyword arguments forwarded to `parse`.

    Returns
    -------
    object or callable
        Generated module result when a class is supplied, otherwise a class
        decorator.

    Raises
    ------
    DiagnosticError
        If module transpilation or builder execution fails.

    Notes
    -----
    Class host bindings are captured before transpilation. Generated execution
    declares all registered signatures before defining their bodies. Source
    acquisition errors and frame lifetime follow `parse`.
    """

    def apply(module):
        return parse(module, _capture(module), **options)

    return apply(module) if module is not None else apply


from_source = parse
