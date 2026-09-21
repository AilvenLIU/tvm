..  Licensed to the Apache Software Foundation (ASF) under one
    or more contributor license agreements.  See the NOTICE file
    distributed with this work for additional information
    regarding copyright ownership.  The ASF licenses this file
    to you under the Apache License, Version 2.0 (the
    "License"); you may not use this file except in compliance
    with the License.  You may obtain a copy of the License at

..    http://www.apache.org/licenses/LICENSE-2.0

..  Unless required by applicable law or agreed to in writing,
    software distributed under the License is distributed on an
    "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
    KIND, either express or implied.  See the License for the
    specific language governing permissions and limitations
    under the License.

tvm.script.parser
-----------------

tvm.script.parser
*****************
The shared parser translates Python source into calls on registered construction
namespaces. Dialect script packages provide their decorators and register the
corresponding builder operations. Construction returns concrete IR values, with
support for eager and postponed function annotations.

.. automodule:: tvm.script.parser
   :members:
   :imported-members:

tvm.script.parser.ir
********************
.. automodule:: tvm.script.parser.ir

tvm.relax.script
****************
.. automodule:: tvm.relax.script
   :members: function, macro
   :imported-members:

tvm.tirx.script
***************
.. automodule:: tvm.tirx.script
   :members: prim_func, inline, macro
   :imported-members:

The public aliases ``tvm.script.parser.relax`` and ``tvm.script.parser.tirx``
continue to resolve to these dialect script packages.
