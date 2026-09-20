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

from contextlib import nullcontext
from dataclasses import dataclass
from inspect import signature
from typing import Any

from tvm import ir

from .base import IRBuilder


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


def expression_args(*fields, introduce=False, dtype=None):
    """Mark constructor fields whose nested strings are source expressions.

    ``introduce`` permits signature/match scopes to introduce otherwise unknown
    names. ``dtype`` optionally selects the dialect's symbol-construction policy;
    it is metadata, never an evaluator or a replacement constructor.
    """

    def decorate(constructor):
        parameters = signature(constructor).parameters
        unknown = set(fields).difference(parameters)
        if unknown:
            raise ValueError(f"Unknown expression argument fields: {sorted(unknown)}")
        constructor.__tvm_expression_args__ = (tuple(fields), bool(introduce), dtype)
        return constructor

    return decorate


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


def span_context(span):
    """Use the existing builder's source-span stack for an eager operation."""
    return IRBuilder.current().with_source_span(span) if span is not None else nullcontext()


def at(span, value):
    """Attach a source range to an expression and preserve ordinary Python values."""
    if span is not None and isinstance(value, ir.Expr):
        with span_context(span):
            return IRBuilder.current()._set_current_source_span(value)
    return value


_at = at
