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
"""Opt-in TVMScript entry point using concrete TIRx construction operations."""

# pylint: disable=wildcard-import,unused-wildcard-import,redefined-builtin
import sys as _sys

from tvm.script.parser_v2.frontend import make_decorator as _make_decorator
from tvm.script.parser_v2.frontend import make_helper as _make_helper
from tvm.script.parser_v2.frontend import register_namespace as _register_namespace
from tvm.tirx.layout import Axis as _Axis

from . import builder_v2 as _builder
from . import tile as _tile
from .builder_v2 import *  # noqa: F403
from .tile import cluster as cluster
from .tile import cta as cta
from .tile import thread as thread
from .tile import warp as warp
from .tile import warpgroup as warpgroup
from .tile import wg as wg

tile = _tile
prim_func = _make_decorator(
    _builder, option_map={"private": "private", "s_tir": "s_tir", "persistent": "persistent"}
)
inline = _make_helper(_builder, preserve_return=True)
macro = _make_helper(_builder, preserve_return=False)

_register_namespace("T", _sys.modules[__name__])
_register_namespace("tirx", _sys.modules[__name__])

_register_namespace("Axis", _Axis)
