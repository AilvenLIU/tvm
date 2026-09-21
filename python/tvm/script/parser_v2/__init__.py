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
"""Translate original Python ASTs into calls on a registered construction namespace.

Shared I infrastructure owns builder lifetime, spans and module identities; each
context selects X from reverse-registered function metadata. Calls return concrete
values, constructor metadata describes syntax, and compiled AST locations remain
those of the original source. Entry modules register policies; this parser never
imports their namespaces.
"""

import importlib
import sys

# Process-wide fixed export names, never per-function construction state. Lazy
# entry loading permits builder imports to register syntax policy without a
# parser/frontend/builder-construction import cycle.
_FRONTEND_EXPORTS = (
    "_NAMESPACES",
    "from_source",
    "ir_module",
    "make_decorator",
    "make_helper",
    "parse",
    "pyfunc",
    "register_namespace",
)
__all__ = [name for name in _FRONTEND_EXPORTS if not name.startswith("_")]


_initialized = False


def _initialize():
    """Register existing dialect entry identities for explicit v2 parsing only."""
    global _initialized
    if _initialized:
        return
    from tvm.script.parser import frontend as legacy
    from tvm.script.ir_builder import construction
    from tvm.tirx.script import builder as tir_builder
    from tvm.tirx.script.builder import v2 as tir_v2
    from tvm.relax import script as relax_entry
    from tvm.relax.script import builder as relax_builder
    from tvm.relax.script.builder import v2 as relax_v2
    from . import frontend, protocol

    protocol.register_builder(tir_builder, tir_v2)
    protocol.register_builder(relax_builder, relax_v2)
    frontend._NAMESPACES.update(legacy._NAMESPACES)
    construction.register_opaque_factory(
        relax_entry._opaque_function, module_adapter=relax_entry._python_module
    )
    _initialized = True


def __getattr__(name):
    if name in _FRONTEND_EXPORTS:
        _initialize()
        frontend = importlib.import_module(f"{__name__}.frontend")
        return getattr(frontend, name)
    frontend = sys.modules.get(f"{__name__}.frontend")
    if frontend is not None and name in getattr(frontend, "_NAMESPACES", {}):
        return frontend._NAMESPACES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
