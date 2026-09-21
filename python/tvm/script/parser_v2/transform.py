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
"""Translate Python syntax into calls on a context-selected construction namespace.

The translator owns ordering, lexical scopes, and original source locations.
Construction operations and callable syntax metadata come from its environment;
ordinary expressions remain nested Python expressions producing concrete values.
"""

import ast
import copy
import inspect


class Transformer(ast.NodeTransformer):
    """Lower statements without importing or discovering construction namespaces."""

    def __init__(
        self,
        filename,
        environment,
        builder_name,
        infrastructure_name,
        span,
        fresh,
        signature_names=None,
        expression_rewriter=None,
        nested_function=None,
        preserve_return=False,
        signature_values=None,
    ):
        self.filename = filename
        self.environment = dict(environment)
        self.builder_name = builder_name
        self.infrastructure_name = infrastructure_name
        self.span = span
        self.fresh = fresh
        self.signature_names = set(signature_names or ())
        self.signature_values = dict(signature_values or {})
        self.bound = set(self.signature_names)
        self.optional = {}
        self.expression_rewriter = expression_rewriter
        self.nested_function = nested_function
        self.preserve_return = preserve_return

    def transform_statements(self, body):
        """Transform a copy, retaining the caller's original source tree."""
        result = []
        for statement in copy.deepcopy(body):
            translated = self.visit(statement)
            if translated is not None:
                block = translated if isinstance(translated, list) else [translated]
                context = self._call(
                    self.infrastructure_name, "span_context", [self.span(statement)], statement
                )
                result.append(self._with(context, block, statement))
        return result

    def _error(self, node, message):
        raise SyntaxError(message, (self.filename, node.lineno, node.col_offset + 1, None))

    @staticmethod
    def _located(value, original):
        return ast.copy_location(value, original)

    def _name(self, name, original, store=False):
        return self._located(ast.Name(name, ast.Store() if store else ast.Load()), original)

    def _attribute(self, namespace, member, original):
        return self._located(
            ast.Attribute(self._name(namespace, original), member, ast.Load()), original
        )

    def _call(self, namespace, member, args, original, **keywords):
        return self._located(
            ast.Call(
                self._attribute(namespace, member, original),
                args,
                [ast.keyword(arg=key, value=value) for key, value in keywords.items()],
            ),
            original,
        )

    def _operation(self, member, args, original, **keywords):
        return self._call(
            self.builder_name, member, args, original, span=self.span(original), **keywords
        )

    def _statement(self, expression, original):
        return self._located(ast.Expr(expression), original)

    def _assign(self, name, value, original):
        return self._located(ast.Assign([self._name(name, original, True)], value), original)

    def _cache(self, value, original, prefix="value"):
        name = self.fresh(prefix)
        return self._assign(name, value, original), self._name(name, original)

    def _resolve(self, node):
        if isinstance(node, ast.Name):
            return self.environment.get(node.id)
        if isinstance(node, ast.Attribute):
            owner = self._resolve(node.value)
            if owner is None:
                return None
            value = inspect.getattr_static(owner, node.attr, None)
            if isinstance(value, staticmethod):
                return value.__func__
            if inspect.isfunction(value):
                return (
                    value
                    if inspect.ismodule(owner) or inspect.isclass(owner)
                    else value.__get__(owner)
                )
            # Inspect syntax metadata without executing a source-level descriptor.
            if hasattr(type(value), "__get__"):
                return None
            return value
        return None

    def _is_declaration(self, node):
        if not isinstance(node, ast.Call):
            return False
        constructor = self._resolve(node.func)
        policy = getattr(constructor, "__tvm_declaration_args__", None)
        if policy is None or any(isinstance(arg, ast.Starred) for arg in node.args):
            return False
        if any(keyword.arg is None for keyword in node.keywords):
            return False
        try:
            arguments = inspect.signature(constructor).bind_partial(
                *node.args, **{keyword.arg: keyword.value for keyword in node.keywords}
            )
        except (TypeError, ValueError):
            return False
        value = arguments.arguments.get(policy.value_parameter)
        return value is None or (isinstance(value, ast.Constant) and value.value is None)

    def _expression(self, original, *, attach_span=True):
        node = copy.deepcopy(original)
        if isinstance(getattr(node, "ctx", None), ast.Store):
            return node
        if isinstance(node, ast.Name) and node.id in self.optional:
            value = self._call(
                self.infrastructure_name,
                "require_defined",
                [copy.deepcopy(self.optional[node.id]), ast.Constant(node.id)],
                node,
            )
            return self._call(
                self.infrastructure_name, "_at", [self.span(original), value], original
            )
        if self.expression_rewriter is not None:
            node = self.expression_rewriter(node)
        if isinstance(node, ast.Await | ast.Yield | ast.YieldFrom | ast.NamedExpr):
            self._error(original, f"Unsupported expression: {type(node).__name__}")
        if isinstance(node, ast.JoinedStr):
            # JoinedStr's children must remain literal fragments/FormattedValue nodes.
            for child in node.values:
                if isinstance(child, ast.FormattedValue):
                    child.value = self._expression(child.value)
                    if child.format_spec is not None:
                        child.format_spec = self._format_spec(child.format_spec)
        else:
            self._expression_children(node)
        if isinstance(node, ast.Starred) or (
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        ):
            return node
        if not attach_span:
            return node
        return self._call(self.infrastructure_name, "_at", [self.span(original), node], original)

    def _format_spec(self, node):
        for child in node.values:
            if isinstance(child, ast.FormattedValue):
                child.value = self._expression(child.value)
                if child.format_spec is not None:
                    child.format_spec = self._format_spec(child.format_spec)
        return node

    def _expression_children(self, node):
        for field, value in ast.iter_fields(node):
            if isinstance(value, ast.expr):
                setattr(node, field, self._expression(value))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, ast.expr):
                        value[index] = self._expression(item)
                    elif isinstance(item, ast.AST):
                        self._expression_children(item)
            elif isinstance(value, ast.AST):
                self._expression_children(value)

    def _index(self, node):
        if isinstance(node, ast.Slice):
            fields = [
                self._expression(value) if value is not None else ast.Constant(None)
                for value in (node.lower, node.upper, node.step)
            ]
            return self._call(self.infrastructure_name, "slice", fields, node)
        if isinstance(node, ast.Tuple):
            return self._located(
                ast.Tuple([self._index(value) for value in node.elts], ast.Load()), node
            )
        return self._expression(node)

    def _bind(self, target, value, statement, ty=None, declaration=False, frame_value=False):
        if isinstance(target, ast.Name):
            keywords = {"name": ast.Constant(target.id), "name_span": self.span(target)}
            if frame_value:
                keywords["frame_value"] = ast.Constant(True)
            if ty is not None:
                keywords["ty"] = ty
            if declaration:
                keywords["declaration"] = ast.Constant(True)
                if target.id in self.signature_names:
                    keywords["previous"] = copy.deepcopy(
                        self.signature_values.get(target.id, self._name(target.id, target))
                    )
            elif target.id in self.bound:
                keywords["previous"] = self._name(target.id, target)
            elif target.id in self.optional:
                keywords["previous"] = copy.deepcopy(self.optional[target.id])
            self.bound.add(target.id)
            self.optional.pop(target.id, None)
            return [
                self._assign(
                    target.id, self._operation("bind_", [value], statement, **keywords), target
                )
            ]
        if isinstance(target, ast.Subscript):
            return [
                self._statement(
                    self._operation(
                        "setitem",
                        [self._expression(target.value), self._index(target.slice), value],
                        statement,
                    ),
                    statement,
                )
            ]
        if isinstance(target, ast.Tuple | ast.List):
            # Each Python unpack finishes before visiting that level's targets. A nested
            # unpack occurs only when reached, preserving assignment and failure order.
            names = [self.fresh("unpack") for _ in target.elts]
            pattern = []
            for item, name in zip(target.elts, names):
                temporary = self._name(name, item, True)
                pattern.append(
                    self._located(ast.Starred(temporary, ast.Store()), item)
                    if isinstance(item, ast.Starred)
                    else temporary
                )
            unpack = self._call(self.builder_name, "unpack", [value], target)
            assignment = self._located(
                ast.Assign([ast.Tuple(pattern, ast.Store())], unpack), target
            )
            result = [assignment]
            for item, name in zip(target.elts, names):
                item = item.value if isinstance(item, ast.Starred) else item
                result.extend(
                    self._bind(item, self._name(name, item), statement, frame_value=frame_value)
                )
            return result
        self._error(target, f"Unsupported assignment target: {type(target).__name__}")

    def visit_Assign(self, node):
        # Cache first: stores evaluate RHS before target base/index, and chained
        # assignments share exactly one RHS evaluation.
        declaration = self._is_declaration(node.value)
        cache, value = self._cache(
            self._expression(node.value, attach_span=not declaration), node.value
        )
        result = [cache]
        resolved = self._resolve(node.value)
        for target in node.targets:
            result.extend(self._bind(target, copy.deepcopy(value), node, declaration=declaration))
            if isinstance(target, ast.Name):
                if resolved is not None:
                    self.environment[target.id] = resolved
                else:
                    self.environment.pop(target.id, None)
        return result

    def visit_AnnAssign(self, node):
        if not isinstance(node.target, ast.Name):
            self._error(node.target, "An annotated binding requires a name")
        result = []
        if node.value is None:
            value = self._attribute(self.infrastructure_name, "MISSING", node)
        else:
            cache, value = self._cache(
                self._expression(node.value, attach_span=not self._is_declaration(node.value)),
                node.value,
            )
            result.append(cache)
        result.extend(
            self._bind(
                node.target,
                value,
                node,
                self._expression(node.annotation),
                self._is_declaration(node.value),
            )
        )
        return result

    def visit_AugAssign(self, node):
        result = []
        if isinstance(node.target, ast.Name):
            old, previous = self._cache(
                self._expression(self._located(ast.Name(node.target.id, ast.Load()), node.target)),
                node.target,
                "old",
            )
            result.append(old)
            value = self._located(ast.BinOp(previous, node.op, self._expression(node.value)), node)
            value = self._call(self.infrastructure_name, "_at", [self.span(node), value], node)
            return result + self._bind(node.target, value, node)
        if not isinstance(node.target, ast.Subscript):
            self._error(node.target, "An augmented assignment requires a name or index")
        base_stmt, base = self._cache(
            self._expression(node.target.value), node.target.value, "base"
        )
        key_stmt, key = self._cache(self._index(node.target.slice), node.target.slice, "key")
        load = self._located(
            ast.Subscript(copy.deepcopy(base), copy.deepcopy(key), ast.Load()), node.target
        )
        old_stmt, old = self._cache(
            self._call(
                self.infrastructure_name, "_at", [self.span(node.target), load], node.target
            ),
            node.target,
            "old",
        )
        result.extend([base_stmt, key_stmt, old_stmt])
        value = self._located(ast.BinOp(old, node.op, self._expression(node.value)), node)
        value = self._call(self.infrastructure_name, "_at", [self.span(node), value], node)
        result.append(self._statement(self._operation("setitem", [base, key, value], node), node))
        return result

    def visit_Expr(self, node):
        return self._statement(self._operation("emit_", [self._expression(node.value)], node), node)

    def visit_Return(self, node):
        value = self._expression(node.value) if node.value is not None else ast.Constant(None)
        if self.preserve_return:
            return self._located(ast.Return(value), node)
        return self._statement(self._operation("return_", [value], node), node)

    def visit_Break(self, node):
        return self._statement(self._operation("break_", [], node), node)

    def visit_Continue(self, node):
        return self._statement(self._operation("continue_", [], node), node)

    def visit_Assert(self, node):
        message = self._expression(node.msg) if node.msg is not None else ast.Constant("")
        return self._statement(
            self._operation("assert_", [self._expression(node.test), message], node), node
        )

    @staticmethod
    def _assigned_names(body):
        names = set()

        class Names(ast.NodeVisitor):
            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Store):
                    names.add(node.id)

            def visit_FunctionDef(self, node):
                names.add(node.name)

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Lambda(self, node):
                pass

        visitor = Names()
        for statement in body:
            visitor.visit(statement)
        return names

    def _scope(self, body, original, prefix, initial=None):
        outer_bound, outer_environment, outer_optional = self.bound, self.environment, self.optional
        referenced = {
            node.id
            for statement in body
            for node in ast.walk(statement)
            if isinstance(node, ast.Name)
        }
        captures = sorted((outer_bound | outer_optional.keys()).intersection(referenced))
        self.bound, self.environment = set(outer_bound), dict(outer_environment)
        self.optional = {
            name: self._name(name, original)
            if name in captures and not self.preserve_return
            else value
            for name, value in outer_optional.items()
        }
        prefix_statements = [] if initial is None else initial()
        translated = prefix_statements + self.transform_statements(body)
        self.bound, self.environment, self.optional = outer_bound, outer_environment, outer_optional
        if self.preserve_return:
            return translated or [self._located(ast.Pass(), original)]
        # A helper isolates construction locals. Optional exports are captured as
        # values or MISSING, and checked only when the original body reads them.
        defaults = [
            self._name(name, original)
            if name in outer_bound
            else copy.deepcopy(outer_optional[name])
            for name in captures
        ]
        helper = self.fresh(prefix)
        arguments = ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=name) for name in captures],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=defaults,
        )
        definition = self._located(
            ast.FunctionDef(
                helper, arguments, translated or [self._located(ast.Pass(), original)], [], None
            ),
            original,
        )
        if "type_params" in ast.FunctionDef._fields:
            definition.type_params = []
        invocation = self._located(ast.Call(self._name(helper, original), [], []), original)
        return [definition, self._statement(invocation, original)]

    def _exports(self, frame, candidates, original):
        mapping_stmt, mapping = self._cache(
            self._call(
                self.infrastructure_name, "frame_result", [self._name(frame, original)], original
            ),
            original,
            "exports",
        )
        result = [mapping_stmt]
        for name in sorted(candidates):
            key = ast.Constant(name)
            condition = self._located(
                ast.Compare(copy.deepcopy(key), [ast.In()], [copy.deepcopy(mapping)]), original
            )
            value = self._located(ast.Subscript(copy.deepcopy(mapping), key, ast.Load()), original)
            result.append(
                self._located(
                    ast.If(condition, [self._assign(name, value, original)], []), original
                )
            )
        for name in candidates - self.bound:
            self.optional[name] = self._located(
                ast.Call(
                    ast.Attribute(copy.deepcopy(mapping), "get", ast.Load()),
                    [
                        ast.Constant(name),
                        self._attribute(self.infrastructure_name, "MISSING", original),
                    ],
                    [],
                ),
                original,
            )
        return result

    def _with(self, context, body, original, target=None):
        return self._located(
            ast.With([ast.withitem(context, target)], body or [ast.Pass()]), original
        )

    def visit_If(self, node):
        frame = self.fresh("conditional")
        branches = [
            self._with(
                self._operation("Then", [], node), self._scope(node.body, node, "then"), node
            )
        ]
        if node.orelse:
            branches.append(
                self._with(
                    self._operation("Else", [], node), self._scope(node.orelse, node, "else"), node
                )
            )
        region = self._with(
            self._operation("If", [self._expression(node.test)], node),
            branches,
            node,
            self._name(frame, node, True),
        )
        return [region, *self._exports(frame, self._assigned_names(node.body + node.orelse), node)]

    def visit_For(self, node):
        if node.orelse:
            self._error(node, "A construction loop does not support an else clause")
        frame, values = self.fresh("loop"), self.fresh("indices")
        context = self._assign(
            frame, self._operation("For", [self._expression(node.iter)], node), node
        )
        body = self._scope(
            node.body,
            node,
            "body",
            lambda: self._bind_entered(node.target, self._name(values, node.target), node),
        )
        region = self._with(self._name(frame, node), body, node, self._name(values, node, True))
        return [context, region, *self._exports(frame, self._assigned_names(node.body), node)]

    def visit_While(self, node):
        if node.orelse:
            self._error(node, "A construction loop does not support an else clause")
        frame = self.fresh("loop")
        region = self._with(
            self._operation("While", [self._expression(node.test)], node),
            self._scope(node.body, node, "body"),
            node,
            self._name(frame, node, True),
        )
        return [region, *self._exports(frame, self._assigned_names(node.body), node)]

    def _bind_entered(self, target, value, original):
        # Context/iteration targets introduce lexical names; they never reassign
        # an outer mutable variable merely because its source spelling matches.
        for item in ast.walk(target):
            if isinstance(item, ast.Name):
                self.bound.discard(item.id)
                self.optional.pop(item.id, None)
        return self._bind(target, value, original, frame_value=True)

    def visit_With(self, node):
        item = node.items[0]
        body = node.body
        if len(node.items) > 1:
            nested = self._located(ast.With(node.items[1:], node.body), node)
            body = [nested]
        manager, value = self.fresh("context"), self.fresh("entered")
        cache = self._assign(manager, self._expression(item.context_expr), item.context_expr)
        initial = (
            None
            if item.optional_vars is None
            else lambda: self._bind_entered(
                item.optional_vars, self._name(value, item.context_expr), node
            )
        )
        region = self._with(
            self._name(manager, node),
            self._scope(body, node, "scope", initial),
            node,
            self._name(value, node, True),
        )
        return [cache, region, *self._exports(manager, self._assigned_names(body), node)]

    def visit_FunctionDef(self, node):
        self.bound.add(node.name)
        if self.nested_function is not None:
            return self.nested_function(node)
        # Ordinary construction helpers retain normal Python execution. Registered
        # function-kind entry points are resolved by the enclosing compiler callback.
        if any(
            getattr(self._resolve(decorator), "__tvm_function_kind__", None)
            for decorator in node.decorator_list
        ):
            self._error(node, "A registered nested function requires a function compiler")
        return node

    def visit_Pass(self, node):
        return node

    def generic_visit(self, node):
        if isinstance(node, ast.stmt):
            self._error(node, f"Unsupported statement: {type(node).__name__}")
        return super().generic_visit(node)
