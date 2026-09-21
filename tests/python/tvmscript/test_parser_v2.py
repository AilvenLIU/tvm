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
"""Small, namespace-independent checks of the explicit construction protocol."""

import ast
import copy
import itertools
import re
import textwrap
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from tvm import ir
from tvm.script import parser_v2
from tvm.script.ir_builder import IRBuilder, protocol
from tvm.script.parser_v2.frontend import Compiler, make_decorator
from tvm.script.parser_v2.transform import Transformer


class _Recorder:
    """Record protocol calls; values keep their ordinary Python behavior."""

    def __init__(self):
        self.bindings = {}
        self.returned = []

    def bind_(self, value=protocol.MISSING, **metadata):
        self.bindings[metadata.get("name")] = (value, metadata)
        return value

    def emit_(self, value, **metadata):
        pass

    def return_(self, value, **metadata):
        self.returned.append(value)

    def setitem(self, target, key, value, **metadata):
        target[key] = value

    def setattr(self, target, name, value, **metadata):
        setattr(target, name, value)

    def unpack(self, value):
        return value


def _registered(source, env=None):
    recorder = _Recorder()
    namespace = {"D": SimpleNamespace(function=make_decorator(recorder)), **(env or {})}
    compiler = Compiler(textwrap.dedent(source), namespace, filename="<protocol-test>")
    function = compiler.tree.body[0]
    kind, _ = compiler.function_kind(function, compiler.env)
    assert kind.builder is recorder
    return compiler, function, kind.builder


def _run(compiler, function, builder):
    with IRBuilder():
        compiler.run_statements(
            function.body,
            builder,
            compiler.env,
            {argument.arg for argument in function.args.args},
        )


def test_source_translation():
    source = """\
        @D.function
        def f(x):
            y = x * x + 1
            return y
    """
    compiler, function, _ = _registered(source)
    original = ast.dump(function, include_attributes=True)
    counter = itertools.count()
    transformer = Transformer(
        compiler.filename,
        compiler.env,
        "X",
        "I",
        lambda node: ast.Name("S", ast.Load()),
        lambda prefix: f"_{prefix}_{next(counter)}",
        {"x"},
    )
    generated = copy.deepcopy(function)
    generated.decorator_list = []
    generated.body = transformer.transform_statements(function.body)
    actual = ast.unparse(ast.fix_missing_locations(generated))
    expected = """\
def f(x):
    with I.span_context(S):
        _value_0 = I._at(S, I._at(S, I._at(S, x) * I._at(S, x)) + I._at(S, 1))
        y = X.bind_(_value_0, span=S, name='y', name_span=S)
    with I.span_context(S):
        X.return_(I._at(S, y), span=S)"""
    assert actual == expected
    assert ast.dump(function, include_attributes=True) == original


def test_assignment_order_and_hierarchical_unpack():
    events = []

    class Target(dict):
        def __getitem__(self, key):
            events.append("read")
            return super().__getitem__(key)

        def __setitem__(self, key, value):
            events.append(("store", value))
            super().__setitem__(key, value)

        @property
        def field(self):
            return self[0]

        @field.setter
        def field(self, value):
            self[0] = value

    target = Target()

    def base():
        events.append("base")
        return target

    def index():
        events.append("index")
        return 0

    def value():
        events.append("value")
        return 7

    compiler, function, builder = _registered(
        """\
        @D.function
        def f():
            base()[index()] = value()
            base()[index()] += value()
            base().field = value()
            base().field += value()
            base()[index()], (a, b) = (1, (2,))
        """,
        {"base": base, "index": index, "value": value},
    )
    with pytest.raises(ValueError, match="unpack"):
        _run(compiler, function, builder)
    assert events == [
        "value",
        "base",
        "index",
        ("store", 7),
        "base",
        "index",
        "read",
        "value",
        ("store", 14),
        "value",
        "base",
        ("store", 7),
        "base",
        "read",
        "value",
        ("store", 14),
        "base",
        "index",
        ("store", 1),
    ]
    assert builder.bindings == {}


def test_callable_metadata_and_missing_initializer():
    def scalar(expr=None):
        return object() if expr is None else expr

    protocol.register_declaration(scalar)
    previous = object()
    compiler, function, builder = _registered(
        """\
        @D.function
        def f(n):
            n = alias()
            absent: int
            explicit = None
        """,
        {"alias": scalar, "n": previous},
    )
    _run(compiler, function, builder)
    _, metadata = builder.bindings["n"]
    assert metadata["declaration"] is True
    assert metadata["previous"] is previous
    assert builder.bindings["absent"][0] is protocol.MISSING
    assert builder.bindings["explicit"][0] is None


def test_finalized_exports_define_scope():
    exported = object()

    class Scope:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.result = {"visible": exported}

    compiler, function, builder = _registered(
        """\
        @D.function
        def f():
            with scope:
                visible = 1
                hidden = 2
            return visible
            hidden
        """,
        {"scope": Scope()},
    )
    with pytest.raises(NameError, match="hidden"):
        _run(compiler, function, builder)
    assert builder.returned == [exported]


def test_original_nested_locations_and_spans():
    source = """\
        @D.function
        def f(x):
            y = x * x + 1
            return y
    """
    compiler, function, builder = _registered(source, {"x": ir.Var("x", "int32")})
    _run(compiler, function, builder)
    addition = builder.returned[0]
    assert (addition.span.line, addition.span.column, addition.span.end_column) == (3, 8, 17)
    multiply = addition.a
    assert (multiply.span.line, multiply.span.column, multiply.span.end_column) == (3, 8, 13)

    class Operand:
        def __mul__(self, other):
            raise ValueError("multiply failed")

    compiler, function, builder = _registered(source, {"x": Operand()})
    with pytest.raises(ValueError, match="multiply failed") as error:
        _run(compiler, function, builder)
    original = [
        frame
        for frame in traceback.extract_tb(error.value.__traceback__)
        if frame.filename == compiler.filename
    ][-1]
    assert original.lineno == 3
    assert original.line == "y = x * x + 1"
    if hasattr(original, "colno"):
        assert (original.colno, original.end_colno) == (8, 13)


def test_shared_parser_dependency_direction():
    forbidden = {"tir", "tirx", "s_tir", "relax"}
    violations = []
    for path in sorted(Path(parser_v2.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            values = []
            if isinstance(node, ast.Import):
                values = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                values = [node.module or "", *(alias.name for alias in node.names)]
            elif isinstance(node, ast.Name):
                values = [node.id]
            elif isinstance(node, ast.Attribute):
                values = [node.attr]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                values = [node.value]
            if any(
                forbidden.intersection(re.findall(r"[A-Za-z_]\w*", value.lower()))
                for value in values
            ):
                violations.append(f"{path.name}:{node.lineno}: {values}")
    assert not violations, "Shared parsing must consume registered policies:\n" + "\n".join(
        violations
    )
