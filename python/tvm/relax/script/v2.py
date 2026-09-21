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
"""Opt-in TVMScript entry point using concrete Relax construction operations."""

# pylint: disable=wildcard-import,unused-wildcard-import,redefined-builtin
import sys as _sys

from tvm import relax as _relax
from tvm.script.parser_v2.frontend import make_decorator as _make_decorator
from tvm.script.parser_v2.frontend import make_helper as _make_helper
from tvm.script.parser_v2.frontend import register_namespace as _register_namespace
from tvm.script.parser_v2.functions import register_opaque_factory as _register_opaque_factory

from . import builder_v2 as _builder
from .builder_v2 import *  # noqa: F403

function = _make_decorator(_builder, option_map={"pure": "is_pure", "private": "is_private"})
macro = _make_helper(_builder, preserve_return=True)

_register_namespace("R", _sys.modules[__name__])
_register_namespace("relax", _sys.modules[__name__])


def _opaque_function(name, function, source, span):
    return _relax.ExternFunc(name, span=span).with_attrs(
        {
            "is_pyfunc": True,
            "function_type": "python",
            "python_function_name": name,
            "python_source": source,
            "python_packed_func": function,
        }
    )


_register_opaque_factory(_opaque_function)
