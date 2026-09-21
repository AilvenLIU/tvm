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
"""Concrete Relax construction operations over the shared native builder stack."""

# pylint: disable=wildcard-import,redefined-builtin,invalid-name
import builtins as _python
import numbers as _numbers

import tvm_ffi as _ffi

from tvm import ir as _ir
from tvm import relax as _relax
from tvm import tirx as _tir
from tvm.relax.distributed import DeviceMesh as _DeviceMesh
from tvm.relax.distributed import DTensorType as _DTensorType
from tvm.relax.distributed import Placement as _Placement
from tvm.relax.distributed import device_mesh as device_mesh
from tvm.script.ir_builder import IRBuilder as _IRBuilder
from tvm.script.ir_builder import ir as _I
from tvm.script.ir_builder.base import MISSING as _MISSING
from tvm.script.ir_builder.base import _construction_span
from tvm.script.ir_builder.base import _frame_result as _named_frame_result
from tvm.script.ir_builder.base import at as _at
from tvm.script.ir_builder.base import source_span as _source_span
from tvm.script.ir_builder.type_var_frame import TypeVarDecl as _TypeVarDecl
from tvm.script.ir_builder.type_var_frame import TypeVarFrame as _TypeVarFrame
from tvm.script.ir_builder.type_var_frame import resolve_type_var
from tvm.script.parser_v2.protocol import expr_str_args as _expr_str_args

from . import _ffi_api
from . import distributed as dist
from . import frame as _frame
from . import ir as _native
from .distributed.ir import _lookup_device_mesh
from .ir import *  # noqa: F403


@_expr_str_args("shape", introduce=True, dtype="int64", scalar_strings=False)
def Tensor(shape=None, dtype=None, vdevice=None, ndim=-1, *, span=None):
    """Construct a tensor type from concrete shape dimensions.

    Parameters
    ----------
    shape : sequence, str, or None
        Concrete dimensions, None for unknown shape, or a bare dtype string when dtype is
        omitted.
    dtype : str or None
        Element dtype; None means unknown.
    vdevice : ir.VDevice, str, or None
        Concrete device or target[:index] lookup in the active module.
    ndim : int
        Rank when shape is unknown; -1 means unknown.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    relax.TensorType or ir.Type
        Concrete tensor type, or MissingType for unresolved eager annotation strings.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate. Shape dimensions follow the native Relax int64 requirement.
    Device-name lookup needs an active module. Marked unresolved strings/TypeVars outside a
    builder produce MissingType; active builders require concrete symbols.

    Examples
    --------
    >>> Tensor((2, 3), "float32")
    R.Tensor((2, 3), dtype="float32")
    """
    if isinstance(shape, _python.str) and dtype is None:
        dtype, shape = shape, None
    if isinstance(vdevice, _python.str):
        target, *qualifiers = vdevice.split(":", 2)
        vdevice = _I.lookup_vdevice(target, int(qualifiers[0]) if qualifiers else 0)
    return _relax.TensorType(shape, dtype, vdevice, ndim, _source_span(span))


@_expr_str_args("shape", introduce=True, dtype="int64", scalar_strings=False)
def DTensor(shape=None, dtype=None, device_mesh=None, placement="", *, ndim=-1, span=None):
    """Construct a distributed tensor type from concrete dimensions.

    Parameters
    ----------
    shape : sequence or None
        Concrete dimensions or unknown shape.
    dtype : str or None
        Element dtype or unknown dtype.
    device_mesh : DeviceMesh, str, or None
        Concrete mesh, module mesh name, or an empty default mesh.
    placement : Placement or str
        Placement object or text; empty text is the default.
    ndim : int
        Rank for unknown shape; -1 means unknown.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    DTensorType or ir.Type
        Distributed tensor type, or MissingType for unresolved eager strings.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate. Named device meshes require module context; concrete shape
    dimensions follow Tensor constraints.

    Examples
    --------
    >>> ty = DTensor((2, 3), "float32", mesh, "S[0]")
    """
    if device_mesh is None:
        device_mesh = _DeviceMesh([], _ir.Range(0, 1))
    elif isinstance(device_mesh, _python.str):
        device_mesh = _lookup_device_mesh(device_mesh)
    if isinstance(placement, _python.str):
        placement = _Placement.from_text(placement)
    return _DTensorType(Tensor(shape, dtype, ndim=ndim), device_mesh, placement, _source_span(span))


Range = _ir.Range


@_expr_str_args("values", introduce=True, dtype="int64")
def Shape(values=None, ndim=-1, *, span=None):
    """Construct a shape type from concrete dimensions.

    Parameters
    ----------
    values : sequence or None
        Concrete int64 dimension expressions, or None for unknown values.
    ndim : int
        Rank when values are unknown; -1 means unknown.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    relax.ShapeType or ir.Type
        Concrete shape type, or MissingType for unresolved eager strings.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate. Active handwritten builders require concrete dimension
    expressions.

    Examples
    --------
    >>> ty = Shape((2, 3))
    """
    return _relax.ShapeType(values, ndim, _source_span(span))


def _type(value):
    if value is None:
        return _ir.TupleType([])
    if callable(value):
        value = value()
    if _ir.is_prim_expr(value) or isinstance(value, _TypeVarDecl):
        value = value.ty
    if not isinstance(value, _ir.Type):
        raise TypeError(f"Expected a concrete type, got {type(value).__name__}")
    return value


def Callable(params=None, ret=None, purity=None, derive_func=None, *, span=None):
    """Construct a concrete or opaque Relax function type.

    Parameters
    ----------
    params : type, sequence, or None
        Parameter annotations. None selects an opaque callable.
    ret : object or None
        Return annotation; None denotes an empty tuple for explicit signatures.
    purity : bool or None
        Purity flag; None defaults true for explicit parameters, false for opaque signatures.
    derive_func : object or None
        Opaque-call return-type derivation function.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    relax.FuncType
        Function type with normalized concrete parameter and return types.

    Raises
    ------
    ValueError
        derive_func is supplied for a nonopaque callable.
    TypeError
        An annotation cannot be converted to a concrete type.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate.

    Examples
    --------
    >>> ty = Callable([Tensor((2,), "float32")], Tensor((2,), "float32"))
    """
    if purity is None:
        purity = params is not None
    if params is None:
        return _relax.FuncType.opaque_func(
            ret=None if ret is None else _type(ret),
            derive_func=derive_func,
            purity=purity,
            span=_source_span(span),
        )
    if derive_func is not None:
        raise ValueError("A derivation function requires an opaque callable")
    if not isinstance(params, list | _python.tuple):
        params = [params]
    return _relax.FuncType(
        [_type(param) for param in params], _type(ret), purity, _source_span(span)
    )


def Tuple(*fields, span=None):
    """Construct a tuple type while preserving missing component types.

    Parameters
    ----------
    fields : object
        Positional annotations or one list/tuple of annotations. Empty input creates an empty
        tuple type.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    ir.TupleType
        Tuple of normalized concrete or missing field types.

    Raises
    ------
    TypeError
        A field cannot be converted to an IR type.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate. Primitive expressions, declaration descriptors, and
    zero-argument annotation constructors are accepted. MissingType fields do not collapse the
    tuple.

    Examples
    --------
    >>> ty = Tuple(Prim("int32"), Object())
    """
    if len(fields) == 1 and isinstance(fields[0], list | _python.tuple):
        fields = fields[0]
    return _ir.TupleType([_type(field) for field in fields], _source_span(span))


def Prim(dtype, *, span=None):
    """Construct a primitive scalar type.

    Parameters
    ----------
    dtype : str or DataType
        Concrete scalar element dtype.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    ir.PrimType
        Primitive type.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate. span is accepted for the common builder interface; PrimType
    stores no source span.

    Examples
    --------
    >>> Prim("int32")
    int32
    """
    return _ir.PrimType(dtype)


Prim.__tvm_parameter_dtype__ = "dtype"


def Object(*, span=None):
    """Construct the unconstrained Relax value type.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    relax.AnyType
        Unconstrained value type with the requested source span.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate.

    Examples
    --------
    >>> ty = Object()
    """
    return _relax.AnyType(_source_span(span))


Any = Object


is_type_var = _ir.is_prim_var


def type_var(name, *, dtype=None, span=None):
    """Construct a fresh standalone primitive symbol.

    Parameters
    ----------
    name : str
        Source name of the new variable.
    dtype : str or None
        Primitive dtype; None retains the explicit-constructor default int64.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    ir.Var
        Fresh variable with the requested name and type.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Creates no construction frame and retains no per-function state. Native constructor
    validation errors propagate. This does not register a canonical symbol. resolve_type_var
    instead uses the active function frame and defaults newly introduced symbols to
    int64.

    Examples
    --------
    >>> n = type_var("n", dtype="int64")
    """
    return _ir.Var(name, "int64" if dtype is None else dtype, _source_span(span))


class _Frame:
    """Retain source metadata and exports around an existing native frame."""

    def __init__(self, native, span=None):
        # Each wrapper owns one native construction frame and source location.
        # result starts empty, records finalized lexical exports on exit, and
        # never outlives its construction region or stores another function's
        # symbols (those belong to the separate TypeVarFrame).
        self.native = native
        self.span = span
        self.result = {}

    def __getattr__(self, name):
        return getattr(self.native, name)

    @property
    def reference(self):
        """Return the stable module or local function reference after declaration."""
        if isinstance(self.native, _frame.FunctionFrame):
            local_var = self.native.local_var
            return local_var if local_var is not None else self.native.global_var
        raise AttributeError("This frame does not declare a function")

    def __enter__(self):
        with _construction_span(self.span):
            self.native.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with _construction_span(self.span):
            self.native.__exit__(exc_type, exc_value, traceback)
        if exc_type is None:
            if isinstance(self.native, _frame.BindingBlockFrame):
                self.result = {var.name: var for var in self.native.output_vars}
            elif (
                isinstance(self.native, _frame.FunctionFrame) and self.native.local_var is not None
            ):
                self.result = {self.native.name: self.native.local_var}
            elif isinstance(self.native, _frame.IfFrame):
                self.result = {self.native.var_name: self.native.var}
        return False


def frame_result(completed_frame, name):
    """Read one named export from an explicitly supplied completed region.

    Parameters
    ----------
    completed_frame : builder frame or dict
        Completed Relax context or explicit host branch export dictionary.
    name : str
        Original source binding name.

    Returns
    -------
    object
        Exported value, including None, or ir_builder.base.MISSING when absent.

    Raises
    ------
    TypeError
        name is not a string.

    Notes
    -----
    A conditional exports its native designated result; dataflow exports declared outputs; local
    functions export their reference. Other region-local names stay hidden. This is read-only
    and consults no ambient frame state; nested results remain independent.

    Examples
    --------
    >>> frame_result({"y": 3}, "y")
    3
    """
    return _named_frame_result(completed_frame, name)


def function(is_pure=True, is_private=False, *, local=False, reference=None, span=None):
    """Create a native Relax function definition context.

    Parameters
    ----------
    is_pure : bool
        Whether the function is pure; defaults True.
    is_private : bool
        Whether a module function is private; defaults False.
    local : bool
        Whether to define a nested function; defaults False.
    reference : ir.Var or None
        Previously declared local function reference; required when local is True.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    context manager
        Definition context retaining its native function and exports.

    Raises
    ------
    ValueError
        A local definition has no declared reference.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    The returned context wraps an existing native frame. Enter it under an active IRBuilder;
    exit finalizes its result and releases the native scope. No ambient completed-frame cache is
    used.

    Examples
    --------
    >>> with function():
    ...     return_(None)
    """
    if local:
        if reference is None:
            raise ValueError("A local function requires its declared reference")
        return _Frame(_ffi_api.LocalFunction(is_pure, reference), span)
    return _Frame(_native.function(is_pure, is_private), span)


def decl_function(is_pure=True, is_private=False, *, local=False, span=None):
    """Create a bodyless Relax function declaration context.

    Parameters
    ----------
    is_pure : bool
        Function purity; defaults True.
    is_private : bool
        Module-function privacy; defaults False.
    local : bool
        Whether to declare a nested function; defaults False.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    context manager
        Declaration context exposing its reference after successful exit.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    The returned context wraps an existing native frame. Enter it under an active IRBuilder;
    exit finalizes its result and releases the native scope. No ambient completed-frame cache is
    used. Signature operations match definition operations; the function body is supplied later.

    Examples
    --------
    >>> with decl_function() as declared:
    ...     x = arg("x", Object())
    ...     func_ret_type(Object())
    """
    return _Frame(_ffi_api.DeclFunction(is_pure, is_private, local), span)


def arg(name, ty, *, span=None):
    """Add a parameter while retaining a supplied variable identity.

    Parameters
    ----------
    name : str
        Original parameter name.
    ty : ir.Var or object
        Cached parameter variable or concrete annotation accepted by the type adapter.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    ir.Var
        Parameter registered on the active function frame.

    Raises
    ------
    TypeError
        The annotation cannot be converted to a type.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Mutates the active declaration/definition signature. A supplied variable is reused, not
    cloned; source instrumentation is temporary.

    Examples
    --------
    >>> x = arg("x", Tensor((2,), "float32"))
    """
    with _construction_span(span):
        if isinstance(ty, _ir.Var):
            return _ffi_api.ArgVar(name, ty)
        return _at(span, _native.arg(name, _type(ty)))


def func_ret_type(ret_ty):
    """Set the active function signature return type.

    Parameters
    ----------
    ret_ty : object
        Concrete annotation, primitive expression, or zero-argument constructor.

    Returns
    -------
    None
        The active frame retains the normalized return type.

    Raises
    ------
    TypeError
        The annotation cannot be converted to a type.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Requires an active declaration or definition. No additional frame is entered.

    Examples
    --------
    >>> func_ret_type(Object())
    """
    return _native.func_ret_type(_type(ret_ty))


func_ret_ty = func_ret_type


def dataflow(*, span=None):
    """Create a dataflow context with explicit finalized exports.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    context manager
        Region whose outputs are accessible through frame_result after exit.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    The returned context wraps an existing native frame. Enter it under an active IRBuilder;
    exit finalizes its result and releases the native scope. No ambient completed-frame cache is
    used. Native output operations determine which variables escape.

    Examples
    --------
    >>> with dataflow() as region:
    ...     y = bind_(value, name="y")
    ...     output(y)
    """
    return _Frame(_native.dataflow(), span)


def If(condition, *, span=None):
    """Create a conditional region with a designated named result.

    Parameters
    ----------
    condition : relax.Expr
        Scalar boolean condition accepted by the native Relax If builder.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    context manager
        Conditional context exposing its designated result after exit.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    The returned context wraps an existing native frame. Enter it under an active IRBuilder;
    exit finalizes its result and releases the native scope. No ambient completed-frame cache is
    used. Then and Else construct branch bodies; both must provide compatible designated
    outputs.

    Examples
    --------
    >>> with If(condition) as region:
    ...     with Then():
    ...         bind_(left, name="y")
    ...     with Else():
    ...         bind_(right, name="y")
    """
    return _Frame(_native.If(condition), span)


def Then(*, span=None):
    """Create the true branch of the active conditional.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    context manager
        Native true-branch construction context.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    The returned context wraps an existing native frame. Enter it under an active IRBuilder;
    exit finalizes its result and releases the native scope. No ambient completed-frame cache is
    used. Requires an enclosing If frame.

    Examples
    --------
    >>> with Then():
    ...     bind_(value, name="y")
    """
    return _Frame(_native.Then(), span)


def Else(*, span=None):
    """Create the false branch of the active conditional.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    context manager
        Native false-branch construction context.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    The returned context wraps an existing native frame. Enter it under an active IRBuilder;
    exit finalizes its result and releases the native scope. No ambient completed-frame cache is
    used. Requires an enclosing If frame.

    Examples
    --------
    >>> with Else():
    ...     bind_(value, name="y")
    """
    return _Frame(_native.Else(), span)


def _check_unterminated():
    for active_frame in reversed(_IRBuilder.current().frames):
        if isinstance(active_frame, _frame.FunctionFrame):
            if active_frame.output is not None:
                raise ValueError("A Relax operation cannot follow an unconditional return")
            break


def _value(value, ty=None):
    if isinstance(value, _python.tuple):
        return _relax.utils.convert_to_expr(value)
    if isinstance(value, _numbers.Number):
        if isinstance(ty, _ir.PrimType):
            return _relax.prim_value(value, dtype=ty.dtype)
        return _relax.const(value)
    return value


def bind_(
    value=_MISSING,
    *,
    ty=None,
    name=None,
    span=None,
    name_span=None,
    previous=_MISSING,
    declaration=False,
    frame_value=False,
):
    """Emit a Relax binding or retain a named frame-owned value.

    Parameters
    ----------
    value : object
        Expression, Python value, symbolic declaration descriptor, or MISSING.
    ty : object or None
        Optional annotation or zero-argument annotation constructor.
    name : str or None
        Optional source binding name.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.
    name_span : ir.Span, tuple, or None
        Binding-name range; None uses span.
    previous : object
        Previously resolved symbol for compatible declarations; defaults MISSING.
    declaration : bool
        Validate/reuse a primitive symbol declaration; defaults False.
    frame_value : bool
        Name a frame-owned value without emitting a binding; defaults False.

    Returns
    -------
    object
        Emitted variable, canonical symbol, or unchanged Python value.

    Raises
    ------
    ValueError
        Initializer is missing or an operation follows an unconditional return.
    TypeError
        A declaration or match-cast annotation is incompatible.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Expression inputs emit in the active native function/block. TypeVarDecl updates the
    nearest symbol frame. Existing native Emit rules fill MissingType on the original RHS
    before normalization and check concrete annotations. This operation adds no recursive
    annotation validation or propagation to tuple fields or call arguments.

    Examples
    --------
    >>> y = bind_(value, ty=Object(), name="y")
    """
    _check_unterminated()
    name_span = _source_span(span if name_span is None else name_span)
    if isinstance(value, _TypeVarDecl):
        return _TypeVarFrame.current().resolve(name, value.ty, span=name_span)
    if frame_value:
        if isinstance(value, _python.list | _python.tuple | _ir.Array):
            for index, item in enumerate(value):
                bind_(
                    item,
                    name=None if name is None else f"{name}_{index}",
                    span=_source_span(span),
                    name_span=name_span,
                    frame_value=True,
                )
        elif isinstance(value, _ir.Var):
            if name is not None:
                _IRBuilder.name(name, value)
            _at(name_span if name_span is not None else span, value)
        return value
    if declaration:
        if not _ir.is_prim_var(value):
            raise TypeError("A symbol declaration requires a concrete primitive variable")
        if ty is not None and not _ffi.structural_equal(_type(ty), value.ty):
            raise TypeError("The symbol declaration has an incompatible type")
        if previous is not _MISSING:
            if not _ir.is_prim_var(previous) or not _ffi.structural_equal(previous.ty, value.ty):
                raise TypeError("The symbol declaration has an incompatible signature dtype")
            return previous
        if name is not None:
            _IRBuilder.name(name, value)
        return _at(name_span if name_span is not None else span, value)
    if value is _MISSING:
        raise ValueError("Relax bindings require an initializer")
    if isinstance(value, _I.meta_var):
        return value.value
    ty = None if ty is None else _type(ty)
    value = _value(value, ty)
    with _construction_span(span):
        if isinstance(value, _relax.MatchCast):
            if ty is not None and not _ffi.structural_equal(ty, value.ty):
                raise TypeError("The binding annotation differs from the match-cast type")
            result = _ffi_api.EmitMatchCastV2(value.value, value.ty, name_span)
        elif isinstance(value, _relax.Expr):
            result = _ffi_api.EmitV2(value, ty, name_span)
        else:
            return value
    if name is not None:
        _IRBuilder.name(name, result)
    return _at(name_span if name_span is not None else span, result)


def emit_(value, *, span=None):
    """Emit a void expression statement.

    Parameters
    ----------
    value : relax.Expr or None
        Void expression, or None from an already emitted effect-only operation.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    None
        Emits the expression when needed.

    Raises
    ------
    TypeError
        The value is neither a Relax expression nor None.
    ValueError
        The emitted expression has a nonvoid type.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    None is a no-op. Other inputs create an underscore binding in the active function/block and
    must have empty tuple type.

    Examples
    --------
    >>> emit_(None)
    """
    if value is None:
        return
    if not isinstance(value, _relax.Expr):
        raise TypeError(f"Unsupported expression statement value: {type(value).__name__}")
    result = bind_(value, name="_", span=span)
    if not isinstance(result.ty, _ir.TupleType) or len(result.ty.fields) != 0:
        raise ValueError(
            "Non-void expressions must be bound to a variable; "
            f"expression of type {result.ty} was used as a statement"
        )


def return_(value=None, *, span=None):
    """Record a function result without exiting Python construction.

    Parameters
    ----------
    value : object or None
        Concrete result or convertible Python value; None denotes an empty tuple.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    None
        Updates the active native function result.

    Raises
    ------
    ValueError
        An unconditional return has already been recorded.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Subsequent ordinary Relax binding operations are rejected. This operation enters no new
    construction frame.

    Examples
    --------
    >>> return_(value)
    """
    _check_unterminated()
    with _construction_span(span):
        if value is None:
            value = _relax.Tuple([])
        _native.func_ret_value(_value(value))


def match_cast(value, ty, *, span=None):
    """Construct a match-cast descriptor for bind_ to consume.

    Parameters
    ----------
    value : object
        Concrete or convertible Relax value; None is rejected.
    ty : object
        Concrete target annotation.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    relax.MatchCast
        Unemitted match-cast with an anonymous result variable.

    Raises
    ------
    ValueError
        value is None.
    TypeError
        The annotation cannot be converted to a type.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Constructs the descriptor without emitting a binding or entering a frame. bind_ registers it
    in the active block.

    Examples
    --------
    >>> y = bind_(match_cast(value, Tensor((2,), "float32")), name="y")
    """
    if value is None:
        raise ValueError("The match-cast value cannot be None")
    ty = _type(ty)
    return _relax.MatchCast(_ir.Var("", ty), _value(value), ty, _source_span(span))


def unpack(value):
    """Project an IR tuple with known arity or preserve host iteration.

    Parameters
    ----------
    value : object
        Tuple literal, tuple-typed expression, or arbitrary host iterable.

    Returns
    -------
    object
        Python tuple of fields/projections, or the original value.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Creates projections only; no binding or construction frame is entered.

    Examples
    --------
    >>> unpack((1, 2))
    (1, 2)
    """
    if isinstance(value, _relax.Tuple):
        return _python.tuple(value.fields)
    if isinstance(value, _relax.Expr) and isinstance(value.ty, _ir.TupleType):
        return _python.tuple(_relax.TupleGetItem(value, i) for i in range(len(value.ty.fields)))
    return value


def assert_(condition, message="", *, span=None):
    """Emit a runtime assertion with construction-time diagnostic text.

    Parameters
    ----------
    condition : relax.Expr
        Runtime boolean condition.
    message : str
        Construction-time message; defaults to empty text.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    None
        Emits a void assertion operation in the active block.

    Raises
    ------
    TypeError
        message is not a string.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Uses existing native assertion and void-binding rules; enters no new construction frame.

    Examples
    --------
    >>> assert_(condition, "shape mismatch")
    """
    if not isinstance(message, _python.str):
        raise TypeError("An assertion message must be construction-time text")
    with _construction_span(span):
        emit_(_at(span, _native.assert_op(condition, format=message)), span=span)


def For(*args, span=None, **kwargs):
    """Reject imperative for loops in the Relax expression dialect.

    Parameters
    ----------
    args : object
        Rejected iteration arguments.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.
    kwargs : object
        Rejected iteration options.

    Returns
    -------
    None
        Never returns normally.

    Raises
    ------
    TypeError
        Relax does not support imperative for loops.

    Notes
    -----
    This rejection changes no builder state and enters no frame.

    Examples
    --------
    >>> # For(...) raises TypeError in Relax.
    """
    raise TypeError("Relax does not support imperative for loops")


def break_(*, span=None):
    """Reject break in the Relax expression dialect.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    None
        Never returns normally.

    Raises
    ------
    TypeError
        Relax does not support break.

    Notes
    -----
    This rejection changes no builder state and enters no frame.

    Examples
    --------
    >>> # break_(...) raises TypeError in Relax.
    """
    raise TypeError("Relax does not support break")


def continue_(*, span=None):
    """Reject continue in the Relax expression dialect.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    None
        Never returns normally.

    Raises
    ------
    TypeError
        Relax does not support continue.

    Notes
    -----
    This rejection changes no builder state and enters no frame.

    Examples
    --------
    >>> # continue_(...) raises TypeError in Relax.
    """
    raise TypeError("Relax does not support continue")


def setitem(target, index, value, *, span=None):
    """Reject indexed assignment in the Relax expression dialect.

    Parameters
    ----------
    target : object
        Rejected assignment target.
    index : object
        Rejected subscript.
    value : object
        Rejected assigned value.
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.

    Returns
    -------
    None
        Never returns normally.

    Raises
    ------
    TypeError
        Relax does not support indexed assignment.

    Notes
    -----
    This rejection changes no builder state and enters no frame.

    Examples
    --------
    >>> # setitem(...) raises TypeError in Relax.
    """
    raise TypeError("Relax does not support indexed assignment")


__all__ = [
    *_native.__all__,
    "Any",
    "Callable",
    "DTensor",
    "For",
    "for_",
    "frame_result",
    "resolve_type_var",
    "Object",
    "Prim",
    "Range",
    "Shape",
    "Tensor",
    "Tuple",
    "assert_",
    "break_",
    "continue_",
    "bind_",
    "decl_function",
    "device_mesh",
    "dist",
    "emit_",
    "is_type_var",
    "match_cast",
    "return_",
    "setitem",
    "type_var",
    "unpack",
]


def _logical_pair(lhs, rhs, operation, primitive, python_operation):
    if not isinstance(lhs, _ir.Expr) and not isinstance(rhs, _ir.Expr):
        return python_operation(lhs, rhs)
    if _ir.is_prim_expr(lhs) or _ir.is_prim_expr(rhs):
        return primitive(lhs, rhs)
    return operation(_value(lhs), _value(rhs))


def logical_and(*values):
    """Construct conjunction of concrete host, primitive, or tensor values.

    Parameters
    ----------
    values : object
        One or more host values, primitive boolean expressions, or boolean tensors.

    Returns
    -------
    object
        Host value, primitive And expression, or Relax logical_and call.

    Raises
    ------
    TypeError
        No operands are supplied.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    This combines eagerly constructed operands elementwise for tensors. It enters no frame and
    emits no binding. Invalid IR conditions may fail later during normalization.

    Examples
    --------
    >>> logical_and(True, False)
    """
    if not values:
        raise TypeError("logical_and requires at least one operand")
    result = values[0]
    for value in values[1:]:
        result = _logical_pair(result, value, _relax.op.logical_and, _tir.And, lambda a, b: a and b)
    return result


def logical_or(*values):
    """Construct disjunction of concrete host, primitive, or tensor values.

    Parameters
    ----------
    values : object
        One or more host values, primitive boolean expressions, or boolean tensors.

    Returns
    -------
    object
        Host value, primitive Or expression, or Relax logical_or call.

    Raises
    ------
    TypeError
        No operands are supplied.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    This combines eagerly constructed operands elementwise for tensors. It enters no frame and
    emits no binding. Invalid IR conditions may fail later during normalization.

    Examples
    --------
    >>> logical_or(False, True)
    """
    if not values:
        raise TypeError("logical_or requires at least one operand")
    result = values[0]
    for value in values[1:]:
        result = _logical_pair(result, value, _relax.op.logical_or, _tir.Or, lambda a, b: a or b)
    return result


def logical_not(value):
    """Negate a host or IR boolean without coercing IR to Python bool.

    Parameters
    ----------
    value : object
        Host value, primitive boolean expression, or boolean tensor.

    Returns
    -------
    bool or ir.Expr
        Host bool, primitive Not, or Relax logical_not call.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Enters no frame, emits no binding, and retains no state. Host truth-conversion errors
    propagate; invalid IR types may fail during normalization.

    Examples
    --------
    >>> logical_not(True)
    False
    """
    if _ir.is_prim_expr(value):
        return _tir.Not(value)
    if isinstance(value, _ir.Expr):
        return _relax.op.logical_not(value)
    return not value


def select(condition, true_value, false_value):
    """Construct elementwise selection or select already-built host values.

    Parameters
    ----------
    condition : object
        Host truth value, primitive boolean, or boolean tensor.
    true_value : object
        Already constructed value for true entries.
    false_value : object
        Already constructed value for false entries.

    Returns
    -------
    object
        Selected host value, primitive Select, or Relax where call.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    This elementwise operation makes no runtime short-circuit promise. It enters no frame and
    emits no binding. Native type errors may surface during normalization.

    Examples
    --------
    >>> select(True, 1, 2)
    1
    """
    if _ir.is_prim_expr(condition):
        return _tir.Select(condition, true_value, false_value)
    if isinstance(condition, _ir.Expr):
        return _relax.op.where(condition, _value(true_value), _value(false_value))
    return true_value if condition else false_value


__all__ += ["logical_and", "logical_not", "logical_or", "select"]


for_ = For


def if_then_else_(condition, true_value, false_value):
    """Construct scalar conditional evaluation from eagerly constructed operands.

    Parameters
    ----------
    condition : object
        Host truth value, primitive boolean, or scalar boolean Relax expression.
    true_value : object
        Already constructed true arm.
    false_value : object
        Already constructed false arm.

    Returns
    -------
    object
        Selected host value, primitive conditional intrinsic, or Relax If.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Python constructs both arms before calling this operation. The resulting IR selects its arm
    at runtime. Relax normalization keeps branch calls inside their branch. Arm types must be
    compatible; invalid conditions/types can fail during normalization. No outer bindings, new
    frames, or persistent state are created.

    Examples
    --------
    >>> if_then_else_(True, 1, 2)
    1
    """
    if isinstance(condition, _ffi.ObjectConvertible):
        condition = condition.asobject()
    if not isinstance(condition, _ir.Expr):
        return true_value if condition else false_value
    true_value = (
        true_value.asobject() if isinstance(true_value, _ffi.ObjectConvertible) else true_value
    )
    false_value = (
        false_value.asobject() if isinstance(false_value, _ffi.ObjectConvertible) else false_value
    )
    if _ir.is_prim_expr(condition) and all(
        _ir.is_prim_expr(value)
        if isinstance(value, _ir.Expr)
        else isinstance(value, _numbers.Number)
        for value in (true_value, false_value)
    ):
        return _tir.if_then_else(condition, true_value, false_value)
    return _relax.If(condition, _value(true_value), _value(false_value))


def _chain_binding(variable, value, body):
    if _ir.is_prim_expr(value) and _ir.is_prim_expr(body):
        return _tir.Let(variable, value, body)
    return _relax.SeqExpr([_relax.BindingBlock([_relax.VarBinding(variable, value)])], body)


def and_(*values, chain=None):
    """Construct scalar conjunction with runtime short-circuit evaluation.

    Parameters
    ----------
    values : object
        One or more host values, primitive booleans, or scalar boolean tensors.
    chain : tuple or None
        Optional original operands for a comparison chain. Must contain len(values)+1 entries;
        values must be their ordered comparisons.

    Returns
    -------
    object
        Host value, primitive expression, or Relax If/SeqExpr.

    Raises
    ------
    TypeError
        No operands are supplied.
    ValueError
        The chain operand count is invalid.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Python eagerly constructs every argument. chain retains shared operand identity and
    introduces progressively scoped IR bindings so each middle operand executes once, before its
    first comparison. Neither later operands nor conditional branches are emitted in the outer
    scope. No callbacks, persistent state, or new construction frames are used. Invalid scalar
    tensor conditions may fail during normalization.

    Examples
    --------
    >>> and_(True, False)
    False
    """
    if not values:
        raise TypeError("and_ requires at least one operand")
    if chain is not None:
        from tvm.tirx.script.builder.comparison import _comparison_chain

        return _comparison_chain(values, chain, and_, _chain_binding)
    result = values[-1]
    for value in reversed(values[:-1]):
        result = if_then_else_(
            value, result, False if isinstance(value, _ir.Expr | _ffi.ObjectConvertible) else value
        )
    return result


def or_(*values):
    """Construct scalar disjunction with runtime short-circuit evaluation.

    Parameters
    ----------
    values : object
        One or more host values, primitive boolean expressions, or boolean tensors.

    Returns
    -------
    object
        Host value, primitive conditional expression, or Relax If.

    Raises
    ------
    TypeError
        No operands are supplied.
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Python eagerly constructs all arguments. Compiled evaluation uses the right operand only
    when the left is false. Tensor conditions must be scalar booleans. No outer bindings, new
    frames, or persistent state are created. Invalid IR conditions may fail later during
    normalization.

    Examples
    --------
    >>> or_(False, True)
    """
    if not values:
        raise TypeError("or_ requires at least one operand")
    result = values[-1]
    for value in reversed(values[:-1]):
        result = if_then_else_(
            value, True if isinstance(value, _ir.Expr | _ffi.ObjectConvertible) else value, result
        )
    return result


def not_(value):
    """Negate a host or IR boolean without coercing IR to Python bool.

    Parameters
    ----------
    value : object
        Host value, primitive boolean expression, or boolean tensor.

    Returns
    -------
    bool or ir.Expr
        Host bool, primitive Not, or Relax logical_not call.

    Raises
    ------
    tvm.error.TVMError
        The active native frame rejects the operation or its concrete types.

    Notes
    -----
    Enters no frame, emits no binding, and retains no state. Host truth-conversion errors
    propagate; invalid IR types may fail during normalization.

    Examples
    --------
    >>> not_(True)
    False
    """
    return logical_not(value)


__all__ += ["and_", "if_then_else_", "not_", "or_"]
