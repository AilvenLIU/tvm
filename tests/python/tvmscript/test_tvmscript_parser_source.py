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
# ruff: noqa: F401
"""Source locations in TVMScript parsing"""

import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest
import tvm_ffi
from tvm_ffi import structural_walk

import tvm
import tvm.testing
from tvm.ir import Call, SequentialSpan, TensorLoad, assert_structural_equal
from tvm.script import tirx as T
from tvm.script.tirx import tile as Tx
from tvm.tirx.stmt import TilePrimitiveCall


def _tirx_source(func):
    """Leave a function intact while marking its source as TIRx."""
    return func


_tirx_source.dispatch_token = "tirx"


def matmul(a: T.handle, b: T.handle, c: T.handle) -> None:
    A = T.match_buffer(a, [128, 128])
    B = T.match_buffer(b, [128, 128])
    C = T.match_buffer(c, [128, 128])
    for i, j, k in T.grid(128, 128, 128):
        with T.sblock("update"):
            vi, vj, vk = T.axis.remap("SSR", [i, j, k])
            C[vi, vj] = C[vi, vj] + A[vi, vk] * B[vj, vk]


def _source(function):
    lines, start = inspect.getsourcelines(function)
    indentation = len(lines[0]) - len(lines[0].lstrip())
    tree = ast.parse(textwrap.dedent("".join(lines)))
    ast.increment_lineno(tree, start - 1)
    for node in ast.walk(tree):
        if hasattr(node, "col_offset"):
            node.col_offset += indentation
        if getattr(node, "end_col_offset", None) is not None:
            node.end_col_offset += indentation
    return SimpleNamespace(filename=inspect.getsourcefile(function), tree=tree)


def _source_range(source, node):
    return (source.filename, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)


def _span_range(span):
    return (
        span.source_name.name,
        span.line,
        span.column,
        span.end_line,
        span.end_column,
    )


def _find_ir_node(func, predicate):
    nodes = []
    structural_walk(func.body, nodes.append, order="post")
    matches = [node for node in nodes if predicate(node)]
    assert len(matches) == 1
    return matches[0]


def test_parser_attaches_span_to_direct_call():
    @_tirx_source
    def direct_call():
        T.device_entry()
        barriers = T.alloc_buffer((1,), "uint64", scope="shared")
        T.cuda.mbarrier_wait(
            T.address_of(barriers[0]),
            0,
        )

    source = _source(direct_call)
    call_ast = source.tree.body[0].body[-1].value
    func = T.prim_func(direct_call)
    call = _find_ir_node(
        func,
        lambda node: (
            isinstance(node, Call) and getattr(node.op, "name", None) == "tirx.cuda.mbarrier_wait"
        ),
    )

    assert _span_range(call.span) == _source_range(source, call_ast)


def test_parser_attaches_span_to_nested_tensor_load():
    @_tirx_source
    def nested_load():
        source_buffer = T.alloc_buffer((1,), "int32")
        output = T.alloc_buffer((1,), "int32")
        output[0] = source_buffer[0] + 1

    source = _source(nested_load)
    load_ast = source.tree.body[0].body[-1].value.left
    func = T.prim_func(nested_load)
    load = _find_ir_node(
        func,
        lambda node: (
            isinstance(node, TensorLoad) and getattr(node.source, "name", None) == "source_buffer"
        ),
    )

    assert _span_range(load.span) == _source_range(source, load_ast)


def test_parser_retains_inline_call_site_and_definition_spans():
    def wait_impl(barrier):
        T.cuda.mbarrier_wait(barrier, 0)

    wait_source = _source(wait_impl)
    wait_call_ast = wait_source.tree.body[0].body[0].value
    wait = T.inline(wait_impl)

    @_tirx_source
    def inline_call():
        T.device_entry()
        barriers = T.alloc_buffer((1,), "uint64", scope="shared")
        wait(T.address_of(barriers[0]))

    caller_source = _source(inline_call)
    caller_call_ast = caller_source.tree.body[0].body[-1].value
    func = T.prim_func(inline_call)
    call = _find_ir_node(
        func,
        lambda node: (
            isinstance(node, Call) and getattr(node.op, "name", None) == "tirx.cuda.mbarrier_wait"
        ),
    )

    assert isinstance(call.span, SequentialSpan)
    assert [_span_range(span) for span in call.span.spans] == [
        _source_range(caller_source, caller_call_ast),
        _source_range(wait_source, wait_call_ast),
    ]


def test_parser_attaches_span_to_tile_primitive_call():
    @_tirx_source
    def tile_call():
        A = T.alloc_buffer((16,), "float32")
        Tx.memset(A[0:16], T.float32(0))

    source = _source(tile_call)
    call_ast = source.tree.body[0].body[-1].value
    func = T.prim_func(tile_call)
    call = _find_ir_node(func, lambda node: isinstance(node, TilePrimitiveCall))

    assert _span_range(call.span) == _source_range(source, call_ast)


def test_parser_spans_do_not_affect_structural_identity():
    source_a = """@T.prim_func\ndef f():\n    T.evaluate(1)\n"""
    source_b = """\n\n@T.prim_func\ndef f():\n    T.evaluate(1)\n"""

    func_a = tvm.script.from_source(source_a)
    func_b = tvm.script.from_source(source_b)

    assert _span_range(func_a.body.span) == ("<str>", 3, 4, 3, 17)
    assert _span_range(func_b.body.span) == ("<str>", 5, 4, 5, 17)
    assert tvm_ffi.structural_hash(func_a) == tvm_ffi.structural_hash(func_b)
    assert_structural_equal(func_a, func_b)


def test_nesting_parsing():
    class dummy:
        pass

    for i in range(1):

        @tvm.script.ir_module
        class Module:
            @T.prim_func(s_tir=True)
            def impl(
                A: T.Buffer((12, 196, 64), "float32"),
            ) -> None:
                T.evaluate(0)


if __name__ == "__main__":
    tvm.testing.main()
