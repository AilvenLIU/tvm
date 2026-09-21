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
"""Runtime owners for generated builder programs.

This module is deliberately on the builder side: concrete parameters, return
annotations, module references, symbol identities and source spans never enter
transpiler state. Generated Python calls these operations in source order.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import TypeVar

from tvm import ir

from . import ir as I
from .base import MISSING, IRBuilder, source_span
from .type_var_frame import TypeVarDecl, TypeVarFrame

# Process-wide owner-supplied callbacks, initialized unset and replaced only by
# explicit entry-module registration. Neither table contains per-parse state.
_OPAQUE_FACTORY = None
_MODULE_ADAPTER = None


def register_opaque_factory(factory, *, module_adapter=None):
    """Register construction and adaptation callbacks for Python module functions.

    Parameters
    ----------
    factory : callable
        Receives name, Python function, source text, and concrete span; returns a native module
        function.
    module_adapter : callable or None
        Optional callback receiving completed module, original class, and base tuple; returns
        the public module.

    Returns
    -------
    None
        Replaces the registered callbacks without executing function bodies.

    Raises
    ------
    TypeError
        A supplied callback is not callable.

    Notes
    -----
    Registration is process-wide and retains callbacks until replaced. No per-parse state is
    retained. None disables module adaptation.

    Examples
    --------
    >>> # Frontend initialization registers its concrete opaque-function factory.
    >>> register_opaque_factory(factory, module_adapter=adapter)
    """
    global _OPAQUE_FACTORY, _MODULE_ADAPTER
    if not callable(factory) or (module_adapter is not None and not callable(module_adapter)):
        raise TypeError("Module construction callbacks must be callable")
    _OPAQUE_FACTORY, _MODULE_ADAPTER = factory, module_adapter


class FunctionRecord:
    """Own one function's declaration and definition construction state.

    Parameters
    ----------
    builder : module
        Registered construction namespace exposing decl_function, function,
        func_name, arg and func_ret_type.
    name : str
        Original source function name.
    options : dict
        Evaluated keyword options accepted by this builder's function frames.
    location : tuple or None
        (SourceName or filename, line, end_line, column, end_column); columns are UTF-8 offsets.
    captures : dict or None
        Opaque lexical namespace captured by the generated factory. Existing
        concrete primitive symbols retain identity; other host values are ignored.
        The dictionary is consumed during initialization and is not retained.
    capture_names : tuple[str]
        Names mentioned by signature syntax; only these enclosing bindings may
        seed the symbol frame. Unrelated outer symbols never constrain locals.
    parameters : tuple[str]
        Source parameter names that shadow outer symbolic captures; default empty.
    local : bool
        True for a nested function; its builder must support local references.

    Notes
    -----
    builder/name/options/span/local are immutable construction configuration.
    symbols is one TypeVarFrame, re-entered for declaration and definition;
    its name-to-symbol dictionary is never shared with another function.
    params is an insertion-ordered name-to-concrete-parameter dict populated by
    parameter() only during declaration and reused without recreating identities.
    signature_symbols retains the names introduced by parameter annotations,
    excluding return-only free symbols; it is updated after each parameter.
    return_type starts MISSING and is set by returns(); reference/function start
    None and are populated after declaration/definition respectively. All state
    belongs to this generated-program invocation and dies with its result graph.

    Examples
    --------
    >>> record = FunctionRecord(builder, "main", {})
    >>> # Enter a ModuleProgram before declaring or defining the record.
    """

    def __init__(
        self,
        builder,
        name,
        options,
        location=None,
        *,
        local=False,
        captures=None,
        parameters=(),
        capture_names=(),
    ):
        self.builder, self.name, self.options = builder, name, dict(options)
        self.span, self.local = source_span(location), local
        self.symbols = TypeVarFrame()
        # Explicit lexical primitive symbols are captures, not new declarations.
        # Parameter names shadow them; all concrete inspection stays builder-side.
        for captured_name in capture_names:
            value = (captures or {}).get(captured_name)
            if (
                captured_name not in parameters
                and ir.is_prim_var(value)
                and value.name in ("", captured_name)
            ):
                self.symbols.bind(captured_name, value)
        self.params = {}
        self.signature_symbols = set(self.symbols.symbols)
        self.return_type = MISSING
        self.reference = self.function = None

    @contextmanager
    def declaration(self):
        """Enter the retained symbol frame and a native declaration frame.

        Yields
        ------
        FunctionRecord
            This record, ready for parameter and return-type operations.

        Raises
        ------
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        Requires an active module builder. Frames leave in reverse order on normal and
        exceptional exit. The finalized reference is retained only on success; symbols remain
        available for definition.

        Examples
        --------
        >>> with record.declaration():
        ...     record.parameter("x", builder.Prim("int32"))
        """
        mode = {"local": True} if self.local else {}
        with self.symbols:
            with self.builder.decl_function(**self.options, **mode, span=self.span) as frame:
                self.builder.func_name(self.name)
                yield self
        self.reference = frame.reference

    def parameter(self, name, annotation, location=None):
        """Construct and retain a parameter in the active declaration.

        Parameters
        ----------
        name : str
            Original source parameter name.
        annotation : object
            Evaluated type, primitive variable, declaration descriptor, or zero-argument
            annotation callable.
        location : ir.Span, tuple, or None
            Optional source range accepted by source_span; None creates no source metadata.

        Returns
        -------
        ir.Var
            Concrete parameter retained in source order.

        Raises
        ------
        TypeError
            A primitive declaration conflicts with a canonical symbol.
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        Primitive parameters reuse canonical symbol identity. Each successful parameter
        refreshes signature_symbols; params is reused during definition rather than
        reconstructing variables.

        Examples
        --------
        >>> with record.declaration():
        ...     x = record.parameter("x", builder.Prim("int32"))
        """
        if callable(annotation) and not isinstance(annotation, ir.Expr | ir.Type):
            annotation = annotation()
        if isinstance(annotation, TypeVarDecl):
            annotation = self.symbols.resolve(name, annotation.ty, span=source_span(location))
        if isinstance(annotation, ir.PrimType):
            annotation = self.symbols.resolve(name, annotation, span=source_span(location))
        elif ir.is_prim_var(annotation):
            annotation = self.symbols.bind(name, annotation)
        value = self.builder.arg(name, annotation, span=source_span(location))
        self.params[name] = value
        self.signature_symbols = set(self.symbols.symbols)
        return value

    def predeclare(self, name, annotation, location=None):
        """Reserve an explicit symbol type before dependent signature shapes.

        Parameters
        ----------
        name : str
            Original symbol name.
        annotation : object
            Primitive type, dtype spelling, declaration descriptor, variable, or zero-argument
            constructor.
        location : ir.Span, tuple, or None
            Optional source range accepted by source_span; None creates no source metadata.

        Returns
        -------
        ir.Var
            Canonical primitive symbol.

        Raises
        ------
        TypeError
            The annotation is not primitive or conflicts with a prior declaration.
        ValueError
            The symbol name is invalid.

        Notes
        -----
        Called during declaration. Registered symbols count as signature bindings, including
        explicit body declarations in parameterless functions; return-only symbols remain
        distinguishable.

        Examples
        --------
        >>> with record.declaration():
        ...     n = record.predeclare("n", "int64")
        """
        if callable(annotation) and not isinstance(annotation, ir.Expr | ir.Type):
            annotation = annotation()
        if isinstance(annotation, TypeVarDecl):
            annotation = annotation.ty
        if ir.is_prim_var(annotation):
            value = self.symbols.bind(name, annotation)
        else:
            value = self.symbols.resolve(name, annotation, span=source_span(location))
        # A body declaration is already an explicit binding, even in a function
        # with no parameters. Only symbols newly introduced by the return
        # annotation itself are unbound return-only symbols.
        self.signature_symbols = set(self.symbols.symbols)
        return value

    def symbol(self, name):
        """Read a declared symbol after signature construction.

        Parameters
        ----------
        name : str
            Source name already introduced by the signature.

        Returns
        -------
        ir.Var
            Retained canonical symbol.

        Raises
        ------
        KeyError
            The symbol was not declared.

        Notes
        -----
        This read-only operation does not require the retained frame to be active and does not
        create missing symbols.

        Examples
        --------
        >>> n = record.symbol("n")
        """
        return self.symbols.symbols[name]

    def capture(self, name, fallback):
        """Resolve a free annotation name against retained signature symbols.

        Parameters
        ----------
        name : str
            Source identifier.
        fallback : object
            Opaque enclosing lexical value.

        Returns
        -------
        object
            Existing canonical symbol, a symbol resolved from a host TypeVar,
            or any other fallback unchanged.

        Notes
        -----
        A host TypeVar resolves in this record's retained symbol frame before
        annotation arithmetic executes. Explicit predeclarations have already
        run, so they retain their requested dtype. No frame is entered and
        ordinary host values are neither converted nor cached.

        Examples
        --------
        >>> value = record.capture("n", outer_n)
        """
        if isinstance(fallback, TypeVar):
            return self.symbols.resolve(name)
        return self.symbols.symbols.get(name, fallback)

    def returns(self, annotation):
        """Set the return annotation on the record and active native frame.

        Parameters
        ----------
        annotation : object
            Evaluated concrete return annotation accepted by the dialect.

        Returns
        -------
        None
            Retains the annotation for definition.

        Raises
        ------
        ValueError
            The return annotation introduced a symbol not bound by the signature.
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        The same concrete annotation is reused in definition. Annotation evaluation itself has
        no exactly-once guarantee.

        Examples
        --------
        >>> with record.declaration():
        ...     record.returns(builder.Object())
        """
        introduced = set(self.symbols.symbols) - self.signature_symbols
        if introduced:
            raise ValueError(
                f"Return annotation introduces unbound symbol {sorted(introduced)[0]!r}"
            )
        self.return_type = annotation
        self.builder.func_ret_type(annotation)

    def define(self, body):
        """Run a generated body with retained parameters under a definition frame.

        Parameters
        ----------
        body : callable
            Receives parameters in source order; Python closures and defaults retain lexical
            captures.

        Returns
        -------
        ir.BaseFunc
            Constructed function, also retained as function on this record.

        Raises
        ------
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        Re-enters the same symbol frame and reuses parameter identities and return type.
        Requires an active module builder. Host callback exceptions propagate and scopes clean
        up normally.

        Examples
        --------
        >>> function = record.define(lambda x: builder.return_(x))
        """
        mode = {"local": True, "reference": self.reference} if self.local else {}
        with self.symbols:
            with self.builder.function(**self.options, **mode, span=self.span) as frame:
                self.builder.func_name(self.name)
                for name, value in self.params.items():
                    self.builder.arg(name, value)
                if self.return_type is not MISSING:
                    self.builder.func_ret_type(self.return_type)
                body(*self.params.values())
        self.function = frame.function
        self.function.__name__ = self.name
        return self.function


class ModuleProgram:
    """Own module construction while a generated builder program executes.

    Parameters
    ----------
    name : str or None
        Source module class name; None for a standalone function.
    original : type or None
        Optional original class for the registered module adapter.
    bases : tuple
        Evaluated source base classes; default empty.

    Notes
    -----
    builder and frame own the active native builder/module scopes from entry to
    exit. namespace is a fresh SimpleNamespace of declared references for source
    Class.member expressions. python_functions stores original host callables by
    source name until exit; result is None until successful native finalization.
    No state is global or shared between invocations.

    Examples
    --------
    >>> with ModuleProgram("Example") as program:
    ...     program.member("factor", 2)
    2
    """

    def __init__(self, name=None, original=None, bases=()):
        self.name, self.original, self.bases = name, original, bases
        self.builder = IRBuilder()
        self.frame = None
        self.namespace = SimpleNamespace()
        self.python_functions = {}
        self.result = None

    def __enter__(self):
        self.builder.__enter__()
        self.frame = I.ir_module()
        self.frame.__enter__()
        return self

    def __exit__(self, *error):
        try:
            self.frame.__exit__(*error)
            if error[0] is None:
                self.result = self.builder.get()
                if self.python_functions:
                    self.result.pyfuncs = self.python_functions
                if self.name is not None:
                    self.result.__name__ = self.name
                    if _MODULE_ADAPTER is not None:
                        self.result = _MODULE_ADAPTER(self.result, self.original, self.bases)
        finally:
            self.builder.__exit__(*error)

    def reserve(self, name):
        """Reserve and expose a stable named module function reference.

        Parameters
        ----------
        name : str
            Source function identifier.

        Returns
        -------
        ir.GlobalVar
            Native reference, also exposed on namespace.

        Raises
        ------
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        Requires this program to be entered. The native module validates duplicate or
        incompatible declarations.

        Examples
        --------
        >>> with ModuleProgram("Example") as program:
        ...     reference = program.reserve("main")
        """
        reference = I.reserve_function(name)
        setattr(self.namespace, name, reference)
        return reference

    def member(self, name, value):
        """Register a class assignment in the generated module namespace.

        Parameters
        ----------
        name : str
            Source member identifier.
        value : object
            Evaluated Python value or concrete IR function.

        Returns
        -------
        object
            Module function reference or the unchanged host value.

        Raises
        ------
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        IR functions are declared and defined in the active module; other values remain opaque
        namespace members.

        Examples
        --------
        >>> with ModuleProgram("Example") as program:
        ...     program.member("factor", 2)
        2
        """
        if isinstance(value, ir.BaseFunc):
            reference = I.decl_function(name, value)
            I.def_function(name, value)
            value = reference
        setattr(self.namespace, name, value)
        return value

    def python(self, name, function, source, location=None):
        """Register a host function without executing its body.

        Parameters
        ----------
        name : str
            Original source function name.
        function : callable
            Original Python function.
        source : str
            Original source text supplied to the registered factory.
        location : ir.Span, tuple, or None
            Optional source range accepted by source_span; None creates no source metadata.

        Returns
        -------
        ir.GlobalVar
            Opaque function reference, also exposed on namespace.

        Raises
        ------
        ValueError
            No opaque function factory has been registered.
        tvm.error.TVMError
            The active native frame rejects the operation or its concrete types.

        Notes
        -----
        Uses the process-registered factory inside the active module. Original host callables
        are retained by this program and transferred to the resulting module on successful exit.

        Examples
        --------
        >>> reference = program.python("helper", helper, source)
        """
        if _OPAQUE_FACTORY is None:
            raise ValueError("No opaque Python function constructor has been registered")
        opaque = _OPAQUE_FACTORY(name, function, source, source_span(location))
        reference = I.decl_function(name, opaque)
        I.def_function(name, opaque)
        self.python_functions[name] = function
        setattr(self.namespace, name, reference)
        return reference


def require_defined(value, name):
    """Return an exported value or report an undefined source name.

    Parameters
    ----------
    value : object
        Opaque lexical export, possibly MISSING.
    name : str
        Original source identifier for diagnostics.

    Returns
    -------
    object
        The value unchanged, including None.

    Raises
    ------
    NameError
        The value is MISSING.

    Notes
    -----
    This operation enters no frame and retains no lexical state.

    Examples
    --------
    >>> require_defined(None, "x") is None
    True
    """
    if value is MISSING:
        raise NameError(f"name {name!r} is not defined")
    return value


def is_python_bool(value):
    """Check for a host boolean without invoking truth conversion.

    Parameters
    ----------
    value : object
        Host value or builder expression.

    Returns
    -------
    bool
        True exactly for Python bool values.

    Notes
    -----
    Pure predicate used to select construction-time control flow without inspecting IR in
    generated code.

    Examples
    --------
    >>> is_python_bool(True)
    True
    >>> is_python_bool(1)
    False
    """
    return isinstance(value, bool)
