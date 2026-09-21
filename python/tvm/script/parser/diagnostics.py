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
"""Source-located construction errors without replacing their original traceback."""

import linecache
import traceback

from tvm.error import DiagnosticError


def diagnostic_error(error, compiler):
    """Render the innermost original operation and retain the original cause."""
    filename = compiler.filename
    span = getattr(error, "__tvm_script_span__", None)
    if span is not None:
        start, end = span.line, span.end_line
        column, end_column = span.column, span.end_column
    elif isinstance(error, SyntaxError) and error.lineno:
        start = error.lineno
        end = error.end_lineno or start
        column = max((error.offset or 1) - 1, 0)
        end_column = max((error.end_offset or column + 2) - 1, column + 1)
    else:
        frames = [
            frame
            for frame in traceback.extract_tb(error.__traceback__)
            if frame.filename == filename
        ]
        if frames:
            frame = frames[-1]
            start, end = frame.lineno, getattr(frame, "end_lineno", None) or frame.lineno
            column = getattr(frame, "colno", None) or 0
            end_column = getattr(frame, "end_colno", None)
        else:
            node = compiler.tree.body[-1]
            start, end = node.lineno, node.lineno
            column, end_column = node.col_offset, None
    lines = [f"{filename}:{start}: {type(error).__name__}: {error}"]
    for number in range(start, end + 1):
        source = linecache.getline(filename, number).rstrip("\n")
        first = column if number == start else len(source) - len(source.lstrip())
        last = end_column if number == end and end_column is not None else len(source)
        lines.extend(
            (
                f" {number} | {source}",
                " " * (len(str(number)) + 4 + first) + "^" * max(last - first, 1),
            )
        )
    return DiagnosticError("\n".join(lines))
