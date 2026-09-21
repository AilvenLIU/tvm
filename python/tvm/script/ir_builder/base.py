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
"""A generic IRBuilder across the TVM stack"""

from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from typing import Any

from tvm_ffi import register_object as _register_object

from tvm import ir
from tvm.runtime import Object as _Object

from . import _ffi_api


@_register_object("script.ir_builder.IRBuilderFrame")
class IRBuilderFrame(_Object):
    """A stack frame of the IRBuilder used to keep track of the current scope.

    Furthermore, the information stored in each stack frame can be useful for context-dependent
    IR construction.

    Examples
    --------

    The `T.match_buffer` below instead an element in the buffer map of `PrimFuncFrame`:

    .. code-block:: python

        from tvm.script.ir_builder import tirx as T
        from tvm.script.ir_builder import IRBuilder

        with IRBuilder() as builder:
            with T.prim_func(...):  # pushes a PrimFuncFrame (subclass of IRBuilderFrame)
                                    # to `builder`'s stack of frames
                buffer = T.match_buffer(...)


    The `T.match_buffer` below instead generates `MatchBufferRegion` in a TIR block:

    .. code-block:: python

        from tvm.script.ir_builder import tirx as T
        from tvm.script.ir_builder import IRBuilder

        with IRBuilder() as builder:
            with T.prim_func(...):  # pushes a PrimFuncFrame (subclass of IRBuilderFrame)
                                    # to `builder`'s stack of frames
               with T.sblock(...):  # pushes a BlockFrame (subclass of IRBuilderFrame)
                                    # to `builder`'s stack of frames
                    buffer = T.match_buffer(...)

    """

    def __enter__(self) -> "IRBuilderFrame":
        _ffi_api.IRBuilderFrameEnter(self)  # type: ignore[attr-defined] # pylint: disable=no-member
        return self

    def __exit__(self, exc_type, exc_value, trace) -> None:  # pylint: disable=unused-argument
        if exc_type is None and exc_value is None:
            # Do not execute `FrameExit` if the with scope exits because of exceptions
            _ffi_api.IRBuilderFrameExit(self)  # type: ignore[attr-defined] # pylint: disable=no-member

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Add a callback method invoked when exiting the with-scope.

        Parameters
        ----------
        callback : Callable[[], None]
            The callback method to be invoked.
        """
        _ffi_api.IRBuilderFrameAddCallback(  # type: ignore[attr-defined] # pylint: disable=no-member
            self, callback
        )


@_register_object("script.ir_builder.IRBuilder")
class IRBuilder(_Object):
    """A dialect-agnostic IRBuilder that constructs any IR of TVM.

    Examples
    --------
    An idiomatic use of this class is to put this inside the with-scope,
    call dialect-specific methods accordingly. Upon exiting the scope.

    .. code-block:: python

        from tvm.script.ir_builder import tirx as T
        from tvm.script.ir_builder import IRBuilder

        with IRBuilder() as builder:
            with T.prim_func(...):  # pushes a PrimFuncFrame (subclass of IRBuilderFrame)
                                # to `builder`'s stack of frames
                buffer = T.match_buffer(...)

        return builder.get()        # returns the constructed IR, i.e. tirx.PrimFunc
    """

    def __init__(self) -> None:
        """Construct an IRBuilder."""
        self.__init_handle_by_constructor__(
            _ffi_api.IRBuilder  # type: ignore[attr-defined] # pylint: disable=no-member
        )

    def __enter__(self) -> "IRBuilder":
        """Enter the with-scope for IRBuilder, which allows the IRBuilder to be discoverable
        using `IRBuilder.current()`.

        Examples
        --------
        .. code-block:: python

            from tvm.script.ir_builder import IRBuilder

            with IRBuilder() as builder:
                assert IRBuilder.current() == builder

        """
        _ffi_api.IRBuilderEnter(self)  # type: ignore[attr-defined] # pylint: disable=no-member
        return self

    def __exit__(self, ptype, value, trace) -> None:  # pylint: disable=unused-argument
        _ffi_api.IRBuilderExit(self)  # type: ignore[attr-defined] # pylint: disable=no-member

    @staticmethod
    def current() -> "IRBuilder":
        """Get the current IRBuilder put in the with-scope.

        Returns
        -------
        builder : IRBuilder
            The current IRBuilder.
        """
        return _ffi_api.IRBuilderCurrent()  # type: ignore[attr-defined] # pylint: disable=no-member

    @staticmethod
    def is_in_scope() -> bool:
        """See if the current thread-local scope has an IRBuilder.

        Returns
        -------
        bool
            Whether the current thread-local scope has an IRBuilder
        """
        return _ffi_api.IRBuilderIsInScope()  # type: ignore[attr-defined] # pylint: disable=no-member

    def get(self) -> _Object:
        """Get the constructed IR."""
        return _ffi_api.IRBuilderGet(self)  # type: ignore[attr-defined] # pylint: disable=no-member

    @contextmanager
    def with_source_span(self, span):
        """Attach ``span`` to IR nodes constructed in the nested scope.

        Nested scopes are retained as a ``SequentialSpan`` when they describe
        distinct source ranges, such as a TVMScript inline expansion.

        Parameters
        ----------
        span : tvm.ir.Span
            The frontend source range active in the nested scope.
        """
        _ffi_api.IRBuilderPushSourceSpan(  # type: ignore[attr-defined] # pylint: disable=no-member
            self, span
        )
        try:
            yield
        finally:
            _ffi_api.IRBuilderPopSourceSpan(  # type: ignore[attr-defined] # pylint: disable=no-member
                self
            )

    def _set_current_source_span(self, value):
        """Attach the active source span to an expression without one."""
        return _ffi_api.IRBuilderSetCurrentSourceSpan(  # type: ignore[attr-defined] # pylint: disable=no-member
            self, value
        )

    @staticmethod
    def name(s: str, v: Any) -> Any:
        """Set the name of an object.

        Parameters
        ----------
        s : str
            The name of the object.
        v : Any
            The object to name.

        Returns
        -------
        v : Any
            The same object with the name set.
        """
        return _ffi_api.IRBuilderName(s, v)  # type: ignore[attr-defined] # pylint: disable=no-member

    @staticmethod
    def name_many(  # pylint: disable=invalid-name
        s: list[str],
        vs: list[Any],
    ) -> list[Any]:
        """Set the name of a list of objects.

        Parameters
        ----------
        s : List[str]
            The names of the objects.
        vs : List[Any]
            The objects to name.

        Returns
        -------
        vs : List[Any]
            The same objects with the names set.
        """
        assert len(s) == len(vs)
        return [IRBuilder.name(i, v) for i, v in zip(s, vs)]


# V2 absence is distinct from legacy protocol state; no function data is retained.
class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


def source_span(location):
    """Materialize a source range without retaining source-unit state.

    Parameters
    ----------
    location : ir.Span, tuple, or None
        Optional source range accepted by source_span; None creates no source metadata.

    Returns
    -------
    ir.Span or None
        The existing span, a span with exact supplied coordinates, or None.

    Raises
    ------
    TypeError or ValueError
        The location cannot be unpacked or its fields are invalid.

    Notes
    -----
    Generated tuples reuse one parser-created SourceName per source unit. Lines are one-based
    and columns are zero-based UTF-8 byte offsets. Filename strings remain available to
    handwritten callers. None is a no-op and accesses no builder; this operation enters no
    frame.

    Examples
    --------
    >>> source_span(None) is None
    True
    """
    if location is None or isinstance(location, ir.Span):
        return location
    source_name, line, end_line, column, end_column = location
    if isinstance(source_name, str):
        source_name = ir.SourceName(source_name)
    return ir.Span(source_name, line, end_line, column, end_column)


@contextmanager
def _construction_span(span):
    """Apply a builder operation's span through the existing native span stack.

    This private construction helper is used only by builders, never emitted by
    the transpiler. ``span`` accepts the source_span input forms. None yields
    directly without IR instrumentation. Otherwise it pushes/pops the existing
    builder span stack when active, and retains no context after exit. Without
    a builder it still materializes diagnostic locations.
    Exceptions retain their original type and receive __tvm_script_location__
    as a plain location tuple only when an inner operation has not already
    supplied a more precise range. Diagnostics need never inspect an IR object.
    Span-construction and nested-operation errors propagate unchanged.
    """
    if span is None:
        yield
        return
    span = source_span(span)
    context = (
        IRBuilder.current().with_source_span(span)
        if span is not None and IRBuilder.is_in_scope()
        else nullcontext()
    )
    try:
        with context:
            yield
    except Exception as error:
        if span is not None and not hasattr(error, "__tvm_script_location__"):
            diagnostic_span = span.spans[-1] if isinstance(span, ir.SequentialSpan) else span
            error.__tvm_script_location__ = (
                str(diagnostic_span.source_name.name),
                diagnostic_span.line,
                diagnostic_span.end_line,
                diagnostic_span.column,
                diagnostic_span.end_column,
            )
        raise


def at(span, value):
    """Attach a source range to a concrete expression when a builder is active.

    Parameters
    ----------
    span : ir.Span, tuple, or None
        Optional source range. A tuple contains SourceName (or filename), start/end lines, and
        start/end UTF-8 byte columns; None adds no metadata.
    value : object
        Already constructed IR expression or arbitrary Python value.

    Returns
    -------
    object
        The same value, with missing expression source metadata filled when supported.

    Raises
    ------
    TypeError or ValueError
        A source range used for an IR expression is invalid.

    Notes
    -----
    None returns the value without source instrumentation. Ordinary Python values and values
    outside a builder pass through. An active builder temporarily uses its native source-span
    stack; no construction frame or persistent cache is created. This is a value operation, not
    a context manager or callback.

    Examples
    --------
    >>> at(None, 3)
    3
    """
    if span is not None and isinstance(value, ir.Expr) and IRBuilder.is_in_scope():
        with _construction_span(span):
            return IRBuilder.current()._set_current_source_span(value)
    return value


def _frame_result(frame, name):
    """Read one explicit export without consulting ambient construction state.

    Dialects pass a completed frame whose result is its finalized export map,
    or a Python-branch dictionary containing host lexical results. The map stays
    private to its owner; a missing name returns MISSING instead of leaking a
    branch-local binding. No frame is entered and no result is cached globally.
    """
    if not isinstance(name, str):
        raise TypeError("A frame result name must be a string")
    exports = frame if isinstance(frame, dict) else getattr(frame, "result", {})
    return exports.get(name, MISSING)
