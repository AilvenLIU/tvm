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
import sys as _sys
from functools import partial as _partial
from functools import wraps as _wraps

import tvm_ffi as _ffi

from tvm import ir as _ir
from tvm import tirx as _tir
from tvm.script.ir_builder import IRBuilder as _IRBuilder
from tvm.script.ir_builder import ir as _I
from tvm.script.ir_builder.base import IRBuilderFrame as _NativeFrame
from tvm.script.ir_builder.protocol import MISSING as _MISSING
from tvm.script.ir_builder.protocol import at as _at
from tvm.script.ir_builder.protocol import expression_args as _expression_args
from tvm.script.ir_builder.protocol import register_call_kind as _register_call_kind
from tvm.script.ir_builder.protocol import register_declaration as _register_declaration
from tvm.script.ir_builder.protocol import span_context as _span_context
from tvm.tirx.lang.alloc_pool import SMEMPool, TMEMPool

from . import _ffi_api
from . import frame as _frame
from . import ir as _native
from . import tirx as tile
from .ir import *  # pylint: disable=wildcard-import,redefined-builtin
from .ir import boolean as bool  # pylint: disable=redefined-builtin
from .tirx import cluster, cta, thread, warp, warpgroup, wg
from .utils import buffer_proxy, frame_scope, seq_scope

is_type_var = _ir.is_prim_var


def type_var(name, *, dtype=None, span=None):
    """Construct a signature symbol; shape symbols default to int64."""
    return _ir.Var(name, "int64" if dtype is None else dtype, span)


@_expression_args(
    "shape",
    "strides",
    "elem_offset",
    "byte_offset",
    introduce=True,
    implicit_dtype="int32",
    compound_declarations=True,
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
    """Construct a concrete buffer from resolved shape expressions."""
    with _span_context(span):
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
    """Construct a concrete pointer variable usable as a function annotation."""
    if callable(dtype) and not isinstance(dtype, _ir.Expr):
        dtype = dtype()
    if isinstance(dtype, _ir.Expr):
        dtype = dtype.ty
    if isinstance(dtype, _ir.PrimType):
        dtype = dtype.dtype
    with _span_context(span):
        return _at(span, _native.ptr(dtype, storage_scope))


class _Frame:
    """Preserve a frame's source range through native finalization."""

    def __init__(self, native, span=None):
        self.native = native
        self.span = span
        self.result = {}

    def __enter__(self):
        with _span_context(self.span):
            value = self.native.__enter__()
        return self if value is self.native else value

    def __exit__(self, *exc):
        with _span_context(self.span):
            return self.native.__exit__(*exc)

    @property
    def reference(self):
        """Return the stable module reference after signature finalization."""
        return self.native.global_var

    def __getattr__(self, name):
        return getattr(self.native, name)


def function(*, private=False, s_tir=False, persistent=False, span=None):
    """Enter a native primitive-function definition frame."""
    with _span_context(span):
        return _Frame(_native.prim_func(private=private, s_tir=s_tir, persistent=persistent), span)


def decl_function(*, private=False, s_tir=False, persistent=False, span=None):
    """Declare a bodyless signature using the native function frame."""
    with _span_context(span):
        return _Frame(_ffi_api.DeclFunction(private, s_tir, persistent), span)


def arg(name, annotation, *, span=None):
    """Use the same concrete parameter object in declaration and definition."""
    if callable(annotation) and not isinstance(annotation, _ir.Expr):
        annotation = annotation()
    if isinstance(annotation, _ir.Type):
        annotation = _ir.Var(name, annotation)
    with _span_context(span):
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
    """Set the signature's concrete return type."""
    if callable(annotation) and not isinstance(annotation, _ir.Expr):
        annotation = annotation()
    if isinstance(annotation, _ir.Expr):
        annotation = annotation.ty
    with _span_context(span):
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
    """Bind values, or name a frame-owned value without introducing new storage."""
    name_span = span if name_span is None else name_span
    with _span_context(span):
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
                buffer_store(previous, value, [0])
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
                annotation = annotation.ty if isinstance(annotation, _ir.Expr) else annotation
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
            buffer_store(previous.source, value, list(previous.indices))
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
            annotation = annotation.ty if isinstance(annotation, _ir.Expr) else annotation
            if not isinstance(annotation, _ir.PrimType) or str(annotation) == "handle":
                raise TypeError("Mutable scalar annotations require a primitive scalar type")
            result = _native.local_scalar(str(annotation)).scalar
            _name(result.source, name, name_span)
            if value is not _MISSING:
                buffer_store(result.source, value, [0])
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
            buffer_store(result.source, value, [0])
            return result
        return _name(_native.Bind(value), name, name_span)


def emit_(value, *, span=None):
    """Consume one expression statement, including effect-only calls."""
    if value is None or isinstance(value, str | _ir.Var):
        return
    with _span_context(span):
        if isinstance(value, _NativeFrame | _Frame):
            _enter_concise(value)
        elif hasattr(value, "frames"):
            for frame in value.frames:
                _enter_concise(frame)
        elif isinstance(value, _tir.BufferStore):
            buffer_store(value.buffer, value.value, value.indices)
        else:
            _native.evaluate(value)


def setitem(target, key, value, *, span=None):
    """Construct an indexed store after the caller has evaluated its operands."""
    with _span_context(span):
        buffer_store(target, value, key)


def setattr(target, name, value, *, span=None):
    """Store through scalar attributes, or update ordinary Python metadata."""
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


def return_(value, *, span=None):
    """Emit a native return; subsequent unreachable statements remain in the IR."""
    if value is None:
        raise TypeError("A primitive function return requires an expression")
    with _span_context(span):
        _native.Return(_as_expr(value))


def _require_loop():
    for frame in reversed(_IRBuilder.current().frames):
        if isinstance(frame, _frame.ForFrame | _frame.WhileFrame):
            return
        if isinstance(frame, _frame.PrimFuncFrame):
            break
    raise ValueError("Loop control requires an enclosing primitive loop")


def break_(*, span=None):
    """Construct a break targeting the nearest primitive loop."""
    _require_loop()
    with _span_context(span):
        _native.evaluate(_native.break_loop())


def continue_(*, span=None):
    """Construct a continue targeting the nearest primitive loop."""
    _require_loop()
    with _span_context(span):
        _native.evaluate(_native.continue_loop())


def assert_(condition, message="", *, span=None):
    """Emit the native flat assertion with its own source range."""
    kind = "RuntimeError"
    if isinstance(message, tuple):
        if len(message) != 2 or not isinstance(message[0], str):
            raise TypeError("Assertion metadata must be (error_kind, message_parts)")
        kind, message = message
    if isinstance(message, list | tuple):
        message = [str(part) for part in message]
    if not isinstance(message, list | tuple):
        message = [message]
    with _span_context(span):
        with _native.Assert(condition, message, error_kind=kind):
            pass


def If(condition, *, span=None):
    with _span_context(span):
        return _Frame(_native.If(condition), span)


def Then(*, span=None):
    with _span_context(span):
        return _Frame(_native.Then(), span)


def Else(*, span=None):
    with _span_context(span):
        return _Frame(_native.Else(), span)


def For(iterable, *, span=None):
    """Adapt a native loop frame, or a Python range, to construction scope."""
    if isinstance(iterable, _python.range):
        iterable = _native.serial(iterable.start, iterable.stop, step=iterable.step)
    if not isinstance(iterable, _frame.ForFrame):
        raise TypeError("A primitive for loop requires a native loop frame or range")
    return _Frame(iterable, span)


def While(condition, *, span=None):
    with _span_context(span):
        return _Frame(_native.While(condition), span)


def unpack(value):
    """Project a concrete IR tuple of known arity, preserving Python iteration."""
    if isinstance(value, _ir.Tuple):
        return _python.tuple(value.fields)
    if isinstance(value, _ir.Expr) and isinstance(value.ty, _ir.TupleType):
        return _python.tuple(_ir.TupleGetItem(value, i) for i in range(len(value.ty.fields)))
    return value


def alloc_scalar(dtype="float32", scope="global"):
    """Allocate scalar storage and return its concrete load expression."""
    value = _native.alloc_scalar(dtype, scope)
    return value.scalar if isinstance(value, _native.scalar_wrapper) else value


def local_scalar(dtype="float32"):
    return alloc_scalar(dtype, "local")


def shared_scalar(dtype="float32"):
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
        _register_declaration(_constructor, dtype=_constructor._dtype_str)
del _constructor


def range_(*args):
    """Construct a serial loop from the source builtin range arguments."""
    if len(args) in (1, 2):
        return _native.serial(*args)
    if len(args) != 3:
        raise TypeError("range expects one to three arguments")
    start, stop, step = args
    if isinstance(step, _python.int) and step == 0:
        raise ValueError("range step cannot be zero")
    return _native.serial(start, stop, step=step)


__tvm_call_overrides__ = {_python.range: range_}


def logical_and(*values):
    """Construct scalar/vector conjunction, preserving ordinary Python values."""
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
    """Construct scalar/vector disjunction, preserving ordinary Python values."""
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
    """Construct IR negation without coercing an IR expression to Python bool."""
    if isinstance(value, _ffi.ObjectConvertible):
        value = value.asobject()
    return _tir.Not(value) if isinstance(value, _ir.Expr) else not value


def select(condition, true_value, false_value):
    """Construct a conditional expression or select an ordinary Python value."""
    if isinstance(condition, _ffi.ObjectConvertible):
        condition = condition.asobject()
    if not isinstance(condition, _ir.Expr):
        return true_value if condition else false_value
    return _tir.if_then_else(condition, true_value, false_value)


def _global_callee(function):
    return _partial(_native._call_global, function)


_register_call_kind(_sys.modules[__name__], _ir.GlobalVar, _global_callee)


from tvm.script.parser.annotations import enable_eager_constructors as _enable_eager_constructors

_enable_eager_constructors(_sys.modules[__name__], classes=("Buffer",))
