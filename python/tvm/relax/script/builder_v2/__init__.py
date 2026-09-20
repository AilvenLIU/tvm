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
from tvm.script.ir_builder import IRBuilder as _IRBuilder
from tvm.script.ir_builder import ir as _I
from tvm.script.ir_builder import protocol as _protocol

from .. import builder as _legacy
from ..builder import *
from ..builder import _ffi_api
from ..builder import frame as _frame


@_protocol.expression_args("shape", introduce=True, dtype="int64", scalar_strings=False)
def Tensor(shape=None, dtype=None, vdevice=None, ndim=-1, *, span=None):
    """Construct a concrete tensor type from already resolved dimensions."""
    if isinstance(shape, _python.str) and dtype is None:
        dtype, shape = shape, None
    if isinstance(vdevice, _python.str):
        target, _, index = vdevice.partition(":")
        vdevice = _I.lookup_vdevice(target, int(index) if index else 0)
    return _relax.TensorType(shape, dtype, vdevice, ndim, span)


@_protocol.expression_args("values", introduce=True, dtype="int64")
def Shape(values=None, ndim=-1, *, span=None):
    """Construct a concrete shape type."""
    return _relax.ShapeType(values, ndim, span)


def _type(value):
    if value is None:
        return _ir.TupleType([])
    if callable(value):
        value = value()
    if _ir.is_prim_expr(value):
        value = value.ty
    if not isinstance(value, _ir.Type):
        raise TypeError(f"Expected a concrete type, got {type(value).__name__}")
    return value


def Callable(params=None, ret=None, purity=None, derive_func=None, *, span=None):
    """Construct a concrete function type."""
    if purity is None:
        purity = params is not None
    if params is None:
        return _relax.FuncType.opaque_func(
            ret=None if ret is None else _type(ret),
            derive_func=derive_func,
            purity=purity,
            span=span,
        )
    if derive_func is not None:
        raise ValueError("A derivation function requires an opaque callable")
    if not isinstance(params, list | _python.tuple):
        params = [params]
    return _relax.FuncType([_type(param) for param in params], _type(ret), purity, span)


def Tuple(*fields, span=None):
    """Construct a concrete tuple type."""
    if len(fields) == 1 and isinstance(fields[0], list | _python.tuple):
        fields = fields[0]
    return _ir.TupleType([_type(field) for field in fields], span)


def Prim(dtype, *, span=None):
    """Construct a primitive type."""
    return _ir.PrimType(dtype)


def Object(*, span=None):
    """Construct the unconstrained Relax value type."""
    return _relax.AnyType(span)


Any = Object


def type_var(name, *, dtype=None, span=None):
    """Construct a signature symbol under Relax's default shape dtype policy."""
    return _ir.Var(name, "int64" if dtype is None else dtype, span)


class _Frame:
    """Retain source metadata and exports around an existing native frame."""

    def __init__(self, native, span=None):
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
        with _protocol.span_context(self.span):
            self.native.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with _protocol.span_context(self.span):
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


def function(is_pure=True, is_private=False, *, local=False, reference=None, span=None):
    """Enter a definition using the native Relax function frame."""
    if local:
        if reference is None:
            raise ValueError("A local function requires its declared reference")
        return _Frame(_ffi_api.LocalFunction(is_pure, reference), span)
    return _Frame(_legacy.function(is_pure, is_private), span)


def decl_function(is_pure=True, is_private=False, *, local=False, span=None):
    """Declare a bodyless function with the same signature operations as a definition."""
    return _Frame(_ffi_api.DeclFunction(is_pure, is_private, local), span)


def arg(name, ty, *, span=None):
    """Add a parameter, retaining a cached parameter's identity when supplied."""
    with _protocol.span_context(span):
        if isinstance(ty, _ir.Var):
            return _ffi_api.ArgVar(name, ty)
        return _protocol.at(span, _legacy.arg(name, _type(ty)))


def func_ret_type(ret_ty):
    """Set the concrete return type of the active declaration or definition."""
    return _legacy.func_ret_type(_type(ret_ty))


func_ret_ty = func_ret_type


def dataflow(*, span=None):
    """Create a dataflow region whose result maps exported names to finalized vars."""
    return _Frame(_legacy.dataflow(), span)


def If(condition, *, span=None):
    """Create a conditional region with finalized named exports."""
    return _Frame(_legacy.If(condition), span)


def Then(*, span=None):
    """Create the true branch of a conditional."""
    return _Frame(_legacy.Then(), span)


def Else(*, span=None):
    """Create the false branch of a conditional."""
    return _Frame(_legacy.Else(), span)


def _check_unterminated():
    for frame in reversed(_IRBuilder.current().frames):
        if isinstance(frame, _frame.FunctionFrame):
            if frame.output is not None:
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
    value=_protocol.MISSING,
    *,
    ty=None,
    name=None,
    span=None,
    name_span=None,
    previous=_protocol.MISSING,
    declaration=False,
):
    """Emit an immutable Relax binding and return the newly bound value."""
    _check_unterminated()
    if declaration:
        if not _ir.is_prim_var(value):
            raise TypeError("A symbol declaration requires a concrete primitive variable")
        if ty is not None and not _ffi.structural_equal(_type(ty), value.ty):
            raise TypeError("The symbol declaration has an incompatible type")
        if previous is not _protocol.MISSING:
            if not _ir.is_prim_var(previous) or not _ffi.structural_equal(previous.ty, value.ty):
                raise TypeError("The symbol declaration has an incompatible signature dtype")
            return previous
        if name is not None:
            _IRBuilder.name(name, value)
        return _protocol.at(name_span if name_span is not None else span, value)
    if value is _protocol.MISSING:
        raise ValueError("Relax bindings require an initializer")
    if isinstance(value, _I.meta_var):
        return value.value
    ty = None if ty is None else _type(ty)
    value = _value(value, ty)
    with _protocol.span_context(span):
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
    return _protocol.at(name_span if name_span is not None else span, result)


def emit_(value, *, span=None):
    """Consume an expression statement; effect-only operations return None."""
    if value is not None:
        bind_(value, span=span)


def return_(value=None, *, span=None):
    """Record the function result without exiting Python construction."""
    _check_unterminated()
    with _protocol.span_context(span):
        if value is None:
            value = _relax.Tuple([])
        _legacy.func_ret_value(_value(value))


def match_cast(value, ty, *, span=None):
    """Construct a concrete match-cast binding for bind_ to consume."""
    if value is None:
        raise ValueError("The match-cast value cannot be None")
    ty = _type(ty)
    return _relax.MatchCast(_ir.Var("", ty), _value(value), ty, span)


def unpack(value):
    """Project an IR tuple with known arity; leave Python iteration unchanged."""
    if isinstance(value, _relax.Tuple):
        return _python.tuple(value.fields)
    if isinstance(value, _relax.Expr) and isinstance(value.ty, _ir.TupleType):
        return _python.tuple(_relax.TupleGetItem(value, i) for i in range(len(value.ty.fields)))
    return value


def assert_(condition, message="", *, span=None):
    """Construct a runtime assertion with construction-time diagnostic text."""
    if not isinstance(message, _python.str):
        raise TypeError("An assertion message must be construction-time text")
    with _protocol.span_context(span):
        emit_(_protocol.at(span, _legacy.assert_op(condition, format=message)), span=span)


def For(*args, span=None, **kwargs):
    """Reject imperative loops in the Relax expression dialect."""
    raise TypeError("Relax does not support imperative for loops")


def break_(*, span=None):
    """Reject imperative loop control in Relax."""
    raise TypeError("Relax does not support break")


def continue_(*, span=None):
    """Reject imperative loop control in Relax."""
    raise TypeError("Relax does not support continue")


def setitem(target, index, value, *, span=None):
    """Reject mutable stores, which are not a Relax binding operation."""
    raise TypeError("Relax does not support indexed assignment")


__all__ = [
    *_legacy.ir.__all__,
    "Any",
    "Callable",
    "For",
    "Object",
    "Prim",
    "Shape",
    "Tensor",
    "Tuple",
    "assert_",
    "break_",
    "continue_",
    "bind_",
    "decl_function",
    "emit_",
    "match_cast",
    "return_",
    "setitem",
    "type_var",
    "unpack",
]
