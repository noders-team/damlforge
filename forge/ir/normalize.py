# Copyright 2026 Noders Team
# Author: Artem Bogomaz <artembogomaz@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from dataclasses import dataclass, fields, is_dataclass, replace

from forge.exceptions import CompilerPanic, DamlForgeException
from forge.ir.nodes import (
    IRAssert,
    IRAssertLoop,
    IRAssign,
    IRAttribute,
    IRBinOp,
    IRBoolOp,
    IRCall,
    IRCompare,
    IRConstant,
    IRDict,
    IRExpr,
    IRExprStmt,
    IRFold,
    IRFor,
    IRIf,
    IRIfExp,
    IREarlyReturnLoop,
    IRList,
    IRName,
    IRPass,
    IRPureAssertionLoop,
    IRRecordUpdate,
    IRReturn,
    IRStmt,
    IRSubscript,
    IRTuple,
    IRUnaryOp,
)
from forge.models.error_codes import ErrorCodes


@dataclass(frozen=True)
class LoopPlan:
    body: tuple[IRStmt, ...]
    accumulators: tuple[str, ...]
    captured: tuple[str, ...]
    assertion: IRAssert | None
    has_return: bool
    in_update_block: bool
    strategy: str = "drop"


class LoopAnalysis:
    @classmethod
    def normalize_for_analysis(cls, stmts, known_effect_fns: frozenset = frozenset()):
        """Normalize effect expressions before inspecting loop requirements.

        Import analysis must see the same statement shape as code generation:
        effects hidden in ``append`` calls or larger expressions are lifted to
        statements before deciding whether a loop needs ``foldlA``.
        """
        namer = LoopNamer()
        rewritten = cls.rewrite_appends(stmts, namer, known_effect_fns)
        return cls.hoist_nested_effects(rewritten, namer, known_effect_fns)

    @classmethod
    def rewrite_appends(cls, stmts, namer=None, known_effect_fns: frozenset = frozenset()):
        rewritten: list[IRStmt] = []
        for s in stmts:
            if isinstance(s, IRIf):
                rewritten.append(
                    IRIf(
                        s.test,
                        tuple(cls.rewrite_appends(s.body, namer, known_effect_fns)),
                        tuple(cls.rewrite_appends(s.orelse, namer, known_effect_fns)),
                        src=s.src,
                    )
                )
            elif isinstance(s, IRFor):
                rewritten.append(
                    IRFor(
                        s.target,
                        s.target_type,
                        s.iter,
                        tuple(cls.rewrite_appends(s.body, namer, known_effect_fns)),
                        src=s.src,
                    )
                )
            elif cls._is_append_call(s):
                receiver = s.value.func.value
                item = s.value.args[0]
                update: IRExpr
                if isinstance(receiver, IRAttribute) and isinstance(receiver.value, IRName):
                    target_name = receiver.value.id
                    target_type = receiver.value.type
                    target = IRName(target_name, target_type)
                    current = receiver
                    field = receiver.attr
                    update = IRRecordUpdate(
                        target, ((field, IRBinOp("<>", current, IRList((item,)))),)
                    )
                elif isinstance(receiver, IRName):
                    target_name = receiver.id
                    target_type = receiver.type
                    target = IRName(target_name, target_type)
                    update = IRBinOp("<>", target, IRList((item,)))
                else:
                    rewritten.append(s)
                    continue
                if namer is not None and cls.is_effect_call(item, known_effect_fns):
                    temp = namer.next_rebind(f"{target_name}_item")
                    item_type = item.type if isinstance(item, IRName) else None
                    rewritten.append(IRAssign(IRName(temp, item_type), item, src=s.src))
                    item = IRName(temp, item_type)
                    if isinstance(receiver, IRAttribute) and isinstance(receiver.value, IRName):
                        update = IRRecordUpdate(
                            target, ((field, IRBinOp("<>", current, IRList((item,)))),)
                        )
                    else:
                        update = IRBinOp("<>", target, IRList((item,)))
                rewritten.append(IRAssign(target, update, src=s.src))
            elif isinstance(s, IRAssign) and isinstance(s.target, IRAttribute):
                # Daml records are immutable. Turn `a.field = value` into a
                # rebinding of `a`, so loop planning can carry the record.
                if isinstance(s.target.value, IRName):
                    base = s.target.value
                    rewritten.append(
                        IRAssign(base, IRRecordUpdate(base, ((s.target.attr, s.value),)), src=s.src)
                    )
                else:
                    rewritten.append(s)
            else:
                rewritten.append(s)
        return rewritten

    @staticmethod
    def _is_append_call(stmt) -> bool:
        return (
            isinstance(stmt, IRExprStmt)
            and isinstance(stmt.value, IRCall)
            and isinstance(stmt.value.func, IRAttribute)
            and stmt.value.func.attr == "append"
            and (
                isinstance(stmt.value.func.value, IRName)
                or (
                    isinstance(stmt.value.func.value, IRAttribute)
                    and isinstance(stmt.value.func.value.value, IRName)
                )
            )
            and len(stmt.value.args) == 1
            and not stmt.value.keywords
        )

    @classmethod
    def collect_accumulators(cls, stmts, known, acc=None):
        if acc is None:
            acc = []
        for s in stmts:
            if isinstance(s, IRAssign):
                for name in cls._target_names(s.target):
                    if name in known and name not in acc:
                        acc.append(name)
            elif isinstance(s, IRIf):
                cls.collect_accumulators(s.body, known, acc)
                cls.collect_accumulators(s.orelse, known, acc)
            elif isinstance(s, IRFor):
                cls.collect_accumulators(s.body, known, acc)
        return acc

    @staticmethod
    def _target_names(target) -> tuple[str, ...]:
        if isinstance(target, IRName):
            return (target.id,)
        if isinstance(target, IRTuple):
            return tuple(item.id for item in target.elements if isinstance(item, IRName))
        return ()

    @classmethod
    def collect_assigned_names(cls, stmts, acc=None) -> set:
        if acc is None:
            acc = set()
        for s in stmts:
            if isinstance(s, IRAssign):
                acc.update(cls._target_names(s.target))
            elif isinstance(s, IRIf):
                cls.collect_assigned_names(s.body, acc)
                cls.collect_assigned_names(s.orelse, acc)
            elif isinstance(s, IRFor):
                cls.collect_assigned_names(s.body, acc)
        return acc

    @classmethod
    def hoist_nested_effects(cls, stmts, namer, known_effect_fns: frozenset) -> list[IRStmt]:
        result: list[IRStmt] = []
        for s in stmts:
            if isinstance(s, IRIf):
                if cls._contains_effect(s.test, known_effect_fns):
                    expanded = cls._expand_effectful_condition(
                        s.test, s.body, s.orelse, namer, known_effect_fns
                    )
                    result.extend(cls.hoist_nested_effects(expanded, namer, known_effect_fns))
                    continue
                test_lifted: list[tuple[str, IRCall]] = []
                test = cls._lift_effects(s.test, test_lifted, namer, known_effect_fns, top=True)
                result.extend(IRAssign(IRName(name), call, src=s.src) for name, call in test_lifted)
                s = replace(
                    s,
                    test=test,
                    body=tuple(cls.hoist_nested_effects(s.body, namer, known_effect_fns)),
                    orelse=tuple(cls.hoist_nested_effects(s.orelse, namer, known_effect_fns)),
                )
            elif isinstance(s, IRFor):
                iterable_lifted: list[tuple[str, IRCall]] = []
                iterator = cls._lift_effects(s.iter, iterable_lifted, namer, known_effect_fns)
                result.extend(
                    IRAssign(IRName(name), call, src=s.src) for name, call in iterable_lifted
                )
                s = replace(
                    s,
                    iter=iterator,
                    body=tuple(cls.hoist_nested_effects(s.body, namer, known_effect_fns)),
                )
            elif isinstance(s, (IRReturn, IRAssign, IRExprStmt)) and s.value is not None:
                lazy = cls._find_effectful_lazy(s.value, known_effect_fns)
                if lazy is not None:
                    expanded = cls._expand_effectful_value(s, lazy, namer, known_effect_fns)
                    result.extend(cls.hoist_nested_effects(expanded, namer, known_effect_fns))
                    continue
                lifted: list[tuple[str, IRCall]] = []
                value = cls._lift_effects(s.value, lifted, namer, known_effect_fns, top=True)
                result.extend(IRAssign(IRName(name), call, src=s.src) for name, call in lifted)
                s = replace(s, value=value)
            elif isinstance(s, IRAssert):
                assert_lifted: list[tuple[str, IRCall]] = []
                test = cls._lift_effects(s.test, assert_lifted, namer, known_effect_fns, top=True)
                msg = (
                    None
                    if s.msg is None
                    else cls._lift_effects(s.msg, assert_lifted, namer, known_effect_fns, top=True)
                )
                result.extend(
                    IRAssign(IRName(name), call, src=s.src) for name, call in assert_lifted
                )
                s = replace(s, test=test, msg=msg)
            result.append(s)
        return result

    @classmethod
    def _contains_effect(cls, expr, known_effect_fns: frozenset) -> bool:
        if cls.is_effect_call(expr, known_effect_fns):
            return True
        if isinstance(expr, (IRName, IRConstant)) or not is_dataclass(expr):
            return False
        for field in fields(expr):
            value = getattr(expr, field.name)
            values = value if isinstance(value, tuple) else (value,)
            if any(cls._contains_effect(item, known_effect_fns) for item in values):
                return True
        return False

    @classmethod
    def _find_effectful_lazy(cls, expr, known_effect_fns):
        """Find the first expression whose effect is evaluated lazily.

        Effects in an ``if`` branch or in a non-first boolean operand cannot
        remain inside a pure Daml expression. They must be represented as
        statement-level branches so the selected ``Update`` action executes
        only when Python would evaluate it.
        """
        if isinstance(expr, IRIfExp):
            if cls._contains_effect(expr.body, known_effect_fns) or cls._contains_effect(
                expr.orelse, known_effect_fns
            ):
                return expr
            for child in (expr.test, expr.body, expr.orelse):
                found = cls._find_effectful_lazy(child, known_effect_fns)
                if found is not None:
                    return found
            return None
        if isinstance(expr, IRBoolOp):
            for index, value in enumerate(expr.values):
                if index > 0 and cls._contains_effect(value, known_effect_fns):
                    return expr
                found = cls._find_effectful_lazy(value, known_effect_fns)
                if found is not None:
                    return found
            return None
        if isinstance(expr, (IRName, IRConstant)) or not is_dataclass(expr):
            return None
        for field in fields(expr):
            value = getattr(expr, field.name)
            values = value if isinstance(value, tuple) else (value,)
            for item in values:
                found = cls._find_effectful_lazy(item, known_effect_fns)
                if found is not None:
                    return found
        return None

    @classmethod
    def _replace_expr(cls, expr, target, replacement):
        if expr is target:
            return replacement
        if isinstance(expr, (IRName, IRConstant)) or not is_dataclass(expr):
            return expr
        changes = {}
        for field in fields(expr):
            value = getattr(expr, field.name)
            if isinstance(value, tuple):
                changes[field.name] = tuple(
                    cls._replace_expr(item, target, replacement) for item in value
                )
            else:
                changes[field.name] = cls._replace_expr(value, target, replacement)
        return replace(expr, **changes)

    @classmethod
    def _expand_effectful_value(cls, statement, lazy, namer, known_effect_fns):
        """Turn a lazy effect inside a statement value into control flow.

        Duplicate the statement into branches with the lazy expression
        replaced by the value Python would produce on that branch. Later
        normalization handles any effectful calls inside those branch values.
        """
        if isinstance(lazy, IRIfExp):
            body = (replace(statement, value=cls._replace_expr(statement.value, lazy, lazy.body)),)
            orelse = (
                replace(statement, value=cls._replace_expr(statement.value, lazy, lazy.orelse)),
            )
            return cls._expand_effectful_condition(lazy.test, body, orelse, namer, known_effect_fns)

        if isinstance(lazy, IRBoolOp) and len(lazy.values) > 1:
            first = lazy.values[0]
            remainder = (
                lazy.values[1] if len(lazy.values) == 2 else IRBoolOp(lazy.op, lazy.values[1:])
            )
            if lazy.op == "and":
                true_value, false_value = remainder, IRConstant(False)
            elif lazy.op == "or":
                true_value, false_value = IRConstant(True), remainder
            else:
                raise CompilerPanic(f"_expand_effectful_value: unsupported BoolOp {lazy.op}")
            body = (replace(statement, value=cls._replace_expr(statement.value, lazy, true_value)),)
            orelse = (
                replace(statement, value=cls._replace_expr(statement.value, lazy, false_value)),
            )
            return cls._expand_effectful_condition(first, body, orelse, namer, known_effect_fns)

        raise CompilerPanic(
            f"_expand_effectful_value: unsupported lazy expression {type(lazy).__name__}"
        )

    @classmethod
    def _expand_effectful_condition(
        cls, test, body, orelse, namer, known_effect_fns
    ) -> tuple[IRStmt, ...]:
        """Convert lazy boolean conditions into nested statement-level ifs.

        This preserves Python's short-circuit behavior while allowing an
        effectful operand such as `fetch(cid).value` to be bound with `<-` in
        the branch where it is actually evaluated.
        """
        if isinstance(test, IRBoolOp) and len(test.values) > 1:
            first, rest = test.values[0], test.values[1:]
            remainder = rest[0] if len(rest) == 1 else IRBoolOp(test.op, rest)
            if test.op == "and":
                nested = cls._expand_effectful_condition(
                    remainder, body, orelse, namer, known_effect_fns
                )
                return cls._expand_effectful_condition(
                    first, nested, orelse, namer, known_effect_fns
                )
            if test.op == "or":
                nested = cls._expand_effectful_condition(
                    remainder, body, orelse, namer, known_effect_fns
                )
                return cls._expand_effectful_condition(first, body, nested, namer, known_effect_fns)

        lifted: list[tuple[str, IRCall]] = []
        pure_test = cls._lift_effects(test, lifted, namer, known_effect_fns)
        prefix = [IRAssign(IRName(name), call) for name, call in lifted]
        return tuple(prefix) + (IRIf(pure_test, tuple(body), tuple(orelse)),)

    @classmethod
    def _lift_effects(cls, expr, lifted, namer, known_effect_fns, top=False):
        """Hoist nested ledger-effect calls out of an IR expression.

        Daml expressions embedded in pure contexts, such as a loop iterable
        or an arithmetic expression, cannot directly contain an ``Update``
        action. Recursively rebuild the expression, replace each nested
        effect call with a temporary ``IRName``, and append the corresponding
        ``IRAssign`` payload to ``lifted`` so the emitter can bind it with
        ``<-``. When ``top`` is true, preserve the outermost effect call for
        its surrounding statement to handle directly.
        """
        if isinstance(expr, IRIfExp):
            return replace(expr, test=cls._lift_effects(expr.test, lifted, namer, known_effect_fns))
        if isinstance(expr, IRBoolOp):
            first = cls._lift_effects(expr.values[0], lifted, namer, known_effect_fns)
            return replace(expr, values=(first, *expr.values[1:]))
        if isinstance(expr, (IRName, IRConstant)) or not is_dataclass(expr):
            return expr
        rebuilt = replace(
            expr,
            **{
                f.name: cls._lift_field(getattr(expr, f.name), lifted, namer, known_effect_fns)
                for f in fields(expr)
            },
        )
        if not top and cls.is_effect_call(rebuilt, known_effect_fns):
            name = namer.next_rebind("effect")
            lifted.append((name, rebuilt))
            return IRName(name)
        return rebuilt

    @classmethod
    def _lift_field(cls, value, lifted, namer, known_effect_fns):
        """Apply ``_lift_effects`` to an IR dataclass field or tuple field.

        IR nodes store child expressions in both scalar fields and tuples,
        such as call arguments and keyword arguments. This helper preserves
        that container shape while recursively lifting effects from every
        nested expression.
        """
        if isinstance(value, tuple):
            return tuple(cls._lift_field(item, lifted, namer, known_effect_fns) for item in value)
        return cls._lift_effects(value, lifted, namer, known_effect_fns)

    @classmethod
    def is_effect_call(cls, expr, known_effect_fns: frozenset = frozenset()) -> bool:
        return (
            isinstance(expr, IRCall)
            and isinstance(expr.func, IRName)
            and expr.func.id in known_effect_fns
        )

    @classmethod
    def body_has_ledger_effect(cls, stmts, known_effect_fns: frozenset = frozenset()) -> bool:
        for s in stmts:
            if isinstance(s, IRAssert):
                return True
            if isinstance(s, IRExprStmt) and cls.is_effect_call(s.value, known_effect_fns):
                return True
            if isinstance(s, IRAssign) and cls.is_effect_call(s.value, known_effect_fns):
                return True
            if (
                isinstance(s, IRReturn)
                and s.value is not None
                and cls.is_effect_call(s.value, known_effect_fns)
            ):
                return True
            if isinstance(s, IRIf) and (
                cls.body_has_ledger_effect(s.body, known_effect_fns)
                or cls.body_has_ledger_effect(s.orelse, known_effect_fns)
            ):
                return True
            if isinstance(s, IRFor) and cls.body_has_ledger_effect(s.body, known_effect_fns):
                return True
        return False

    @classmethod
    def requires_foldl_a(
        cls, stmts, in_update_block: bool, effect_functions: frozenset[str]
    ) -> bool:
        """Return whether a body requires Daml's action-aware fold import."""
        for stmt in stmts:
            if isinstance(stmt, IRFor):
                if (
                    in_update_block
                    and not cls.is_assertion_only_loop(stmt.body)
                    and cls.body_has_ledger_effect(stmt.body, effect_functions)
                ):
                    return True
                if cls.requires_foldl_a(stmt.body, in_update_block, effect_functions):
                    return True
            elif isinstance(stmt, IRIf) and (
                cls.requires_foldl_a(stmt.body, in_update_block, effect_functions)
                or cls.requires_foldl_a(stmt.orelse, in_update_block, effect_functions)
            ):
                return True
        return False

    @staticmethod
    def is_assertion_only_loop(body: tuple[IRStmt, ...]) -> bool:
        return (
            len(body) == 1
            and isinstance(body[0], IRAssert)
            and (body[0].msg is None or isinstance(body[0].msg, IRConstant))
        )

    @classmethod
    def body_has_assertion(cls, stmts) -> bool:
        """Return whether a statement tree contains an assertion."""
        for s in stmts:
            if isinstance(s, IRAssert):
                return True
            if isinstance(s, IRIf) and (
                cls.body_has_assertion(s.body) or cls.body_has_assertion(s.orelse)
            ):
                return True
            if isinstance(s, IRFor) and cls.body_has_assertion(s.body):
                return True
        return False

    @classmethod
    def _expr_free_names(cls, e, acc: set) -> None:
        if isinstance(e, IRName):
            acc.add(e.id)
        elif isinstance(e, IRAttribute):
            cls._expr_free_names(e.value, acc)
        elif isinstance(e, IRSubscript):
            cls._expr_free_names(e.value, acc)
            cls._expr_free_names(e.index, acc)
        elif isinstance(e, IRCall):
            cls._expr_free_names(e.func, acc)
            for a in e.args:
                cls._expr_free_names(a, acc)
            for _, v in e.keywords:
                cls._expr_free_names(v, acc)
        elif isinstance(e, IRConstant):
            pass
        elif isinstance(e, (IRList, IRTuple)):
            for el in e.elements:
                cls._expr_free_names(el, acc)
        elif isinstance(e, IRDict):
            for k in e.keys:
                cls._expr_free_names(k, acc)
            for v in e.values:
                cls._expr_free_names(v, acc)
        elif isinstance(e, IRBinOp):
            cls._expr_free_names(e.left, acc)
            cls._expr_free_names(e.right, acc)
        elif isinstance(e, IRBoolOp):
            for v in e.values:
                cls._expr_free_names(v, acc)
        elif isinstance(e, IRUnaryOp):
            cls._expr_free_names(e.operand, acc)
        elif isinstance(e, IRCompare):
            cls._expr_free_names(e.left, acc)
            cls._expr_free_names(e.right, acc)
        elif isinstance(e, IRRecordUpdate):
            cls._expr_free_names(e.base, acc)
            for _, v in e.updates:
                cls._expr_free_names(v, acc)
        elif isinstance(e, IRIfExp):
            cls._expr_free_names(e.test, acc)
            cls._expr_free_names(e.body, acc)
            cls._expr_free_names(e.orelse, acc)
        else:
            raise CompilerPanic(f"_expr_free_names: unsupported IR expression {type(e).__name__}")

    @classmethod
    def collect_free_names(cls, stmts, acc=None, bound=frozenset()) -> set:
        if acc is None:
            acc = set()
        for s in stmts:
            names: set = set()
            if isinstance(s, IRAssert):
                cls._expr_free_names(s.test, names)
                if s.msg is not None:
                    cls._expr_free_names(s.msg, names)
            elif isinstance(s, IRAssign):
                cls._expr_free_names(s.value, names)
                if not isinstance(s.target, IRName):
                    cls._expr_free_names(s.target, names)
            elif isinstance(s, IRIf):
                cls._expr_free_names(s.test, names)
                cls.collect_free_names(s.body, acc, bound)
                cls.collect_free_names(s.orelse, acc, bound)
            elif isinstance(s, IRFor):
                cls._expr_free_names(s.iter, names)
                cls.collect_free_names(s.body, acc, bound)
            elif isinstance(s, IRReturn):
                if s.value is not None:
                    cls._expr_free_names(s.value, names)
            elif isinstance(s, IRExprStmt):
                cls._expr_free_names(s.value, names)
            elif not isinstance(s, IRPass):
                raise CompilerPanic(
                    f"collect_free_names: unsupported IR statement {type(s).__name__}"
                )
            acc |= names - bound
            if isinstance(s, IRAssign):
                bound = bound | set(cls._target_names(s.target))
        return acc

    @classmethod
    def body_has_return(cls, stmts) -> bool:
        for s in stmts:
            if isinstance(s, IRReturn):
                return True
            if isinstance(s, IRIf) and (
                cls.body_has_return(s.body) or cls.body_has_return(s.orelse)
            ):
                return True
            if isinstance(s, IRFor) and cls.body_has_return(s.body):
                return True
        return False

    @classmethod
    def wrap_returns_as_some(cls, stmts):
        """Make function returns explicit when a loop is lowered recursively."""
        result = []
        for s in stmts:
            if isinstance(s, IRReturn):
                value = s.value if s.value is not None else IRTuple(())
                result.append(IRReturn(IRCall(IRName("Some"), (value,)), src=s.src))
            elif isinstance(s, IRIf):
                result.append(
                    IRIf(
                        s.test,
                        tuple(cls.wrap_returns_as_some(s.body)),
                        tuple(cls.wrap_returns_as_some(s.orelse)),
                        src=s.src,
                    )
                )
            elif isinstance(s, IRFor):
                result.append(
                    IRFor(
                        s.target,
                        s.target_type,
                        s.iter,
                        tuple(cls.wrap_returns_as_some(s.body)),
                        src=s.src,
                    )
                )
            else:
                result.append(s)
        return result

    @classmethod
    def apply_renames(cls, e, renames: dict):
        if not renames:
            return e
        if isinstance(e, IRName):
            return IRName(renames.get(e.id, e.id), e.type)
        if isinstance(e, IRAttribute):
            return IRAttribute(cls.apply_renames(e.value, renames), e.attr)
        if isinstance(e, IRSubscript):
            return IRSubscript(
                cls.apply_renames(e.value, renames),
                cls.apply_renames(e.index, renames),
                is_list=e.is_list,
            )
        if isinstance(e, IRCall):
            return IRCall(
                cls.apply_renames(e.func, renames),
                tuple(cls.apply_renames(a, renames) for a in e.args),
                tuple((k, cls.apply_renames(v, renames)) for k, v in e.keywords),
            )
        if isinstance(e, IRConstant):
            return e
        if isinstance(e, IRList):
            return IRList(tuple(cls.apply_renames(el, renames) for el in e.elements))
        if isinstance(e, IRTuple):
            return IRTuple(tuple(cls.apply_renames(el, renames) for el in e.elements))
        if isinstance(e, IRDict):
            return IRDict(
                tuple(cls.apply_renames(k, renames) for k in e.keys),
                tuple(cls.apply_renames(v, renames) for v in e.values),
            )
        if isinstance(e, IRBinOp):
            return IRBinOp(
                e.op, cls.apply_renames(e.left, renames), cls.apply_renames(e.right, renames)
            )
        if isinstance(e, IRBoolOp):
            return IRBoolOp(e.op, tuple(cls.apply_renames(v, renames) for v in e.values))
        if isinstance(e, IRUnaryOp):
            return IRUnaryOp(e.op, cls.apply_renames(e.operand, renames))
        if isinstance(e, IRCompare):
            return IRCompare(
                cls.apply_renames(e.left, renames), e.op, cls.apply_renames(e.right, renames)
            )
        if isinstance(e, IRRecordUpdate):
            return IRRecordUpdate(
                cls.apply_renames(e.base, renames),
                tuple((n, cls.apply_renames(v, renames)) for n, v in e.updates),
            )
        if isinstance(e, IRIfExp):
            return IRIfExp(
                cls.apply_renames(e.test, renames),
                cls.apply_renames(e.body, renames),
                cls.apply_renames(e.orelse, renames),
            )
        raise CompilerPanic(f"apply_renames: unsupported IR expression {type(e).__name__}")


class LoopAnalyzer:
    def __init__(self, namer, known_effect_fns: frozenset = frozenset()):
        self.namer = namer
        self.known_effect_fns = known_effect_fns

    def normalize(self, stmts) -> list[IRStmt]:
        rewritten = LoopAnalysis.rewrite_appends(stmts, self.namer, self.known_effect_fns)
        return LoopAnalysis.hoist_nested_effects(rewritten, self.namer, self.known_effect_fns)

    def plan(
        self, node: IRFor, known: frozenset, in_update_block: bool, post_loop: tuple[str, ...] = ()
    ) -> LoopPlan:
        body = tuple(node.body)
        # A Python loop target shadows an outer binding for the duration of the
        # loop. It must therefore not accidentally become a fold accumulator.
        loop_known = known - {node.target}
        accumulators = tuple(LoopAnalysis.collect_accumulators(body, loop_known))
        assertion: IRAssert | None = None
        if LoopAnalysis.is_assertion_only_loop(body) and isinstance(body[0], IRAssert):
            assertion = body[0]
        captured = tuple(
            sorted(
                (LoopAnalysis.collect_free_names(body) & loop_known)
                - set(accumulators)
                - set(post_loop)
            )
        )
        has_return = LoopAnalysis.body_has_return(body)
        update_loop = (
            assertion is None
            and in_update_block
            and LoopAnalysis.body_has_ledger_effect(body, self.known_effect_fns)
        )
        if accumulators and has_return:
            strategy = "unsupported_return_with_accumulator"
        elif has_return:
            strategy = "early_return"
        elif assertion is not None:
            strategy = "assertion"
        elif accumulators or post_loop or update_loop:
            strategy = "fold"
        elif LoopAnalysis.body_has_assertion(body):
            strategy = "pure_assertion"
        else:
            strategy = "drop"
        return LoopPlan(
            body=body,
            accumulators=accumulators,
            captured=captured,
            assertion=assertion,
            has_return=has_return,
            in_update_block=update_loop,
            strategy=strategy,
        )

    def lower_loop(
        self, node: IRFor, known: frozenset, in_update_block: bool, post_loop: tuple[str, ...] = ()
    ):
        """Convert a raw loop into an explicit normalized IR loop node."""
        plan = self.plan(node, known, in_update_block, post_loop)
        if plan.strategy == "unsupported_return_with_accumulator":
            raise DamlForgeException(
                "IR loop with both loop-carried accumulator mutation and an early "
                "return is not supported",
                code=ErrorCodes.DF1230.value,
            )
        if plan.strategy == "early_return":
            wrapped_body = LoopAnalysis.wrap_returns_as_some(plan.body)
            wrapped_body = LoopAnalysis.hoist_nested_effects(
                wrapped_body, self.namer, self.known_effect_fns
            )
            return IREarlyReturnLoop(
                node.target,
                node.target_type,
                node.iter,
                tuple(wrapped_body),
                plan.captured,
                in_update_block,
                src=node.src,
            )
        if plan.strategy == "assertion":
            assert plan.assertion is not None
            return IRAssertLoop(
                node.target,
                node.target_type,
                node.iter,
                plan.assertion,
                in_update_block,
                src=node.src,
            )
        if plan.strategy == "fold":
            return IRFold(
                node.target,
                node.target_type,
                node.iter,
                plan.body,
                plan.accumulators,
                plan.captured,
                plan.in_update_block,
                post_loop,
                tuple(name in known for name in post_loop),
                src=node.src,
            )
        if plan.strategy == "pure_assertion":
            return IRPureAssertionLoop(
                node.target, node.target_type, node.iter, plan.body, plan.captured, src=node.src
            )
        return None

    @staticmethod
    def _is_assertion_only_loop(body: tuple[IRStmt, ...]) -> bool:
        return LoopAnalysis.is_assertion_only_loop(body)


class LoopNamer:
    def __init__(self):
        self._loop_n = 0
        self._cont_n = 0
        self._rebind_n = 0

    def next(self) -> str:
        self._loop_n += 1
        return f"_damlforge_loop{self._loop_n}"

    def next_cont(self) -> str:
        self._cont_n += 1
        return f"_damlforge_cont{self._cont_n}"

    def next_rebind(self, base: str) -> str:
        self._rebind_n += 1
        return f"_damlforge_{base}{self._rebind_n}"
