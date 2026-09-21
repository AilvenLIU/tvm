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
"""Build shared operand bindings for eager comparison-chain expressions."""

from tvm import ir


def _comparison_chain(comparisons, operands, conjunction, bind):
    """Bind original chain operands progressively around short-circuit tests.

    All inputs have already been constructed eagerly. The operand tuple retains
    pre-conversion expression identities: comparison overloads may cast one
    middle operand differently on either side. Python object reuse alone does
    not make its compiled value execute once. Fresh IR variables replace those
    identities inside comparisons, and each binding occurs only when its
    comparison is reached. The first two operands evaluate left to right.

    conjunction and bind are builder implementation functions, not lazy source
    callbacks. They construct control flow and a dialect-specific expression
    binding. All maps/variables are local to this construction; no registry,
    native frame, or source expression is mutated.
    """
    import tvm_ffi

    if len(operands) != len(comparisons) + 1:
        raise ValueError("A comparison chain requires one more operand than comparison")
    replacements = []
    bindings = []
    for operand in operands:
        if isinstance(operand, tvm_ffi.ObjectConvertible):
            operand = operand.asobject()
        if not isinstance(operand, ir.Expr) or isinstance(operand, ir.Var):
            bindings.append(None)
            continue
        previous = next((var for value, var in replacements if value.same_as(operand)), None)
        if previous is not None:
            bindings.append(None)
            continue
        variable = ir.Var("chain_operand", operand.ty)
        replacements.append((operand, variable))
        bindings.append((variable, operand))

    def replace(value, mutator):
        for original, variable in replacements:
            if value.same_as(original):
                return variable
        return mutator.default_mutate(value)

    conditions = []
    for comparison in comparisons:
        if isinstance(comparison, tvm_ffi.ObjectConvertible):
            comparison = comparison.asobject()
        conditions.append(
            tvm_ffi.structural_mutate(comparison, [(ir.Expr, replace)])
            if isinstance(comparison, ir.Expr)
            else comparison
        )
    result = conditions[-1]
    for index in range(len(conditions) - 1, -1, -1):
        if index < len(conditions) - 1:
            result = conjunction(conditions[index], result)
        if bindings[index + 1] is not None:
            variable, value = bindings[index + 1]
            result = bind(variable, value, result)
    if bindings[0] is not None:
        variable, value = bindings[0]
        result = bind(variable, value, result)
    return result
