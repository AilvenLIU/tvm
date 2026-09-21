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
"""Function-group construction and opaque Python member registration."""

import ast
import copy
import dis
import inspect
import linecache
from dataclasses import dataclass
from types import CodeType

from tvm.script.ir_builder import ir as I

_OPAQUE_FACTORY = None
_MODULE_ADAPTER = None


def register_opaque_factory(factory, *, module_adapter=None):
    """Register the owner-provided constructor for a module's opaque function slot.

    The factory receives ``(name, python_callable, source_text, span)`` and must
    return a concrete BaseFunc. The optional adapter receives a completed module,
    its original class (if available), and its resolved base classes. Registration
    never executes the Python body.
    """
    global _OPAQUE_FACTORY, _MODULE_ADAPTER
    if not callable(factory):
        raise TypeError("An opaque function factory must be callable")
    if module_adapter is not None and not callable(module_adapter):
        raise TypeError("An opaque module adapter must be callable")
    _OPAQUE_FACTORY = factory
    _MODULE_ADAPTER = module_adapter


def is_python_function(compiler, node, env):
    """Identify a Python function through its registered function-kind metadata."""
    kind, _ = compiler.function_kind(node, env)
    return bool(kind.metadata.get("python"))


def _error(compiler, node, message):
    raise SyntaxError(
        message,
        (
            compiler.filename,
            node.lineno,
            node.col_offset + 1,
            linecache.getline(compiler.filename, node.lineno),
        ),
    )


def materialize_python(compiler, node, env, *, original=None):
    """Retain an existing Python callable, or execute its unchanged definition.

    Source-only module members share ``env``, preserving ordinary global lookup.
    Nested Python definitions should remain directly in the construction helper's
    AST, so Python itself creates their lexical closure cells.
    """
    if original is None and not isinstance(compiler.original, str):
        candidate = env.get(node.name)
        if getattr(candidate, "__tvm_python_function__", False):
            original = candidate
    if original is not None:
        if not callable(original):
            _error(compiler, node, "A Python function definition must retain a callable")
        return original
    module = ast.fix_missing_locations(ast.Module([copy.deepcopy(node)], []))
    exec(
        compile(
            module,
            compiler.filename,
            "exec",
            flags=getattr(compiler, "compile_flags", 0),
            dont_inherit=True,
        ),
        env,
    )
    return env[node.name]


@dataclass(frozen=True)
class PythonFunction:
    """Original Python callable and its separate opaque module reference."""

    name: str
    function: object
    reference: object


def declare_python(compiler, node, env, *, original=None):
    """Construct a real opaque module entry and retain its Python implementation."""
    if _OPAQUE_FACTORY is None:
        _error(compiler, node, "No opaque Python function constructor has been registered")
    function = materialize_python(compiler, node, env, original=original)
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        source = ast.unparse(node)
    opaque = _OPAQUE_FACTORY(node.name, function, source, compiler.span(node))
    reference = I.decl_function(node.name, opaque)
    I.def_function(node.name, opaque)
    return PythonFunction(node.name, function, reference)


def attach_python(module, functions):
    """Attach Python callables to a completed module with opaque entries.

    This preserves construction and Python execution. Device conversion, method
    binding, and runtime registration remain responsibilities of the runtime's
    module adapter; attaching callables alone does not establish those bridges.
    """
    existing = dict(getattr(module, "pyfuncs", {}))
    for function in functions:
        existing[function.name] = function.function
    module.pyfuncs = existing
    return module


def adapt_python_module(module, *, original=None, bases=()):
    """Let the registered owner supply an executable module wrapper if needed."""
    if _MODULE_ADAPTER is None:
        return module
    return _MODULE_ADAPTER(module, original, bases)


def _global_loads(node, filename):
    """Find free sibling uses without confusing attributes or shadowed locals."""
    node = copy.deepcopy(node)
    node.decorator_list = []
    node.returns = None
    for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        argument.annotation = None
    module = ast.fix_missing_locations(ast.Module([node], []))
    code = compile(module, filename, "exec", dont_inherit=True)
    names = set()

    def visit(current):
        for instruction in dis.get_instructions(current):
            if instruction.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                names.add(instruction.argval)
        for value in current.co_consts:
            if isinstance(value, CodeType):
                visit(value)

    # Skip the module's decoration/default expressions: only body references
    # determine whether a local definition uses a still-undefined peer.
    for value in code.co_consts:
        if isinstance(value, CodeType) and value.co_name == node.name:
            visit(value)
    return names


class FunctionGroup:
    """Declare sibling IR signatures before defining any of their bodies.

    An optional ``reserve(name)`` callback allocates stable identities before
    annotation evaluation, when the enclosing module or builder supports it.
    Local definitions retain source ordering and reject forward sibling uses;
    self-recursion uses the function's own completed declaration.
    """

    def __init__(self, compiler, nodes, env, *, local=False, reserve=None):
        self.compiler = compiler
        self.local = local
        self.env = dict(env)
        self.signatures = {}
        self.references = {}
        self.results = {}
        nodes = list(nodes)
        names = {node.name for node in nodes}
        if len(names) != len(nodes):
            duplicate = next(
                node
                for index, node in enumerate(nodes)
                if node.name in {previous.name for previous in nodes[:index]}
            )
            _error(compiler, duplicate, f"Duplicate function declaration {duplicate.name!r}")
        if reserve is not None:
            self.references.update((node.name, reserve(node.name)) for node in nodes)
            self.env.update(self.references)
        for node in nodes:
            signature = compiler.declare(node, self.env, local=local)
            self.signatures[node.name] = signature
            self.references[node.name] = signature.reference
            self.env[node.name] = signature.reference
        for signature in self.signatures.values():
            shadowed = signature.params.keys() | signature.scope.symbols.keys()
            signature.scope.env.update(
                (name, reference)
                for name, reference in self.references.items()
                if name not in shadowed
            )

    def define(self, name, env=None):
        """Define one declared body, returning the cached construction reference."""
        signature = self.signatures[name]
        if name in self.results:
            _error(self.compiler, signature.node, f"Function {name!r} is already defined")
        if self.local:
            unresolved = (
                (_global_loads(signature.node, self.compiler.filename) & self.references.keys())
                - self.results.keys()
                - {name}
            )
            if unresolved:
                _error(
                    self.compiler,
                    signature.node,
                    f"Local function {name!r} refers to undefined sibling "
                    f"{sorted(unresolved)[0]!r}; "
                    "mutually recursive local definitions are unsupported",
                )
        visible = {**self.env, **(env or {}), **self.references}
        self.results[name] = self.compiler.define(signature, visible, local=self.local)
        return self.references[name]

    def define_all(self):
        """Define bodies in source order after every signature has been registered."""
        for name in self.signatures:
            self.define(name)
        return self.results
