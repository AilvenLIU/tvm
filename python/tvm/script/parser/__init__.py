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
"""Default TVMScript parser entries.

The generic implementation is retained under ``parser_v2`` until the migration
cleanup. Dialect namespaces resolve through the shared script registry.
"""

import importlib
from typing import Any

from ..parser_v2 import ir, ir_module, parse


def __getattr__(name: str) -> Any:
    # Lazy import to avoid loading tvm.script during dialect bootstrap.
    from tvm.script import _DIALECT_REGISTRY  # pylint: disable=import-outside-toplevel

    if name in _DIALECT_REGISTRY:
        module = importlib.import_module(_DIALECT_REGISTRY[name])
        globals()[name] = module
        return module
    raise AttributeError(f"module 'tvm.script.parser' has no attribute {name!r}")
