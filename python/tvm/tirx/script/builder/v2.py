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
"""Concrete TIRx construction operations over the shared native IRBuilder stack."""

import builtins as _python
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from functools import partial as _partial
from functools import wraps as _wraps

import tvm_ffi as _ffi

from tvm import ir as _ir
from tvm import tirx as _tir
from tvm.script.ir_builder import IRBuilder as _IRBuilder
from tvm.script.ir_builder import ir as _I
from tvm.script.ir_builder.base import MISSING as _MISSING
from tvm.script.ir_builder.base import IRBuilderFrame as _NativeFrame
from tvm.script.ir_builder.base import _construction_span
from tvm.script.ir_builder.base import _frame_result as _named_frame_result
from tvm.script.ir_builder.base import at as _at
from tvm.script.ir_builder.base import source_span as _source_span
from tvm.script.ir_builder.type_var_frame import TypeVarDecl as _TypeVarDecl
from tvm.script.ir_builder.type_var_frame import TypeVarFrame as _TypeVarFrame
from tvm.script.ir_builder.type_var_frame import resolve_type_var as resolve_type_var
from tvm.script.parser_v2.protocol import expr_str_args as _expression_args
from tvm.script.parser_v2.protocol import register_type_var_decl as _register_type_var_decl
from tvm.tirx.lang.alloc_pool import SMEMPool as SMEMPool
from tvm.tirx.lang.alloc_pool import TMEMPool as TMEMPool

from . import _ffi_api
from . import frame as _frame
from . import ir as _native
from . import tirx as tile  # noqa: F401
from .ir import *  # noqa: F403
from .ir import boolean as bool  # pylint: disable=redefined-builtin
from .tirx import cluster as cluster
from .tirx import cta as cta
from .tirx import thread as thread
from .tirx import warp as warp
from .tirx import warpgroup as warpgroup
from .tirx import wg as wg
from .utils import buffer_proxy as buffer_proxy
from .utils import frame_scope as frame_scope
from .utils import seq_scope as seq_scope

is_type_var = _ir.is_prim_var


def type_var(name, *, dtype=None, span=None):
    """Construct an explicit standalone primitive symbol.

    Parameters
    ----------
    name : str
        Name of the new symbol.
    dtype : str, optional
        Primitive dtype; None uses the explicit-construction default int64.
    span : ir.Span or tuple, optional
        IR span or original-source location; None omits the span.

    Returns
    -------
    ir.Var
        Fresh primitive variable.

    Raises
    ------
    TypeError
        If the primitive dtype or source location is invalid.

    Notes
    -----
    This operation enters no frame and retains no symbol state. Expression
    strings use resolve_type_var, whose function-owned first-use default is i64.

    Examples
    --------
    >>> n = type_var("n", dtype="int32")
    """
    return _ir.Var(name, "int64" if dtype is None else dtype, _source_span(span))


@_expression_args(
    "shape",
    "strides",
    "elem_offset",
    "byte_offset",
    introduce=True,
    implicit_dtype="int32",
    compound_declarations=True,
    as_type=True,
)
def Buffer(
    shape,
    dtype="float32",
    data=None,
    strides=None,
    elem_offset=None,
    byte_offset=None,
    scope="global",
    align=0,
    offset_factor=0,
    layout="default",
    allocated_addr=None,
    buffer_name="",
    *,
    span=None,
):
    """Construct a concrete buffer annotation from resolved expressions.

    Parameters
    ----------
    shape : sequence of PrimExpr
        Buffer dimensions; expression strings are resolved by the transpiler.
    dtype : str, optional
        Element dtype, default "float32".
    data : ir.Var, optional
        Underlying pointer; None creates a fresh pointer.
    strides : sequence of PrimExpr, optional
        Element strides; None uses the native compact-layout default.
    elem_offset : PrimExpr, optional
        Element offset, using the native default when omitted.
    byte_offset : PrimExpr, optional
        Byte offset, using the native default when omitted.
    scope : str, optional
        Storage scope, default "global".
    align : int, optional
        Data alignment; zero requests the native default.
    offset_factor : int, optional
        Required offset divisibility; zero requests the native default.
    layout : object, optional
        Native layout specification, default "default".
    allocated_addr : sequence, optional
        Allocation metadata, absent by default.
    buffer_name : str, optional
        Buffer name, empty by default.
    span : ir.Span or tuple, optional
        IR span or original-source location; None omits the span.

    Returns
    -------
    ir.Var or ir.Type
        Concrete buffer variable, or a missing annotation type when an eager
        annotation still contains unresolved expression strings.

    Raises
    ------
    TypeError
        If unresolved strings are used in an active handwritten builder or the
        native constructor rejects the shape, layout or element type.

    Notes
    -----
    Only shape, strides and offset fields are expression fields. Names, dtype
    and scope remain literal strings. The call temporarily applies its span,
    but enters no persistent construction frame.

    Examples
    --------
    >>> annotation = Buffer((16, 32), "float32")
    """
    with _construction_span(span):
        return _at(
            span,
            _native.buffer(
                shape,
                dtype,
                data,
                strides,
                elem_offset,
                byte_offset,
                scope,
                align,
                offset_factor,
                layout,
                allocated_addr,
                buffer_name,
            ),
        )


buffer = Buffer


def Ptr(dtype, storage_scope="global", *, span=None):
    """Construct a pointer variable usable as a function annotation.

    Parameters
    ----------
    dtype : str, ir.PrimType or callable
        Element dtype or a zero-argument primitive type constructor.
    storage_scope : str, optional
        Pointer storage scope, default "global".
    span : ir.Span or tuple, optional
        Source location; None omits the span.

    Returns
    -------
    ir.Var
        Fresh pointer variable.

    Raises
    ------
    TypeError
        If the element type cannot describe a pointer.

    Notes
    -----
    The span applies only during construction; no frame or symbol is retained.

    Examples
    --------
    >>> pointer = Ptr("float32", "shared")
    """
    if callable(dtype) and not isinstance(dtype, _ir.Expr):
        dtype = dtype()
    if isinstance(dtype, _ir.Expr | _TypeVarDecl):
        dtype = dtype.ty
    if isinstance(dtype, _ir.PrimType):
        dtype = dtype.dtype
    with _construction_span(span):
        return _at(span, _native.ptr(dtype, storage_scope))


class _Frame:
    """Preserve a frame's source range through native finalization."""

    def __init__(self, native, span=None):
        # Each wrapper owns one native construction frame and source location.
        # result starts empty, records finalized lexical exports on exit, and
        # never outlives its construction region or stores another function's
        # symbols (those belong to the separate TypeVarFrame).
        self.native = native
        self.span = span
        self.result = {}

    def __enter__(self):
        with _construction_span(self.span):
            value = self.native.__enter__()
        return self if value is self.native else value

    def __exit__(self, *exc):
        with _construction_span(self.span):
            return self.native.__exit__(*exc)

    @property
    def reference(self):
        """Return the stable module reference after signature finalization."""
        return self.native.global_var

    def __getattr__(self, name):
        return getattr(self.native, name)


def frame_result(completed_frame, name):
    """Return a named export from an explicitly supplied completed region.

    Parameters
    ----------
    completed_frame : object or dict
        Completed builder context or host-branch result dictionary.
    name : str
        Original source variable name.

    Returns
    -------
    object
        Exported value, including None, or MISSING when no export exists.

    Raises
    ------
    TypeError
        If name is not a string.

    Notes
    -----
    TIR statement frames do not export their internal bindings. Host branch
    dictionaries expose explicitly supplied values. This operation changes no
    result maps, enters no frames and retains no completed-frame state.

    Examples
    --------
    >>> frame_result({"answer": 42}, "answer")
    42
    """
    return _named_frame_result(completed_frame, name)


def function(*, private=False, s_tir=False, persistent=False, span=None):
    """Create a native primitive-function definition frame.

    Parameters
    ----------
    private : bool, optional
        Omit public symbol export, default False.
    s_tir : bool, optional
        Construct S-TIR semantics, default False.
    persistent : bool, optional
        Enable persistent-function metadata, default False.
    span : ir.Span or tuple, optional
        Function source location; None omits the span.

    Returns
    -------
    _Frame
        Context manager over the native function body frame.

    Raises
    ------
    ValueError
        If the native builder rejects the enclosing construction context.

    Notes
    -----
    Entry pushes the native frame; exit finalizes it with the supplied span.
    The context retains only that frame, its source location and result mapping.

    Examples
    --------
    >>> # Inside an IRBuilder:
    >>> with function(private=True):
    ...     arg("n", int32)
    """
    with _construction_span(span):
        return _Frame(_native.prim_func(private=private, s_tir=s_tir, persistent=persistent), span)


def decl_function(*, private=False, s_tir=False, persistent=False, span=None):
    """Create a native primitive-function declaration frame.

    Parameters
    ----------
    private : bool, optional
        Omit public symbol export, default False.
    s_tir : bool, optional
        Construct S-TIR semantics, default False.
    persistent : bool, optional
        Enable persistent-function metadata, default False.
    span : ir.Span or tuple, optional
        Function source location; None omits the span.

    Returns
    -------
    _Frame
        Context manager over the native bodyless signature frame.

    Raises
    ------
    ValueError
        If the native builder rejects the enclosing construction context.

    Notes
    -----
    Entry pushes the native frame; exit finalizes it with the supplied span.
    The context retains only that frame, its source location and result mapping.

    Examples
    --------
    >>> # Inside an IRBuilder:
    >>> with decl_function(private=True):
    ...     arg("n", int32)
    """
    with _construction_span(span):
        return _Frame(_ffi_api.DeclFunction(private, s_tir, persistent), span)


def arg(name, annotation, *, span=None):
    """Register a concrete primitive-function parameter.

    Parameters
    ----------
    name : str
        Parameter name.
    annotation : ir.Type, ir.Expr, TypeVarDecl or callable
        Concrete annotation or zero-argument constructor.
    span : ir.Span or tuple, optional
        Parameter source location; None omits the span.

    Returns
    -------
    ir.Var
        Registered parameter shared by declaration and definition.

    Raises
    ------
    TypeError
        If the annotation is not supported by the native parameter builder.

    Notes
    -----
    Requires an active primitive-function frame and modifies that signature.
    S-TIR buffer annotations discard the TIRx layout when registering parameters.

    Examples
    --------
    >>> # Inside a function frame:
    >>> parameter = arg("n", int32)
    """
    if callable(annotation) and not isinstance(annotation, _ir.Expr):
        annotation = annotation()
    if isinstance(annotation, _TypeVarDecl):
        annotation = annotation.ty
    if isinstance(annotation, _ir.Type):
        annotation = _ir.Var(name, annotation)
    with _construction_span(span):
        if _tir.is_buffer_var(annotation) and annotation.ty.layout is not None:
            frames = _IRBuilder.current().frames
            if _python.any(
                isinstance(frame, _frame.PrimFuncFrame) and frame.s_tir for frame in frames
            ):
                ty = annotation.ty
                annotation = _native.buffer(
                    ty.shape,
                    ty.dtype,
                    strides=ty.strides,
                    elem_offset=ty.elem_offset,
                    scope=ty.storage_scope,
                    align=ty.data_alignment,
                    offset_factor=ty.offset_factor,
                    layout=None,
                    allocated_addr=list(ty.allocated_addr),
                    buffer_name=name,
                )
        return _native.arg(name, _at(span, annotation))


def func_ret_type(annotation, *, span=None):
    """Set the active primitive function's return type.

    Parameters
    ----------
    annotation : ir.Type, ir.Expr, TypeVarDecl or callable
        Concrete return type or zero-argument type constructor.
    span : ir.Span or tuple, optional
        Annotation source location; None omits the span.

    Returns
    -------
    None
        The active function signature is updated in place.

    Raises
    ------
    TypeError
        If the annotation cannot represent a native return type.

    Notes
    -----
    Requires an active primitive-function frame; no additional frame is retained.

    Examples
    --------
    >>> # Inside a function frame:
    >>> func_ret_type(int32)
    """
    if callable(annotation) and not isinstance(annotation, _ir.Expr):
        annotation = annotation()
    if isinstance(annotation, _ir.Expr | _TypeVarDecl):
        annotation = annotation.ty
    with _construction_span(span):
        return _native.func_ret(annotation)


def _name(value, name, span):
    if name is not None:
        _IRBuilder.name(name, value)
    return _at(span, value)


def _enter_concise(frame):
    native = frame.native if isinstance(frame, _Frame) else frame
    native.add_callback(_partial(frame.__exit__, None, None, None))
    return frame.__enter__()


def _as_expr(value):
    if isinstance(value, _ffi.ObjectConvertible):
        value = value.asobject()
    if isinstance(value, _ir.Expr):
        return value
    if isinstance(value, str):
        return _ir.StringImm(value)
    if isinstance(value, list | tuple):
        return _ir.Tuple([_as_expr(item) for item in value])
    return _tir.const(value)


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
    """Construct a named binding under the active primitive function's policy.

    Parameters
    ----------
    value : object, optional
        Concrete expression, native frame or TypeVarDecl. MISSING denotes an
        annotation-only binding.
    ty : ir.Type or callable, optional
        Concrete annotation or zero-argument type constructor; None omits it.
    name : str, optional
        Source binding name; None leaves the native name unchanged.
    span : ir.Span or tuple, optional
        Statement source location; None omits the span.
    name_span : ir.Span or tuple, optional
        Name source location; None uses span.
    previous : object, optional
        Prior binding for reassignment, or MISSING for a fresh binding.
    declaration : bool, optional
        Check and reuse an explicit primitive declaration, default False.
    frame_value : bool, optional
        Name a context-manager result without allocating storage, default False.

    Returns
    -------
    object
        Constructed binding, canonical symbol or unchanged Python value.

    Raises
    ------
    ValueError
        If an initializer is missing or a buffer, pointer or axis is rebound.
    TypeError
        If declaration types disagree or an annotation is unsupported.

    Notes
    -----
    Requires the relevant native function frame. Symbol declarations use the
    nearest TypeVarFrame and retain identity across declaration and definition.
    Scalar bindings may allocate storage and emit stores. Native frames may be
    entered with a callback that finalizes them with the enclosing region.

    Examples
    --------
    >>> # Inside a function and TypeVarFrame:
    >>> n = bind_(int32(), name="n")
    """
    name_span = span if name_span is None else name_span
    # Dtype constructors shared with the legacy builder return anonymous Vars.
    # Block axes and environment threads are already owned by native frames;
    # replacing their Vars would disconnect references from those registrations.
    # Other anonymous declarations share the function-owned type-variable frame.
    if ty is None and not frame_value and _ir.is_prim_var(value):
        if _python.any(
            (
                isinstance(frame, _frame.SBlockFrame)
                and _python.any(axis.var.same_as(value) for axis in frame.iter_vars)
            )
            or (
                isinstance(frame, _frame.PrimFuncFrame)
                and _python.any(thread.same_as(value) for thread in frame.env_threads)
            )
            for frame in _IRBuilder.current().frames
        ):
            return _name(value, name, name_span)
        if not value.name:
            return _TypeVarFrame.current().resolve(name, value.ty, span=name_span)
    if isinstance(value, _TypeVarDecl):
        return _TypeVarFrame.current().resolve(name, value.ty, span=name_span)
    with _construction_span(span):
        if frame_value:
            if isinstance(value, _frame.SBlockFrame):
                raise TypeError("A block does not introduce an as-target value")
            if isinstance(value, _python.list | _python.tuple | _ir.Array):
                for index, item in enumerate(value):
                    bind_(
                        item,
                        name=None if name is None else f"{name}_{index}",
                        span=span,
                        name_span=name_span,
                        frame_value=True,
                    )
            elif isinstance(value, _ir.Var | _tir.IterVar | _tir.Layout):
                _name(value, name, name_span)
            elif isinstance(value, _ir.TensorLoad) and _tir.is_buffer_var(value.source):
                _name(value.source, name, name_span)
            return value
        if previous is not _MISSING and _tir.is_buffer_var(previous):
            shape = previous.ty.shape
            if len(shape) == 1 and bool(shape[0] == 1):
                if value is _MISSING:
                    raise ValueError("A reassignment requires an initializer")
                _native.buffer_store(previous, value, [0])
                return previous
        if previous is not _MISSING and isinstance(getattr(previous, "ty", None), _ir.PointerType):
            raise ValueError(f"Pointer variable {name!r} cannot be reassigned")
        if previous is not _MISSING and (
            _tir.is_buffer_var(previous)
            or isinstance(previous, _tir.IterVar)
            or _python.any(
                isinstance(frame, _frame.SBlockFrame)
                and _python.any(axis.var.same_as(previous) for axis in frame.iter_vars)
                for frame in _IRBuilder.current().frames
            )
        ):
            raise ValueError(f"Cannot rebind buffer or block axis {name!r}")
        if declaration:
            if not _ir.is_prim_var(value):
                raise TypeError("A symbol declaration requires a concrete primitive variable")
            if ty is not None:
                annotation = ty() if callable(ty) else ty
                annotation = (
                    annotation.ty if isinstance(annotation, _ir.Expr | _TypeVarDecl) else annotation
                )
                if not _ffi.structural_equal(annotation, value.ty):
                    raise TypeError("The symbol declaration has an incompatible type")
            if previous is not _MISSING:
                if not _ir.is_prim_var(previous) or not _ffi.structural_equal(
                    previous.ty, value.ty
                ):
                    raise TypeError("The symbol declaration has an incompatible signature dtype")
                return previous
            return _name(value, name, name_span)
        if previous is not _MISSING and isinstance(previous, _ir.TensorLoad):
            if value is _MISSING:
                raise ValueError("A reassignment requires an initializer")
            _native.buffer_store(previous.source, value, list(previous.indices))
            return previous
        if isinstance(value, _I.meta_var):
            return value.value
        if isinstance(ty, _native.LocalVectorAnnotation):
            if value is not _MISSING:
                raise ValueError("Vector annotation does not support an initializer")
            return _name(_native.alloc_local(ty.shape, ty.dtype), name, name_span)
        if isinstance(ty, _native.LetAnnotation):
            if value is _MISSING:
                raise ValueError("An immutable binding requires an initializer")
            value = _as_expr(value)
            variable = _name(ty.as_var(rhs_dtype=value.ty), name, name_span)
            _native.Bind(value, var=variable)
            return variable
        if ty is not None:
            annotation = ty() if callable(ty) and not isinstance(ty, _ir.Expr) else ty
            annotation = (
                annotation.ty if isinstance(annotation, _ir.Expr | _TypeVarDecl) else annotation
            )
            if not isinstance(annotation, _ir.PrimType) or str(annotation) == "handle":
                raise TypeError("Mutable scalar annotations require a primitive scalar type")
            result = _native.local_scalar(str(annotation)).scalar
            _name(result.source, name, name_span)
            if value is not _MISSING:
                _native.buffer_store(result.source, value, [0])
            return result
        if value is _MISSING:
            raise ValueError("An uninitialized binding requires a scalar type annotation")
        if (
            isinstance(value, _ir.TensorLoad)
            and _tir.is_buffer_var(value.source)
            and not value.source.name
            and len(value.source.ty.shape) == 1
            and isinstance(value.source.ty.shape[0], _tir.IntImm)
            and value.source.ty.shape[0].value == 1
        ):
            _name(value.source, name, name_span)
            return value
        if isinstance(value, _native.scalar_wrapper):
            _name(value.scalar.source, name, name_span)
            return value.scalar
        if isinstance(value, _NativeFrame | _Frame):
            return _name(_enter_concise(value), name, name_span)
        if isinstance(value, list | tuple):
            for index, item in enumerate(value):
                bind_(item, name=None if name is None else f"{name}_{index}", span=span)
            return value
        if getattr(type(value), "_is_meta_class", False):
            if name is not None:
                _native.name_meta_class_value(name, value)
            return value
        if _tir.is_buffer_var(value) or isinstance(value, _tir.IterVar | _tir.Layout):
            return _name(value, name, name_span)
        if isinstance(value, _ir.Var) and not value.name:
            return _name(value, name, name_span)
        if isinstance(value, _ir.TensorRegion):
            return value
        if not isinstance(value, _ir.Expr | _python.int | _python.float | _python.bool | str):
            return value
        value = _as_expr(value)
        if _ir.is_prim_expr(value):
            result = _native.local_scalar(str(value.ty.dtype)).scalar
            _name(result.source, name, name_span)
            _native.buffer_store(result.source, value, [0])
            return result
        return _name(_native.Bind(value), name, name_span)


def emit_(value, *, span=None):
    """Consume an expression statement, including effect-only calls.

    Parameters
    ----------
    value : object
        Expression, native frame, scalar value, or None from an effect-only call.
    span : ir.Span or tuple, optional
        Statement source location; None omits the span.

    Returns
    -------
    None
        Effects are emitted into the active native statement frame.

    Raises
    ------
    TypeError
        If the native evaluator cannot represent the supplied value.

    Notes
    -----
    Strings, variables and None need no emitted evaluation. Concise native frames
    remain entered until their enclosing region finalizes its callbacks.

    Examples
    --------
    >>> # Inside a function frame:
    >>> emit_(0)
    """
    if value is None or isinstance(value, str | _ir.Var):
        return
    with _construction_span(span):
        if isinstance(value, _NativeFrame | _Frame):
            _enter_concise(value)
        elif hasattr(value, "frames"):
            for frame in value.frames:
                _enter_concise(frame)
        elif isinstance(value, _tir.BufferStore):
            _native.buffer_store(value.buffer, value.value, value.indices)
        else:
            _native.evaluate(value)


def setitem(target, key, value, *, span=None):
    """Emit an indexed store using already evaluated operands.

    Parameters
    ----------
    target : ir.Var
        Destination buffer.
    key : PrimExpr or sequence of PrimExpr
        Buffer indices.
    value : PrimExpr
        Value to store.
    span : ir.Span or tuple, optional
        Statement source location; None omits the span.

    Returns
    -------
    None
        A store is appended to the active statement frame.

    Raises
    ------
    TypeError
        If the target, indices or value are invalid for the native store.

    Notes
    -----
    Requires an active statement frame and retains no additional Python state.

    Examples
    --------
    >>> # Inside a function frame with buffer A:
    >>> setitem(A, [0], 1)
    """
    with _construction_span(span):
        _native.buffer_store(target, value, key)


def setattr(target, name, value, *, span=None):
    """Store through a scalar attribute or update Python metadata.

    Parameters
    ----------
    target : object
        Object containing the attribute.
    name : str
        Attribute name.
    value : object
        Replacement value, optionally wrapped by meta_var.
    span : ir.Span or tuple, optional
        Store source location; None omits the span.

    Returns
    -------
    None
        The attribute or its existing scalar storage is updated.

    Raises
    ------
    AttributeError
        If Python attribute assignment is unsupported.
    TypeError
        If a native scalar store receives an incompatible value.

    Notes
    -----
    Native scalar stores require an active statement frame. Ordinary metadata
    updates follow Python attribute semantics and retain no builder state.

    Examples
    --------
    >>> # Update an ordinary metadata object:
    >>> setattr(metadata, "count", 4)
    """
    if isinstance(value, _I.meta_var):
        _python.setattr(target, name, value.value)
        return
    previous = getattr(target, name, _MISSING)
    if isinstance(previous, _native.scalar_wrapper):
        previous = previous.scalar
    buffer = previous.source if isinstance(previous, _ir.TensorLoad) else previous
    if _tir.is_buffer_var(buffer):
        shape = buffer.ty.shape
        if len(shape) == 1 and bool(shape[0] == 1):
            bind_(value, previous=previous, span=span)
            return
    _python.setattr(target, name, value)


def return_(value=None, *, span=None):
    """Emit a primitive-function return expression.

    Parameters
    ----------
    value : PrimExpr, optional
        Return expression; the default None is rejected.
    span : ir.Span or tuple, optional
        Statement source location; None omits the span.

    Returns
    -------
    None
        A return statement is appended to the active function body.

    Raises
    ------
    TypeError
        If value is None or cannot be converted to a native expression.

    Notes
    -----
    Requires an active primitive-function frame. Subsequent unreachable source
    statements remain in the constructed IR; this operation does not exit Python.

    Examples
    --------
    >>> # Inside a function frame:
    >>> return_(0)
    """
    if value is None:
        raise TypeError("A primitive function return requires an expression")
    with _construction_span(span):
        _native.Return(_as_expr(value))


def _require_loop():
    for frame in reversed(_IRBuilder.current().frames):
        if isinstance(frame, _frame.ForFrame | _frame.WhileFrame):
            return
        if isinstance(frame, _frame.PrimFuncFrame):
            break
    raise ValueError("Loop control requires an enclosing primitive loop")


def break_(*, span=None):
    """Emit a break targeting the nearest primitive loop.

    Parameters
    ----------
    span : ir.Span or tuple, optional
        Statement source location; None omits the span.

    Returns
    -------
    None
        Loop control is appended to the active statement frame.

    Raises
    ------
    ValueError
        If no loop exists inside the current primitive function.

    Notes
    -----
    Only the native frame stack determines the target. No additional state or
    frame is retained, and Python construction continues after this call.

    Examples
    --------
    >>> # Inside a primitive loop:
    >>> break_()
    """
    _require_loop()
    with _construction_span(span):
        _native.evaluate(_native.break_loop())


def continue_(*, span=None):
    """Emit a continue targeting the nearest primitive loop.

    Parameters
    ----------
    span : ir.Span or tuple, optional
        Statement source location; None omits the span.

    Returns
    -------
    None
        Loop control is appended to the active statement frame.

    Raises
    ------
    ValueError
        If no loop exists inside the current primitive function.

    Notes
    -----
    Only the native frame stack determines the target. No additional state or
    frame is retained, and Python construction continues after this call.

    Examples
    --------
    >>> # Inside a primitive loop:
    >>> continue_()
    """
    _require_loop()
    with _construction_span(span):
        _native.evaluate(_native.continue_loop())


def assert_(condition, message="", *, span=None):
    """Emit a flat native assertion with its source location.

    Parameters
    ----------
    condition : PrimExpr
        Boolean assertion condition.
    message : str, sequence or tuple, optional
        Message, message parts, or (error_kind, message_parts). The default is
        an empty message with RuntimeError as its error kind.
    span : ir.Span or tuple, optional
        Assertion source location; None omits the span.

    Returns
    -------
    None
        An assertion is appended to the active statement frame.

    Raises
    ------
    TypeError
        If error metadata is malformed or the condition is invalid.

    Notes
    -----
    Requires an active statement frame. The assertion's temporary native frame
    is finalized before this operation returns.

    Examples
    --------
    >>> # Inside a function frame:
    >>> assert_(n > 0, "n must be positive")
    """
    kind = "RuntimeError"
    if isinstance(message, tuple):
        if len(message) != 2 or not isinstance(message[0], str):
            raise TypeError("Assertion metadata must be (error_kind, message_parts)")
        kind, message = message
    if isinstance(message, list | tuple):
        message = [str(part) for part in message]
    if not isinstance(message, list | tuple):
        message = [message]
    with _construction_span(span):
        with _native.Assert(condition, message, error_kind=kind):
            pass


def If(condition, *, span=None):
    """Create a native if statement region.

    Parameters
    ----------
    condition : PrimExpr
        Scalar boolean condition.
    span : ir.Span or tuple, optional
        Region source location; None omits the span.

    Returns
    -------
    _Frame
        Context manager for the native if region.

    Raises
    ------
    ValueError
        If the enclosing native region is invalid for this frame.

    Notes
    -----
    Entry pushes the native region and exit finalizes it. Internal bindings are
    lexical to the region and are not implicitly exported.

    Examples
    --------
    >>> # Inside the appropriate native statement context:
    >>> with If(condition):
    ...     emit_(0)
    """
    with _construction_span(span):
        return _Frame(_native.If(condition), span)


def Then(*, span=None):
    """Create a native then statement region.

    Parameters
    ----------
    span : ir.Span or tuple, optional
        Region source location; None omits the span.

    Returns
    -------
    _Frame
        Context manager for the native then region.

    Raises
    ------
    ValueError
        If the enclosing native region is invalid for this frame.

    Notes
    -----
    Entry pushes the native region and exit finalizes it. Internal bindings are
    lexical to the region and are not implicitly exported.

    Examples
    --------
    >>> # Inside the appropriate native statement context:
    >>> with Then():
    ...     emit_(0)
    """
    with _construction_span(span):
        return _Frame(_native.Then(), span)


def Else(*, span=None):
    """Create a native else statement region.

    Parameters
    ----------
    span : ir.Span or tuple, optional
        Region source location; None omits the span.

    Returns
    -------
    _Frame
        Context manager for the native else region.

    Raises
    ------
    ValueError
        If the enclosing native region is invalid for this frame.

    Notes
    -----
    Entry pushes the native region and exit finalizes it. Internal bindings are
    lexical to the region and are not implicitly exported.

    Examples
    --------
    >>> # Inside the appropriate native statement context:
    >>> with Else():
    ...     emit_(0)
    """
    with _construction_span(span):
        return _Frame(_native.Else(), span)


@_dataclass(frozen=True)
class _IterationSpec:
    """Loop description; native frame construction belongs to for_.

    kind is 'range' or 'grid'; arguments holds concrete bounds/extents, and dtype
    is the optional grid dtype. These fields are immutable and may be consumed
    repeatedly into distinct frames. _context starts/reset to None and holds
    only the delegated for_ context during a direct handwritten ``with spec``.
    It is owned by this descriptor, excluded from equality/repr, and cleared on
    every exit. It adds no frame stack: entry/exit use the existing native stack.
    Direct nested reuse of one descriptor is rejected; sequential reuse is safe.
    """

    kind: str
    arguments: tuple
    dtype: str | None = None
    _context: object = _field(default=None, init=False, repr=False, compare=False)

    def __enter__(self):
        if self._context is not None:
            raise ValueError("An iteration descriptor is already entered")
        context = for_(self)
        _python.object.__setattr__(self, "_context", context)
        try:
            return context.__enter__()
        except BaseException:
            _python.object.__setattr__(self, "_context", None)
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if self._context is None:
            raise ValueError("An iteration descriptor is not entered")
        try:
            return self._context.__exit__(exc_type, exc_value, traceback)
        finally:
            _python.object.__setattr__(self, "_context", None)


def grid(*extents, dtype=None):
    """Describe Cartesian iteration without constructing native loop frames.

    Parameters
    ----------
    *extents : int, PrimExpr or tuple
        Dimension extents or (start, extent) pairs.
    dtype : str, optional
        Loop-variable dtype; None infers it from each extent.

    Returns
    -------
    _IterationSpec
        Reusable descriptor consumed by for_ or directly as a context manager.

    Raises
    ------
    ValueError
        If the same descriptor is directly entered recursively.

    Notes
    -----
    Native bound validation occurs when consumed. Sequential reuse constructs
    fresh native frames. A direct context yields the native variable sequence
    and finalizes the same frames on exit. Handwritten bounds must be concrete.

    Examples
    --------
    >>> loops = grid(16, 32)
    """
    return _IterationSpec("grid", extents, dtype)


class _LoopFrame(_Frame):
    """One loop frame plus optional source target names, consumed at entry.

    names is None, one source identifier, or an immutable tuple of identifiers
    with an optional '*name' unpack marker, owned by this loop wrapper.
    It never enters symbol state: loop variables are lexical iteration values,
    not function-wide TypeVars. Native variables are named once on entry.
    """

    def __init__(self, native, names, span):
        super().__init__(native, span)
        self.names = names

    def __enter__(self):
        values = super().__enter__()
        if self.names is not None:
            variables = values if isinstance(values, list | tuple | _ir.Array) else [values]
            if isinstance(self.names, str):
                names = (
                    [self.names]
                    if len(variables) == 1
                    else [f"{self.names}_{index}" for index in range(len(variables))]
                )
            else:
                names = list(self.names)
                stars = [index for index, name in enumerate(names) if name.startswith("*")]
                if stars:
                    index = stars[0]
                    count = len(variables) - len(names) + 1
                    if count < 0:
                        raise ValueError("Loop target count differs from iteration dimensions")
                    prefix = names[index][1:]
                    names[index : index + 1] = [f"{prefix}_{item}" for item in range(count)]
                if len(variables) != len(names):
                    raise ValueError("Loop target count differs from iteration dimensions")
                # Python's original tuple target still performs unpacking; even
                # a one-dimensional grid must return a one-element sequence.
                values = variables
            for name, value in zip(names, variables):
                _IRBuilder.name(name, value)
        return values


def for_(iterable, *, names=None, span=None):
    """Create native loop scope from a concrete iteration specification.

    Parameters
    ----------
    iterable : _IterationSpec, ForFrame or range
        Grid/range descriptor, native loop frame or Python range.
    names : str or tuple of str, optional
        Source target names, optionally including one '*name' unpack marker.
        None preserves native names. Grouped targets receive numeric suffixes.
    span : ir.Span or tuple, optional
        Loop source location; None omits the span.

    Returns
    -------
    _LoopFrame
        Context manager yielding one serial variable or a grid variable sequence.

    Raises
    ------
    TypeError
        If iterable or names has an unsupported type.
    ValueError
        If unpack arity differs or more than one starred target is supplied.

    Notes
    -----
    Entry pushes native loop frames and names their variables. Exit finalizes
    them. No function-wide type-variable symbols are introduced by loop targets.

    Examples
    --------
    >>> # Inside a function frame:
    >>> with for_(range_(16), names="i") as i:
    ...     emit_(i)
    """
    if names is not None and not isinstance(names, str):
        if not isinstance(names, tuple) or not _python.all(isinstance(name, str) for name in names):
            raise TypeError("Loop names must be a source identifier or tuple of identifiers")
        if _python.sum(name.startswith("*") for name in names) > 1:
            raise ValueError("Loop targets may contain only one starred group")
    with _construction_span(span):
        if isinstance(iterable, _IterationSpec):
            if iterable.kind == "grid":
                iterable = _native.grid(*iterable.arguments, dtype=iterable.dtype)
            else:
                start, stop, step = iterable.arguments
                iterable = _native.serial(start, stop, step=step)
        if isinstance(iterable, _python.range):
            iterable = _native.serial(iterable.start, iterable.stop, step=iterable.step)
        if not isinstance(iterable, _frame.ForFrame):
            raise TypeError("A primitive for loop requires an iteration specification")
        return _LoopFrame(iterable, names, span)


For = for_


def While(condition, *, span=None):
    """Create a native while statement region.

    Parameters
    ----------
    condition : PrimExpr
        Scalar boolean condition.
    span : ir.Span or tuple, optional
        Region source location; None omits the span.

    Returns
    -------
    _Frame
        Context manager for the native while region.

    Raises
    ------
    ValueError
        If the enclosing native region is invalid for this frame.

    Notes
    -----
    Entry pushes the native region and exit finalizes it. Internal bindings are
    lexical to the region and are not implicitly exported.

    Examples
    --------
    >>> # Inside the appropriate native statement context:
    >>> with While(condition):
    ...     emit_(0)
    """
    with _construction_span(span):
        return _Frame(_native.While(condition), span)


def unpack(value):
    """Project a concrete IR tuple while preserving Python iteration.

    Parameters
    ----------
    value : object
        IR tuple, tuple-typed expression or ordinary Python iterable.

    Returns
    -------
    object
        Tuple of concrete fields or projections, or the unchanged Python value.

    Raises
    ------
    TypeError
        If a native tuple projection rejects an invalid type.

    Notes
    -----
    No frame is entered and no construction state is retained.

    Examples
    --------
    >>> unpack((1, 2))
    (1, 2)
    """
    if isinstance(value, _ir.Tuple):
        return _python.tuple(value.fields)
    if isinstance(value, _ir.Expr) and isinstance(value.ty, _ir.TupleType):
        return _python.tuple(_ir.TupleGetItem(value, i) for i in range(len(value.ty.fields)))
    return value


def alloc_scalar(dtype="float32", scope="global"):
    """Allocate scalar storage and return its load expression.

    Parameters
    ----------
    dtype : str, optional
        Scalar dtype, default "float32".
    scope : str, optional
        Storage scope, default "global".

    Returns
    -------
    PrimExpr
        Concrete scalar load, unwrapped from the native scalar wrapper.

    Raises
    ------
    TypeError
        If the native allocation rejects the dtype or storage scope.

    Notes
    -----
    Requires an active allocation-capable native frame. The allocated storage
    belongs to that enclosing construction region.

    Examples
    --------
    >>> # Inside a function frame:
    >>> counter = alloc_scalar("int32", "local")
    """
    value = _native.alloc_scalar(dtype, scope)
    return value.scalar if isinstance(value, _native.scalar_wrapper) else value


def local_scalar(dtype="float32"):
    """Allocate scalar storage in local memory.

    Parameters
    ----------
    dtype : str, optional
        Scalar dtype, default "float32".

    Returns
    -------
    PrimExpr
        Concrete load from the allocated scalar.

    Raises
    ------
    TypeError
        If the dtype cannot describe scalar storage.

    Notes
    -----
    Requires an active allocation-capable frame. Storage belongs to the
    containing native region; no separate Python state is retained.

    Examples
    --------
    >>> # Inside a function frame:
    >>> counter = local_scalar("int32")
    """
    return alloc_scalar(dtype, "local")


def shared_scalar(dtype="float32"):
    """Allocate scalar storage in shared memory.

    Parameters
    ----------
    dtype : str, optional
        Scalar dtype, default "float32".

    Returns
    -------
    PrimExpr
        Concrete load from the allocated scalar.

    Raises
    ------
    TypeError
        If the dtype cannot describe scalar storage.

    Notes
    -----
    Requires an active allocation-capable frame. Storage belongs to the
    containing native region; no separate Python state is retained.

    Examples
    --------
    >>> # Inside a function frame:
    >>> counter = shared_scalar("int32")
    """
    return alloc_scalar(dtype, "shared")


@_expression_args(
    "shape",
    "strides",
    "elem_offset",
    introduce=True,
    implicit_dtype="int32",
    compound_declarations=True,
)
@_wraps(_native.match_buffer)
def match_buffer(*args, **kwargs):
    """Construct a native buffer match with resolved symbolic shape fields."""
    return _native.match_buffer(*args, **kwargs)


# Constructor identities carry syntax policy; aliases share it without wrappers.
for _constructor in vars(_native).values():
    if isinstance(_constructor, _native.DtypeConstructor):
        _register_type_var_decl(_constructor, dtype=_constructor._dtype_str)
del _constructor


def range_(*args):
    """Describe serial iteration using Python's range argument forms.

    Parameters
    ----------
    *args : int or PrimExpr
        Stop; start and stop; or start, stop and step. Start defaults to zero,
        and omitted step retains the native semantic default of one.

    Returns
    -------
    _IterationSpec
        Descriptor consumed by for_ or directly as a context manager.

    Raises
    ------
    TypeError
        Unless one to three positional arguments are supplied.
    ValueError
        If a literal step is zero or the descriptor is directly re-entered.

    Notes
    -----
    Creates no native frame or variable until consumed. Native loops validate
    other bounds and steps. Sequential context-manager reuse creates fresh frames.

    Examples
    --------
    >>> iteration = range_(0, 16, 2)
    """
    if len(args) == 1:
        args = (0, args[0], None)
    elif len(args) == 2:
        args = (*args, None)
    elif len(args) != 3:
        raise TypeError("range expects one to three arguments")
    if isinstance(args[2], _python.int) and args[2] == 0:
        raise ValueError("range step cannot be zero")
    return _IterationSpec("range", args)


def logical_and(*values):
    """Construct scalar or vector conjunction from eager operands.

    Parameters
    ----------
    *values : object
        Host values or primitive IR expressions, optionally ObjectConvertible.

    Returns
    -------
    object
        Selected host value or native scalar/vector boolean expression.

    Raises
    ------
    TypeError
        If no operands are supplied or native types are incompatible.

    Notes
    -----
    Python has already constructed all arguments. Native scalar operands use
    boolean IR nodes; vector operands use elementwise operators. No frame or
    construction state persists after the call.

    Examples
    --------
    >>> logical_and(True, False)
    False
    """
    if not values:
        raise TypeError("logical_and requires at least one operand")
    values = [
        value.asobject() if isinstance(value, _ffi.ObjectConvertible) else value for value in values
    ]
    result = values[0]
    for value in values[1:]:
        if not isinstance(result, _ir.Expr) and not isinstance(value, _ir.Expr):
            result = result and value
        else:
            lhs, rhs = _as_expr(result), _as_expr(value)
            result = _tir.And(lhs, rhs) if lhs.ty.is_scalar() and rhs.ty.is_scalar() else lhs & rhs
    return result


def logical_or(*values):
    """Construct scalar or vector disjunction from eager operands.

    Parameters
    ----------
    *values : object
        Host values or primitive IR expressions, optionally ObjectConvertible.

    Returns
    -------
    object
        Selected host value or native scalar/vector boolean expression.

    Raises
    ------
    TypeError
        If no operands are supplied or native types are incompatible.

    Notes
    -----
    Python has already constructed all arguments. Native scalar operands use
    boolean IR nodes; vector operands use elementwise operators. No frame or
    construction state persists after the call.

    Examples
    --------
    >>> logical_or(True, False)
    True
    """
    if not values:
        raise TypeError("logical_or requires at least one operand")
    values = [
        value.asobject() if isinstance(value, _ffi.ObjectConvertible) else value for value in values
    ]
    result = values[0]
    for value in values[1:]:
        if not isinstance(result, _ir.Expr) and not isinstance(value, _ir.Expr):
            result = result or value
        else:
            lhs, rhs = _as_expr(result), _as_expr(value)
            result = _tir.Or(lhs, rhs) if lhs.ty.is_scalar() and rhs.ty.is_scalar() else lhs | rhs
    return result


def logical_not(value):
    """Negate a host or IR value without testing IR truth in Python.

    Parameters
    ----------
    value : object
        Host value or boolean IR expression, optionally ObjectConvertible.

    Returns
    -------
    bool or PrimExpr
        Host boolean or primitive boolean expression.

    Raises
    ------
    TypeError
        If native operand types or host truth conversion are invalid.

    Notes
    -----
    This operation enters no frame and retains no state.

    Examples
    --------
    >>> logical_not(False)
    True
    """
    if isinstance(value, _ffi.ObjectConvertible):
        value = value.asobject()
    return _tir.Not(value) if isinstance(value, _ir.Expr) else not value


def select(condition, true_value, false_value):
    """Construct a scalar conditional whose runtime evaluates one arm.

    Parameters
    ----------
    condition : bool or PrimExpr
        Host condition or scalar boolean expression, optionally ObjectConvertible.
    true_value : object
        Already constructed value selected by a true condition.
    false_value : object
        Already constructed value selected by a false condition.

    Returns
    -------
    object
        Selected host value or scalar primitive conditional expression.

    Raises
    ------
    TypeError
        If the condition is not scalar or IR arm types are incompatible.

    Notes
    -----
    Python eagerly constructs all three arguments. The native conditional emits
    control flow without binding both arms outside it. No frame or state persists.

    Examples
    --------
    >>> select(True, 1, 2)
    1
    """
    if isinstance(condition, _ffi.ObjectConvertible):
        condition = condition.asobject()
    if not isinstance(condition, _ir.Expr):
        return true_value if condition else false_value
    return _tir.if_then_else(condition, true_value, false_value)


def if_then_else_(condition, true_value, false_value):
    """Construct a scalar conditional whose runtime evaluates one arm.

    Parameters
    ----------
    condition : bool or PrimExpr
        Host condition or scalar boolean expression, optionally ObjectConvertible.
    true_value : object
        Already constructed value selected by a true condition.
    false_value : object
        Already constructed value selected by a false condition.

    Returns
    -------
    object
        Selected host value or scalar primitive conditional expression.

    Raises
    ------
    TypeError
        If the condition is not scalar or IR arm types are incompatible.

    Notes
    -----
    Python eagerly constructs all three arguments. The native conditional emits
    control flow without binding both arms outside it. No frame or state persists.

    Examples
    --------
    >>> if_then_else_(True, 1, 2)
    1
    """
    return select(condition, true_value, false_value)


def and_(*values, chain=None):
    """Construct left-to-right scalar conjunction with runtime short circuit.

    Parameters
    ----------
    *values : object
        Eagerly constructed host values or scalar boolean IR expressions.
    chain : tuple, optional
        Original comparison operands; None denotes an ordinary conjunction.
        A chain must contain one more operand than values.

    Returns
    -------
    object
        Selected host value or scalar boolean expression.

    Raises
    ------
    TypeError
        If no values are supplied or IR operands are invalid.
    ValueError
        If the chain operand count is invalid.

    Notes
    -----
    Runtime evaluation stops at the first false operand. For comparison chains,
    native bindings evaluate each shared operand once when its comparison is
    reached. All operands are eagerly constructed; no callbacks or frames persist.

    Examples
    --------
    >>> and_(True, False)
    False
    """
    if not values:
        raise TypeError("and_ requires at least one operand")
    if chain is not None:
        from .comparison import _comparison_chain

        return _comparison_chain(values, chain, and_, _tir.Let)
    result = values[-1]
    for value in reversed(values[:-1]):
        result = if_then_else_(
            value, result, False if isinstance(value, _ir.Expr | _ffi.ObjectConvertible) else value
        )
    return result


def or_(*values):
    """Construct left-to-right scalar disjunction with runtime short circuit.

    Parameters
    ----------
    *values : object
        Eagerly constructed host values or scalar boolean IR expressions.

    Returns
    -------
    object
        Selected host value or scalar boolean expression.

    Raises
    ------
    TypeError
        If no operands are supplied or IR operands have invalid types.

    Notes
    -----
    Runtime evaluation stops at the first true operand. Python has already
    constructed every argument. No native frame or state persists after the call.

    Examples
    --------
    >>> or_(False, True)
    True
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
    """Negate a host or IR value without testing IR truth in Python.

    Parameters
    ----------
    value : object
        Host value or boolean IR expression, optionally ObjectConvertible.

    Returns
    -------
    bool or PrimExpr
        Host boolean or primitive boolean expression.

    Raises
    ------
    TypeError
        If native operand types or host truth conversion are invalid.

    Notes
    -----
    This operation enters no frame and retains no state.

    Examples
    --------
    >>> not_(False)
    True
    """
    return logical_not(value)
