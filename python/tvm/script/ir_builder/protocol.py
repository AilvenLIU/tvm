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
"""Shared construction metadata for explicit TVMScript builders.

Metadata describes source syntax; decorated constructors still execute normally
and return concrete values. Dialects register their own function kinds here, so
translation consumes construction policies without importing their owners.
"""

from builtins import locals as locals
from builtins import slice as slice
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from inspect import signature
from typing import Any, NamedTuple

from tvm import ir

from .base import IRBuilder


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


class ExpressionArguments(NamedTuple):
    """Syntax policy shared by a constructor and every alias of it."""

    fields: tuple[str, ...]
    introduce: bool = False
    dtype: Any = None
    scalar_strings: bool = True
    implicit_dtype: Any = None
    compound_declarations: bool = False


def expression_args(
    *fields,
    introduce=False,
    dtype=None,
    scalar_strings=True,
    implicit_dtype=None,
    compound_declarations=False,
):
    """Mark constructor fields whose nested strings are source expressions.

    ``introduce`` permits signature/match scopes to introduce otherwise unknown
    names. ``dtype`` optionally selects the dialect's symbol-construction policy;
    it is metadata, never an evaluator or a replacement constructor. Set
    ``scalar_strings=False`` when a bare string is literal shorthand while
    strings nested in tuples/lists remain expressions. ``implicit_dtype`` selects
    only the default for names introduced by strings; explicit TypeVars retain
    the dialect default unless ``dtype`` is supplied. ``compound_declarations``
    also permits first-use declarations inside quoted compound expressions.
    """

    def decorate(constructor):
        parameters = signature(constructor).parameters
        unknown = set(fields).difference(parameters)
        if unknown:
            raise ValueError(f"Unknown expression argument fields: {sorted(unknown)}")
        constructor.__tvm_expression_args__ = ExpressionArguments(
            tuple(fields),
            bool(introduce),
            dtype,
            bool(scalar_strings),
            implicit_dtype,
            bool(compound_declarations),
        )
        return constructor

    return decorate


class DeclarationArguments(NamedTuple):
    """Callable syntax that declares a symbol when its value argument is absent."""

    value_parameter: str
    dtype: Any = None


def register_declaration(constructor, *, value_parameter="expr", dtype=None):
    """Mark a concrete constructor's declaration form without wrapping the call."""
    constructor.__tvm_declaration_args__ = DeclarationArguments(value_parameter, dtype)
    return constructor


@dataclass(frozen=True)
class FunctionKind:
    """Construction namespace and policies registered by a function decorator."""

    builder: Any
    metadata: dict


def register_function(decorator, builder, **metadata):
    """Associate a decorator identity with its explicit construction namespace."""
    decorator.__tvm_function_kind__ = FunctionKind(builder, metadata)
    return decorator


def function_kind(decorator):
    """Return registered function metadata, without resolving namespace names."""
    return getattr(decorator, "__tvm_function_kind__", None)


@contextmanager
def span_context(span):
    """Use the existing span stack, preserving a failing operation's source range."""
    context = IRBuilder.current().with_source_span(span) if span is not None else nullcontext()
    try:
        with context:
            yield
    except Exception as error:
        if span is not None and not hasattr(error, "__tvm_script_span__"):
            error.__tvm_script_span__ = span
        raise


def at(span, value):
    """Attach a source range to an expression and preserve ordinary Python values."""
    if span is not None and isinstance(value, ir.Expr):
        with span_context(span):
            return IRBuilder.current()._set_current_source_span(value)
    return value


_at = at


def frame_result(frame):
    """Read finalized lexical exports without imposing a dialect policy."""
    return getattr(frame, "result", {})


def require_defined(value, name):
    """Reject reads of names absent from a finalized lexical export set."""
    if value is MISSING:
        raise NameError(f"name {name!r} is not defined")
    return value


def is_python_bool(value):
    """Identify an ordinary condition that selects a construction-time branch."""
    return isinstance(value, bool)


def compare_chain(logical_and, operands, comparisons):
    """Evaluate each operand once, stopping after an ordinary false comparison."""
    left = operands[0]()
    result = True
    for index, comparison in enumerate(comparisons):
        right = operands[index + 1]()
        current = comparison(left, right)
        result = current if index == 0 else logical_and(result, current)
        if isinstance(result, bool) and not result:
            return False
        left = right
    return result


def logical_chain(operation, operands, short_circuit):
    """Preserve ordinary boolean short-circuiting while constructing symbolic operands."""
    result = operands[0]()
    for operand in operands[1:]:
        if isinstance(result, bool) and result is short_circuit:
            return result
        result = operation(result, operand())
    return result


def select_lazy(operation, condition, true_value, false_value):
    """Select one ordinary boolean arm, or construct both symbolic arms."""
    if isinstance(condition, bool):
        return true_value() if condition else false_value()
    return operation(condition, true_value(), false_value())


def register_call_kind(builder, value_type, adapter):
    """Register a concrete callable's construction policy on its builder namespace."""
    policies = dict(getattr(builder, "__tvm_call_kinds__", {}))
    policies[value_type] = adapter
    builder.__tvm_call_kinds__ = policies


def callee(builder, value):
    """Resolve an already-evaluated callable without altering global call behavior."""
    for value_type, adapter in getattr(builder, "__tvm_call_kinds__", {}).items():
        if isinstance(value, value_type):
            return adapter(value)
    return value
