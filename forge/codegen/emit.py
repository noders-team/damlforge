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

import re
from decimal import Decimal
from typing import Optional

from forge.builtin_stubs import stub_scope
from forge.builtins import collect_daml_builtins
from forge.builtins.daml_lang import TEMPLATE_ARGUMENT_FUNCTIONS
from forge.daml_versions import DamlCodegenSettings
from forge.codegen.doc import concat, line, nest, render, text
from forge.codegen.maps import is_map_type, render_map_literal, render_map_type
from forge.codegen.optional import is_optional_type, render_optional_type
from forge.codegen.sets import is_set_type, render_set_type
from forge.exceptions import CompilerPanic, DamlForgeException
from forge.utils import daml_choice_name
from forge.ir.lower import FLOOR_DIV_DECIMAL_HELPER, FLOOR_DIV_HELPER, LIST_SET_HELPER
from forge.ir.nodes import (
    IRAssert,
    IRAssertLoop,
    IRAssign,
    IRAttribute,
    IRBinOp,
    IRBoolOp,
    IRCall,
    IRChoice,
    IRCompare,
    IRConstant,
    Field,
    IRDict,
    IRExprStmt,
    IRFold,
    IREnum,
    IRFor,
    IRFunction,
    IRIf,
    IRIfExp,
    IRImport,
    IREarlyReturnLoop,
    IRInterface,
    IRInterfaceInstance,
    IRList,
    IRModule,
    IRName,
    IRPass,
    IRPureAssertionLoop,
    IRRecordUpdate,
    IRReturn,
    IRStruct,
    IRSubscript,
    IRTemplate,
    IRTuple,
    IRUnaryOp,
    IRVariant,
    SourceSpan,
    TypeRef,
)
from forge.models.decorators import Consuming
from forge.models.error_codes import ErrorCodes
from forge.ir.normalize import LoopAnalysis, LoopAnalyzer, LoopNamer
from forge.codegen.self_mutation import SELF, SelfFieldRewriter, SelfMutationInjector

_PRIMITIVE_TYPE_MAP = {
    "int": "Int",
    "Int": "Int",
    "float": "Decimal",
    "bool": "Bool",
    "Text": "Text",
    "str": "Text",
    "Decimal": "Decimal",
    "Date": "Date",
    "Timestamp": "Time",
    "Party": "Party",
    "None": "()",
}

_ATOMIC_GENERIC_NAMES = ("list", "tuple")

_COMPARE_OP_MAP = {
    "==": "==",
    "!=": "/=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "in": "`elem`",
    "not in": "`notElem`",
}
_BOOL_OP_MAP = {"and": "&&", "or": "||"}
_HELPER_DEFINITIONS = {
    FLOOR_DIV_HELPER: (
        f"{FLOOR_DIV_HELPER} : Int -> Int -> Int",
        f"{FLOOR_DIV_HELPER} a b =",
        "  let q = a / b",
        "      r = a % b",
        "  in if r /= 0 && (r < 0) /= (b < 0) then q - 1 else q",
    ),
    FLOOR_DIV_DECIMAL_HELPER: (
        f"{FLOOR_DIV_DECIMAL_HELPER} : Decimal -> Decimal -> Decimal",
        f"{FLOOR_DIV_DECIMAL_HELPER} a b =",
        "  intToDecimal (floor (a / b))",
    ),
    LIST_SET_HELPER: (
        f"{LIST_SET_HELPER} : Int -> a -> [a] -> [a]",
        f"{LIST_SET_HELPER} 0 value (_ :: xs) = value :: xs",
        f"{LIST_SET_HELPER} index value (x :: xs) = x :: {LIST_SET_HELPER} (index - 1) value xs",
        f'{LIST_SET_HELPER} _ _ [] = error "List index out of bounds"',
    ),
}
_UNARY_OP_MAP = {"not": "not ", "-": "-"}
_BIN_OP_MAP = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "%", "**": "^"}


def _is_text_literal(e) -> bool:
    return isinstance(e, IRConstant) and isinstance(e.value, str)


_CONSUMING_KEYWORD = {
    None: "",
    Consuming.CONSUMING: "",
    Consuming.NON_CONSUMING: "nonconsuming ",
    Consuming.PRE_CONSUMING: "preconsuming ",
    Consuming.POST_CONSUMING: "postconsuming ",
}

_MAX_TUPLE_ACCESSOR_INDEX = 8


def _required_field_type(f: Field) -> TypeRef:
    assert f.type is not None, f"field {f.name!r} is missing a type"
    return f.type


def render_type(t: TypeRef, as_arg: bool = False) -> str:
    rendered = _render_type_inner(t)
    if as_arg and _type_needs_parens_as_arg(t):
        return f"({rendered})"
    return rendered


def _type_needs_parens_as_arg(t: TypeRef) -> bool:
    return bool(t.args) and t.name not in _ATOMIC_GENERIC_NAMES


def _render_type_inner(t: TypeRef) -> str:
    if t.name == "list":
        return f"[{render_type(t.args[0])}]"
    if t.name == "tuple":
        return "(" + ", ".join(render_type(a) for a in t.args) + ")"
    if is_optional_type(t.name):
        return render_optional_type(t, render_type)
    if t.name == "ContractId":
        return f"ContractId {render_type(t.args[0], as_arg=True)}"
    if is_map_type(t.name):
        return render_map_type(t, render_type)
    if is_set_type(t.name):
        return render_set_type(t, render_type)
    if t.name in _PRIMITIVE_TYPE_MAP:
        return _PRIMITIVE_TYPE_MAP[t.name]
    name = _external_builtin_renames().get(t.name, t.name)
    if not t.args:
        return name
    return " ".join([name, *(render_type(a, as_arg=True) for a in t.args)])


def _type_ref_references_non_derivable_type(t: TypeRef) -> bool:
    bare_name = t.name.rsplit(".", 1)[-1]
    if bare_name in collect_daml_builtins().no_derive_types:
        return True
    return any(_type_ref_references_non_derivable_type(a) for a in t.args)


def render_constant(value, type_ref=None) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if type_ref is not None and type_ref.name == "Decimal" and isinstance(value, int):
        return _render_decimal_literal(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


_NEEDS_PARENS_AS_INFIX_OPERAND = (IRBinOp, IRBoolOp, IRUnaryOp, IRCompare, IRIfExp, IRSubscript)
_NEEDS_PARENS_AS_CALL_ARG = (
    IRCall,
    IRDict,
    IRSubscript,
    IRBinOp,
    IRBoolOp,
    IRUnaryOp,
    IRCompare,
    IRIfExp,
)


def _is_negative_constant(e) -> bool:
    return (
        isinstance(e, IRConstant)
        and isinstance(e.value, (int, float, Decimal))
        and not isinstance(e.value, bool)
        and e.value < 0
    )


def _render_infix_operand(e) -> str:
    rendered = render_expr(e)
    if isinstance(e, _NEEDS_PARENS_AS_INFIX_OPERAND) or _is_negative_constant(e):
        return f"({rendered})"
    return rendered


def _render_unary_operand(e) -> str:
    if isinstance(e, (IRAttribute, IRSubscript, IRCall)):
        return f"({render_expr(e)})"
    return _render_infix_operand(e)


def _render_call_arg(e) -> str:
    rendered = render_expr(e)
    if isinstance(e, _NEEDS_PARENS_AS_CALL_ARG) or _is_negative_constant(e):
        return f"({rendered})"
    return rendered


_LANGUAGE_BUILTIN_RENAMES = {"len": "length", "ceil": "ceiling", "from_some": "fromSome"}


def _external_builtin_renames() -> dict:
    return {**_LANGUAGE_BUILTIN_RENAMES, **collect_daml_builtins().function_renames}


def _builtin_import_modules() -> dict:
    return collect_daml_builtins().function_import_modules


def _case_accessors() -> dict:
    return collect_daml_builtins().case_accessors


def render_expr(e) -> str:
    if isinstance(e, IRName):
        return _external_builtin_renames().get(e.id, e.id)
    if isinstance(e, IRAttribute):
        return f"{_render_atom_for_attr(e.value)}.{e.attr}"
    if isinstance(e, IRSubscript):
        return _render_subscript(e)
    if isinstance(e, IRCall):
        return _render_call(e)
    if isinstance(e, IRConstant):
        return render_constant(e.value, e.type)
    if isinstance(e, IRList):
        return "[" + ", ".join(render_expr(el) for el in e.elements) + "]"
    if isinstance(e, IRTuple):
        return "(" + ", ".join(render_expr(el) for el in e.elements) + ")"
    if isinstance(e, IRDict):
        return render_map_literal(e.keys, e.values, render_expr, e.kind)
    if isinstance(e, IRBinOp):
        op = _BIN_OP_MAP.get(e.op, e.op)
        if e.op == "+" and (_is_text_literal(e.left) or _is_text_literal(e.right)):
            op = "<>"
        elif e.op == "+" and (isinstance(e.left, IRList) or isinstance(e.right, IRList)):
            op = "++"
        return f"{_render_infix_operand(e.left)} {op} {_render_infix_operand(e.right)}"
    if isinstance(e, IRBoolOp):
        op = f" {_BOOL_OP_MAP.get(e.op, e.op)} "
        return op.join(_render_infix_operand(v) for v in e.values)
    if isinstance(e, IRUnaryOp):
        return f"{_UNARY_OP_MAP.get(e.op, e.op)}{_render_unary_operand(e.operand)}"
    if isinstance(e, IRCompare):
        op = _COMPARE_OP_MAP.get(e.op, e.op)
        return f"{_render_infix_operand(e.left)} {op} {_render_infix_operand(e.right)}"
    if isinstance(e, IRRecordUpdate):
        updates_str = ", ".join(f"{name} = {render_expr(v)}" for name, v in e.updates)
        return f"{_render_atom_for_attr(e.base)} {{ {updates_str} }}"
    if isinstance(e, IRIfExp):
        test_str = (
            f"({render_expr(e.test)})" if isinstance(e.test, IRIfExp) else render_expr(e.test)
        )
        body_str = (
            f"({render_expr(e.body)})" if isinstance(e.body, IRIfExp) else render_expr(e.body)
        )
        return f"if {test_str} then {body_str} else {render_expr(e.orelse)}"
    raise CompilerPanic(f"render_expr: unsupported IR expression {type(e).__name__}")


def _render_atom_for_attr(value) -> str:
    if isinstance(value, IRName) and all(part.isidentifier() for part in value.id.split(".")):
        return value.id
    return f"({render_expr(value)})"


def _render_subscript(e: IRSubscript) -> str:
    if (
        not e.is_list
        and isinstance(e.index, IRConstant)
        and isinstance(e.index.value, int)
        and e.index.value >= 0
    ):
        if e.index.value <= _MAX_TUPLE_ACCESSOR_INDEX:
            return f"{_render_atom_for_attr(e.value)}._{e.index.value + 1}"
    return f"({render_expr(e.value)}) !! ({render_expr(e.index)})"


def _render_decimal_literal(value) -> str:
    text = format(value, "f") if isinstance(value, Decimal) else str(value)
    text = text if "." in text else f"{text}.0"
    return f"({text} : Decimal)"


def _render_record_field_value(v) -> str:
    rendered = render_expr(v)
    if isinstance(v, IRCall) and v.keywords:
        return f"({rendered})"
    return rendered


def _render_call(e: IRCall) -> str:
    if (
        isinstance(e.func, IRName)
        and e.func.id in ("TextMap.keys", "TextMap.values")
        and len(e.args) == 1
        and not e.keywords
    ):
        projection = "fst" if e.func.id.endswith("keys") else "snd"
        return f"map {projection} (TextMap.toList {_render_call_arg(e.args[0])})"

    if (
        isinstance(e.func, IRName)
        and e.func.id == "Decimal"
        and len(e.args) == 1
        and not e.keywords
    ):
        if isinstance(e.args[0], IRConstant):
            return _render_decimal_literal(e.args[0].value)
        return f"intToDecimal {_render_call_arg(e.args[0])}"

    if (
        isinstance(e.func, IRName)
        and e.func.id == "range"
        and not e.keywords
        and len(e.args) in (1, 2, 3)
    ):
        start = e.args[0] if len(e.args) >= 2 else IRConstant(0)
        stop = e.args[1] if len(e.args) >= 2 else e.args[0]
        if len(e.args) < 3:
            return f"[{render_expr(start)} .. ({render_expr(stop)}) - 1]"
        step = e.args[2]
        step_text = _render_call_arg(step)
        start_text = _render_call_arg(start)
        stop_text = _render_call_arg(stop)
        positive = f"[{start_text}, {start_text} + {step_text} .. ({stop_text}) - 1]"
        negative = f"[{start_text}, {start_text} + {step_text} .. ({stop_text}) + 1]"
        return f'if {step_text} > 0 then {positive} else if {step_text} < 0 then {negative} else error "range() arg 3 must not be zero"'

    if isinstance(e.func, IRName) and not e.keywords and len(e.args) == 1:
        constructor = _case_accessors().get(e.func.id)
        if constructor is not None:
            scrutinee = _render_call_arg(e.args[0])
            if isinstance(constructor, tuple):
                constructor, arity, *result = constructor
            else:
                arity = 1
                result = []
            names = [f"_damlforge_case_val{i}" for i in range(arity)]
            pattern = " ".join([constructor, *names])
            if result:
                result_constructor, field_names = result
                fields = ", ".join(f"{field} = {name}" for field, name in zip(field_names, names))
                value = f"({result_constructor} with {fields})"
            elif arity == 0:
                value = "()"
            elif arity == 1:
                value = names[0]
            else:
                value = "(" + ", ".join(names) + ")"
            return f"(case {scrutinee} of {pattern} -> Some {value}; _ -> None)"

    func_name = render_expr(e.func)
    if (
        isinstance(e.func, IRName)
        and e.func.id in TEMPLATE_ARGUMENT_FUNCTIONS
        and e.args
        and isinstance(e.args[0], (IRName, IRAttribute))
    ):
        rest = " ".join(_render_call_arg(a) for a in e.args[1:])
        return f"{func_name} @{render_expr(e.args[0])} {rest}".rstrip()
    if e.keywords:
        kw_str = ", ".join(f"{k} = {_render_record_field_value(v)}" for k, v in e.keywords)
        return f"{func_name} with {kw_str}"
    if not e.args:
        return func_name
    args_str = " ".join(_render_call_arg(a) for a in e.args)
    return f"{func_name} {args_str}"


class BodyTranslator:
    def __init__(
        self,
        namer: LoopNamer,
        hoisted: list,
        known_effect_fns: frozenset = frozenset(),
        self_type: Optional[str] = None,
        return_type: Optional[TypeRef] = None,
    ):
        self.namer = namer
        self.hoisted = hoisted
        self.known_effect_fns = known_effect_fns
        self.self_type = self_type
        self.return_type = return_type
        self.loop_analyzer = LoopAnalyzer(namer, known_effect_fns)

    def render_body(
        self, stmts, indent: int, in_update_block: bool = True, known=frozenset()
    ) -> list:
        rewritten = self.loop_analyzer.normalize(list(stmts))

        def tail(i, extra_known=frozenset(), renames=None):
            return self._final_tail(i, in_update_block)

        return self._translate(rewritten, indent, in_update_block, tail, known)

    def render_stmt(self, stmt, indent: int, in_update_block: bool = True) -> list:
        return self.render_body([stmt], indent, in_update_block)

    def _final_tail(self, i: int, in_update_block: bool) -> list:
        pad = "  " * i
        is_unit = self.return_type is None or (
            self.return_type.name == "None"
            or (self.return_type.name == "tuple" and not self.return_type.args)
        )
        if is_unit:
            return [f"{pad}pure ()"] if in_update_block else [f"{pad}()"]
        failure = 'error "Function did not return a value"'
        return [f"{pad}pure ({failure})"] if in_update_block else [f"{pad}{failure}"]

    @staticmethod
    def _resolve_all(names, renames: dict) -> list:
        return [renames.get(n, n) for n in names]

    @staticmethod
    def _render_assign_target(s: IRAssign) -> str:
        rendered = render_expr(s.target)
        if s.type is None or not isinstance(s.target, IRName):
            return rendered
        return f"({rendered} : {render_type(s.type)})"

    @staticmethod
    def _render_assert(indent, test: str, msg, in_update_block: bool, render_rest) -> list:
        pad = "  " * indent
        if in_update_block:
            head = f"{pad}assert ({test})" if msg is None else f"{pad}assertMsg {msg} ({test})"
            return [head] + render_rest(indent)
        failure = 'error "Assertion failed"' if msg is None else f"error {msg}"
        return (
            [f"{pad}if {test}", f"{pad}  then"]
            + render_rest(indent + 2)
            + [f"{pad}  else {failure}"]
        )

    def _render_leaf(self, s, indent: int, in_update_block: bool, renames: dict) -> list:
        pad = "  " * indent
        if isinstance(s, IRAssign):
            value = LoopAnalysis.apply_renames(s.value, renames)
            target = self._render_assign_target(s)
            if in_update_block and LoopAnalysis.is_effect_call(s.value, self.known_effect_fns):
                return [f"{pad}{target} <- {render_expr(value)}"]
            binding = f"let {target} = {render_expr(value)}"
            return [f"{pad}{binding} in" if not in_update_block else f"{pad}{binding}"]
        if isinstance(s, IRExprStmt):
            return [f"{pad}{render_expr(LoopAnalysis.apply_renames(s.value, renames))}"]
        raise CompilerPanic(f"_render_leaf: unsupported IR statement {type(s).__name__}")

    def _translate(
        self, stmts, indent, in_update_block, make_tail, known=frozenset(), renames=None
    ) -> list:
        renames = renames or {}
        pad = "  " * indent
        if not stmts:
            return make_tail(indent, frozenset(), renames)

        first, rest = stmts[0], stmts[1:]

        if isinstance(first, IRReturn):
            if first.value is None:
                return [f"{pad}pure ()"] if in_update_block else [f"{pad}()"]
            value_expr = render_expr(LoopAnalysis.apply_renames(first.value, renames))
            if in_update_block and LoopAnalysis.is_effect_call(first.value, self.known_effect_fns):
                return [f"{pad}{value_expr}"]
            already_parenthesized = isinstance(first.value, IRTuple)
            wrapped = value_expr if already_parenthesized else f"({value_expr})"
            return [f"{pad}pure {wrapped}"] if in_update_block else [f"{pad}{value_expr}"]

        if isinstance(first, IRPass):
            # `pass` is a no-op in Python.  It must not terminate the
            # surrounding block and hide statements that follow it.
            return self._translate(rest, indent, in_update_block, make_tail, known, renames)

        if isinstance(first, IRIf):
            cont_lines = []
            inline_rest = False
            if rest:
                branch_stmts = list(first.body) + list(first.orelse)
                cont_vars = LoopAnalysis.collect_accumulators(branch_stmts, known)
                free_after = LoopAnalysis.collect_free_names(rest)
                new_vars = sorted(
                    n
                    for n in LoopAnalysis.collect_assigned_names(branch_stmts)
                    if n not in known and n in free_after and n not in cont_vars
                )
                cont_vars = cont_vars + new_vars
                if (
                    in_update_block
                    and not cont_vars
                    and not LoopAnalysis.body_has_return(branch_stmts)
                ):
                    inline_rest = True

                    def branch_tail(i, extra_known=frozenset(), br_renames=None):
                        # The branch is followed by `rest`; it only needs to
                        # close its do block, not synthesize the function's
                        # missing-return error.
                        return [f"{'  ' * i}{'pure ()' if in_update_block else '()'}"]

                else:
                    cont_name = self.namer.next_cont()
                    params = " ".join(cont_vars)
                    header = f"{cont_name} {params}".rstrip()
                    cont_lines = [
                        f"{pad}let {header} = do" if in_update_block else f"{pad}let {header} ="
                    ]
                    cont_renames = {k: v for k, v in renames.items() if k not in cont_vars}
                    cont_lines.extend(
                        self._translate(
                            rest,
                            indent + 3,
                            in_update_block,
                            make_tail,
                            known | set(cont_vars),
                            cont_renames,
                        )
                    )
                    if not in_update_block:
                        cont_lines.append(f"{pad}in")

                    def branch_tail(i, extra_known=frozenset(), br_renames=None):
                        args = " ".join(self._resolve_all(cont_vars, br_renames or {}))
                        call = f"{cont_name} {args}".rstrip()
                        return [f"{'  ' * i}{call}"]

            else:
                branch_tail = make_tail

            test_expr = render_expr(LoopAnalysis.apply_renames(first.test, renames))
            lines = [
                f"{pad}if {test_expr}",
                f"{pad}  then do" if in_update_block else f"{pad}  then",
            ]
            lines.extend(
                self._translate(
                    list(first.body), indent + 2, in_update_block, branch_tail, known, renames
                )
            )
            lines.append(f"{pad}  else do" if in_update_block else f"{pad}  else")
            lines.extend(
                self._translate(
                    list(first.orelse), indent + 2, in_update_block, branch_tail, known, renames
                )
            )
            if inline_rest:
                lines.extend(
                    self._translate(rest, indent, in_update_block, make_tail, known, renames)
                )
            return cont_lines + lines

        if isinstance(first, IRFor):

            free_after = LoopAnalysis.collect_free_names(rest)
            loop_defined = LoopAnalysis.collect_assigned_names(first.body)
            post_loop_names = set(free_after & loop_defined - set(known))
            # The for-target always shadows an outer name while the loop runs,
            # so it is a separate post-loop value even when it was bound before.
            if first.target in free_after:
                post_loop_names.add(first.target)
            post_loop = tuple(sorted(post_loop_names))

            def rest_tail(i, extra_known=frozenset(), for_renames=None):
                return self._translate(
                    rest, i, in_update_block, make_tail, known | extra_known, for_renames or {}
                )

            return self._translate_for(
                first, indent, in_update_block, rest_tail, known, renames, post_loop
            )

        if isinstance(first, IRAssert):
            test = render_expr(LoopAnalysis.apply_renames(first.test, renames))
            msg = None
            if first.msg is not None:
                msg = _render_call_arg(LoopAnalysis.apply_renames(first.msg, renames))
            return self._render_assert(
                indent,
                test,
                msg,
                in_update_block,
                lambda i: self._translate(rest, i, in_update_block, make_tail, known, renames),
            )

        if isinstance(first, IRAssign) and isinstance(first.target, IRTuple):
            targets = [target for target in first.target.elements if isinstance(target, IRName)]
            if not targets or len(targets) != len(first.target.elements):
                raise CompilerPanic("tuple assignment targets must be names")
            if not isinstance(first.value, IRTuple) or len(targets) != len(first.value.elements):
                raise CompilerPanic("tuple assignment requires matching tuple values")
            tuple_name = self.namer.next_rebind("tuple")
            value = render_expr(LoopAnalysis.apply_renames(first.value, renames))
            bind = f"let {tuple_name} = {value}"
            lines = [f"{pad}{bind}" if in_update_block else f"{pad}{bind} in"]
            new_renames = dict(renames)
            for index, target in enumerate(targets):
                fresh = self.namer.next_rebind(target.id)
                projected = f"{tuple_name}._{index + 1}"
                binding = f"let {fresh} = {projected}"
                lines.append(f"{pad}{binding}" if in_update_block else f"{pad}{binding} in")
                new_renames[target.id] = fresh
            return lines + self._translate(
                rest,
                indent,
                in_update_block,
                make_tail,
                known | set(target.id for target in targets),
                new_renames,
            )

        if isinstance(first, IRAssign) and isinstance(first.target, IRName):
            if first.target.id in known:
                value = LoopAnalysis.apply_renames(first.value, renames)
                fresh = self.namer.next_rebind(first.target.id)
                fresh_target = (
                    f"({fresh} : {render_type(first.type)})" if first.type is not None else fresh
                )
                if in_update_block and LoopAnalysis.is_effect_call(
                    first.value, self.known_effect_fns
                ):
                    line = f"{pad}{fresh_target} <- {render_expr(value)}"
                else:
                    binding = f"let {fresh_target} = {render_expr(value)}"
                    line = f"{pad}{binding}" if in_update_block else f"{pad}{binding} in"
                new_renames = {**renames, first.target.id: fresh}
                return [line] + self._translate(
                    rest, indent, in_update_block, make_tail, known, new_renames
                )
            known = known | {first.target.id}

        return self._render_leaf(first, indent, in_update_block, renames) + self._translate(
            rest, indent, in_update_block, make_tail, known, renames
        )

    def _translate_for(
        self,
        node: IRFor,
        indent,
        in_update_block,
        make_tail,
        known=frozenset(),
        renames=None,
        post_loop=(),
    ) -> list:
        renames = renames or {}
        normalized = self.loop_analyzer.lower_loop(node, known, in_update_block, post_loop)
        if normalized is None:
            return make_tail(indent, frozenset(), renames)
        return self._translate_normalized_loop(
            normalized, indent, make_tail, known, renames, in_update_block
        )

    def _translate_normalized_loop(
        self, node, indent, make_tail, known, renames, surrounding_in_update_block
    ) -> list:
        if isinstance(node, IRFold):
            return self._translate_fold(
                node, indent, make_tail, known, renames, surrounding_in_update_block
            )
        if isinstance(node, IRAssertLoop):
            return self._translate_assertion_loop(node, indent, make_tail, renames)
        if isinstance(node, IREarlyReturnLoop):
            return self._translate_early_return_loop(node, indent, make_tail, known, renames)
        if isinstance(node, IRPureAssertionLoop):
            return self._translate_pure_assertion_loop(node, indent, make_tail, known, renames)
        raise CompilerPanic(f"_translate_normalized_loop: unsupported node {type(node).__name__}")

    @staticmethod
    def _loop_state(names, renames=None) -> str:
        resolved = [renames.get(name, name) for name in names] if renames else list(names)
        if not resolved:
            return "()"
        if len(resolved) == 1:
            return resolved[0]
        return f"({', '.join(resolved)})"

    def _translate_assertion_loop(self, node: IRAssertLoop, indent, make_tail, renames) -> list:
        assertion = node.assertion
        body_renames = {name: value for name, value in renames.items() if name != node.target}
        test = render_expr(LoopAnalysis.apply_renames(assertion.test, body_renames))
        iterable = _render_call_arg(LoopAnalysis.apply_renames(node.iter, renames))
        condition = f"all (\\{node.target} -> {test}) {iterable}"
        msg = None if assertion.msg is None else _render_call_arg(assertion.msg)
        return self._render_assert(
            indent,
            condition,
            msg,
            node.in_update_block,
            lambda i: make_tail(i, frozenset(), renames),
        )

    def _translate_fold(
        self, node: IRFold, indent, make_tail, known, renames, surrounding_in_update_block
    ) -> list:
        pad = "  " * indent
        accumulators = list(node.accumulators)
        post_loop = list(node.post_loop)
        post_state_names = [self.namer.next_rebind(f"post_{name}") for name in post_loop]
        inner_known = known | {node.target} | set(accumulators) | set(post_loop)
        fresh_here = set(accumulators) | {node.target} | set(post_loop)
        inner_renames = {k: v for k, v in renames.items() if k not in fresh_here}
        state_names = accumulators + post_state_names
        state_pattern = self._loop_state(state_names)
        initial_values = [renames.get(name, name) for name in accumulators]
        for name, initially_bound in zip(post_loop, node.post_loop_initially_bound):
            initial_values.append(f"Some {renames.get(name, name)}" if initially_bound else "None")
        initial_state = (
            "()"
            if not initial_values
            else (
                f"({initial_values[0]})"
                if len(initial_values) == 1 and post_loop
                else (
                    initial_values[0]
                    if len(initial_values) == 1
                    else f"({', '.join(initial_values)})"
                )
            )
        )
        iter_expr = _render_call_arg(LoopAnalysis.apply_renames(node.iter, renames))

        def fold_tail(i, extra_known=frozenset(), fold_renames=None):
            current = fold_renames or {}
            values = [current.get(name, name) for name in accumulators]
            values.extend(f"Some {current.get(name, name)}" for name in post_loop)
            state = (
                "()"
                if not values
                else (values[0] if len(values) == 1 else f"({', '.join(values)})")
            )
            if node.in_update_block:
                value = f"({state})" if len(values) == 1 and post_loop else state
                return [f"{'  ' * i}pure {value}"]
            return [f"{'  ' * i}{state}"]

        body_indent = indent + (3 if node.in_update_block else 5)
        body_lines = self._translate(
            list(node.body),
            body_indent,
            node.in_update_block,
            fold_tail,
            inner_known,
            inner_renames,
        )
        body_lines[-1] += ")"
        fresh_names = [self.namer.next_rebind(name) for name in state_names]
        bind_target = self._loop_state(fresh_names) if fresh_names else "_"

        if node.in_update_block:
            lines = [
                f"{pad}{bind_target} <-",
                f"{pad}  foldlA",
                f"{pad}    (\\{state_pattern} {node.target} -> do",
                *body_lines,
                f"{pad}    {initial_state} {iter_expr}",
            ]
        else:
            lines = [
                f"{pad}let {bind_target} =",
                f"{pad}      foldl",
                f"{pad}        (\\{state_pattern} {node.target} ->",
                *body_lines,
                f"{pad}        {initial_state} {iter_expr}",
            ]
            if not surrounding_in_update_block:
                lines.append(f"{pad}in")
        post_renames = {**renames}
        post_renames.update(dict(zip(accumulators, fresh_names)))
        post_renames.update(
            (name, f"fromSome {fresh}")
            for name, fresh in zip(post_loop, fresh_names[len(accumulators) :])
        )
        lines.extend(make_tail(indent, set(accumulators) | set(post_loop), post_renames))
        return lines

    def _translate_early_return_loop(
        self, node: IREarlyReturnLoop, indent, make_tail, known, renames
    ) -> list:
        pad = "  " * indent
        helper = self.namer.next()
        rest_name = "_damlforge_rest"
        captured = list(node.captured)
        inner_known = known | {node.target}
        fresh_here = set(captured) | {node.target}
        inner_renames = {k: v for k, v in renames.items() if k not in fresh_here}
        base_params = [
            f"({p} : {self.self_type})" if p == SELF and self.self_type else p for p in captured
        ]
        base_head = f"{helper} {' '.join(base_params + ['[]'])}".strip()
        rec_head = f"{helper} {' '.join(captured + [f'({node.target} :: {rest_name})'])}"
        resolved_captured = self._resolve_all(captured, renames)
        iter_expr = render_expr(LoopAnalysis.apply_renames(node.iter, renames))

        base_rhs = "pure None" if node.in_update_block else "None"

        def recursive_tail(i, extra_known=frozenset(), rec_renames=None):
            call = f"{helper} {' '.join(captured)} {rest_name}".strip()
            return [f"{'  ' * i}{call}"]

        def_lines = [
            f"{base_head} = {base_rhs}",
            f"{rec_head} = do" if node.in_update_block else f"{rec_head} =",
        ]
        def_lines.extend(
            self._translate(
                list(node.body), 1, node.in_update_block, recursive_tail, inner_known, inner_renames
            )
        )
        self.hoisted.extend(def_lines)
        self.hoisted.append("")

        call_expr = f"{helper} {' '.join(resolved_captured)} {iter_expr}".strip()
        maybe_name = self.namer.next_rebind("maybe")
        early_name = self.namer.next_rebind("early")
        lines = [
            (
                f"{pad}{maybe_name} <- {call_expr}"
                if node.in_update_block
                else f"{pad}let {maybe_name} = {call_expr} in"
            )
        ]
        lines.append(f"{pad}case {maybe_name} of")
        lines.append(
            f"{pad}  Some {early_name} -> "
            + (f"pure {early_name}" if node.in_update_block else early_name)
        )
        lines.append(f"{pad}  None -> do" if node.in_update_block else f"{pad}  None ->")
        lines.extend(make_tail(indent + 2, frozenset(), renames))
        return lines

    def _translate_pure_assertion_loop(
        self, node: IRPureAssertionLoop, indent, make_tail, known, renames
    ) -> list:
        """Preserve assertions in a loop inside a pure function.

        A Daml `assert` is an Update action, but Python functions may use an
        assertion as a pure validation guard. For that case the helper is a
        pure recursive traversal whose failing branch is `error`.
        """
        pad = "  " * indent
        helper = self.namer.next()
        rest_name = "_damlforge_rest"
        captured = list(node.captured)
        inner_known = known | {node.target}
        fresh_here = set(captured) | {node.target}
        inner_renames = {k: v for k, v in renames.items() if k not in fresh_here}
        base_params = [
            f"({p} : {self.self_type})" if p == SELF and self.self_type else p for p in captured
        ]
        base_head = f"{helper} {' '.join(base_params + ['[]'])}".strip()
        rec_head = f"{helper} {' '.join(captured + [f'({node.target} :: {rest_name})'])}"
        resolved_captured = self._resolve_all(captured, renames)
        iter_expr = render_expr(LoopAnalysis.apply_renames(node.iter, renames))

        def recursive_tail(i, extra_known=frozenset(), rec_renames=None):
            call = f"{helper} {' '.join(captured)} {rest_name}".strip()
            return [f"{'  ' * i}{call}"]

        def_lines = [f"{base_head} = ()", f"{rec_head} ="]
        def_lines.extend(
            self._translate(list(node.body), 1, False, recursive_tail, inner_known, inner_renames)
        )
        self.hoisted.extend(def_lines)
        self.hoisted.append("")

        call_expr = f"{helper} {' '.join(resolved_captured)} {iter_expr}".strip()
        checked_name = self.namer.next_rebind("checked")
        return [f"{pad}let {checked_name} = {call_expr}", f"{pad}in"] + make_tail(
            indent, frozenset(), renames
        )


def render_body(
    stmts,
    indent: int,
    in_update_block: bool = True,
    known=frozenset(),
    hoisted: list = None,
    namer: LoopNamer = None,
    self_type: str = None,
    known_effect_fns: frozenset = frozenset(),
    return_type: TypeRef = None,
) -> list:
    namer = namer if namer is not None else LoopNamer()
    translator = BodyTranslator(
        namer, hoisted if hoisted is not None else [], known_effect_fns, self_type, return_type
    )
    return translator.render_body(stmts, indent, in_update_block, known)


def render_stmt(stmt, indent: int, in_update_block: bool = True) -> list:
    return render_body([stmt], indent, in_update_block)


class Emitter:
    def __init__(
        self,
        known_effect_fns: frozenset = frozenset(),
        settings: DamlCodegenSettings = DamlCodegenSettings(),
    ):
        self.lines: list = []
        self.mappings: list = []
        self.namer = LoopNamer()
        self.hoisted: list = []
        self.known_effect_fns = known_effect_fns
        self.derive_serializable = settings.derive_serializable
        self.settings = settings

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def lines_block(self, texts) -> None:
        self.lines.extend(texts)

    def record(self, src: Optional[SourceSpan], kind: str, name: str, start_line: int) -> None:
        if src is None:
            return
        self.mappings.append(
            {
                "generated": {"startLine": start_line, "endLine": len(self.lines)},
                "source": {
                    "startLine": src.lineno,
                    "startColumn": src.col_offset,
                    "endLine": src.end_lineno,
                    "endColumn": src.end_col_offset,
                },
                "kind": kind,
                "name": name,
            }
        )

    def _translator(
        self, self_type: Optional[str] = None, return_type: Optional[TypeRef] = None
    ) -> BodyTranslator:
        return BodyTranslator(
            self.namer, self.hoisted, self.known_effect_fns, self_type, return_type
        )

    def _derivations(self, can_derive_eq_show: bool) -> list[str]:
        derivations = ["Eq", "Show"] if can_derive_eq_show else []
        if self.derive_serializable:
            derivations.append("Serializable")
        return derivations

    def emit_struct(self, s: IRStruct) -> None:
        start = len(self.lines) + 1
        head = " ".join([s.name, *s.params])
        parts = [text(f"data {head} = {s.name}" + ("" if s.fields else " {}"))]
        if s.fields:
            field_lines = concat(
                concat([line(), text(f"{f.name} : {render_type(_required_field_type(f))}")])
                for f in s.fields
            )
            parts.append(nest(2, concat([line(), text("with"), nest(2, field_lines)])))
        can_derive_eq_show = not any(
            _type_ref_references_non_derivable_type(_required_field_type(f)) for f in s.fields
        )
        derivations = self._derivations(can_derive_eq_show)
        if derivations:
            parts.append(nest(2, concat([line(), text(f"deriving ({', '.join(derivations)})")])))
        self.lines_block(render(concat(parts)))
        self.record(s.src, "struct", s.name, start)

    def emit_enum(self, enum: IREnum) -> None:
        start = len(self.lines) + 1
        if not enum.values:
            doc = text(f"data {enum.name}")
        else:
            derivations = self._derivations(True)
            doc = concat(
                [
                    text(f"data {enum.name} = " + " | ".join(enum.values)),
                    nest(2, concat([line(), text(f"deriving ({', '.join(derivations)})")])),
                ]
            )
        self.lines_block(render(doc))
        self.record(enum.src, "enum", enum.name, start)

    def emit_variant(self, variant: IRVariant) -> None:
        start = len(self.lines) + 1
        self.line(f"data {' '.join([variant.name, *variant.params])}")
        if not variant.constructors:
            self.record(variant.src, "variant", variant.name, start)
            return
        for index, constructor in enumerate(variant.constructors):
            prefix = "=" if index == 0 else "|"
            suffix = " with" if constructor.record and constructor.fields else ""
            if constructor.fields and not constructor.record:
                suffix = " " + " ".join(
                    render_type(_required_field_type(field), as_arg=True)
                    for field in constructor.fields
                )
            self.line(f"  {prefix} {constructor.name}{suffix}")
            if constructor.record:
                for field in constructor.fields:
                    self.line(f"      {field.name} : {render_type(_required_field_type(field))}")

        field_types = [
            _required_field_type(field)
            for constructor in variant.constructors
            for field in constructor.fields
        ]
        can_derive_eq_show = not any(
            _type_ref_references_non_derivable_type(field) for field in field_types
        )
        derivations = self._derivations(can_derive_eq_show)
        if derivations:
            self.line(f"  deriving ({', '.join(derivations)})")
        self.record(variant.src, "variant", variant.name, start)

    def emit_choice(
        self,
        c: IRChoice,
        container_name: str,
        indent: int,
        field_types=None,
        delegate_to: Optional[str] = None,
    ) -> None:
        start = len(self.lines) + 1
        is_template_choice = field_types is not None
        known = frozenset(a.name for a in c.args)

        body = c.body
        display_return_type = c.return_type
        if is_template_choice:
            body = SelfFieldRewriter.rewrite_stmts(list(body), field_types)
            known = known | {SELF}
            if SelfMutationInjector.body_mutates_self(body):
                body = SelfMutationInjector.inject_self_create(body)
                original = c.return_type if c.return_type is not None else TypeRef("tuple", ())
                display_return_type = TypeRef(
                    "tuple", (TypeRef("ContractId", (TypeRef(container_name),)), original)
                )

        consuming_kw = _CONSUMING_KEYWORD.get(c.consuming, "")
        return_type = render_type(display_return_type) if display_return_type is not None else "()"
        controller_expr = (
            render_expr(c.controller) if c.controller is not None else "-- MISSING controller"
        )

        parts = [text(f"{consuming_kw}choice {daml_choice_name(c.name)} : {return_type}")]
        if c.args:
            arg_lines = concat(
                concat(
                    [
                        line(),
                        text(f"{a.name} : {render_type(a.type) if a.type is not None else '()'}"),
                    ]
                )
                for a in c.args
            )
            parts.append(nest(2, concat([line(), text("with"), nest(2, arg_lines)])))
        if c.observer is not None:
            parts.append(nest(2, concat([line(), text(f"observer {render_expr(c.observer)}")])))
        parts.append(nest(2, concat([line(), text(f"controller {controller_expr}")])))

        if delegate_to is not None:
            arg_names = " ".join(a.name for a in c.args)
            call = f"{delegate_to} this self" + (f" {arg_names}" if arg_names else "")
            parts.append(nest(2, concat([line(), text(f"do {call}")])))
            self.lines_block(render(concat(parts), initial_indent=indent * 2))
            self.record(c.src, "choice", f"{container_name}.{c.name}", start)
            return

        parts.append(nest(2, concat([line(), text("do")])))
        self.lines_block(render(concat(parts), initial_indent=indent * 2))

        body_lines = [f"{'  ' * indent}    let {SELF} = this"] if is_template_choice else []
        translator = self._translator(
            self_type=container_name if is_template_choice else None,
            return_type=display_return_type,
        )
        body_lines.extend(translator.render_body(body, indent + 2, known=known))
        self.lines_block(body_lines)
        self.record(c.src, "choice", f"{container_name}.{c.name}", start)

    def emit_interface_choice_impl(
        self, c: IRChoice, container_name: str, indent: int, field_types
    ) -> None:
        start = len(self.lines) + 1
        arg_names = [a.name for a in c.args]
        header = f"{self.private_name(c.name + 'Impl')} _self" + "".join(f" {n}" for n in arg_names)
        self.lines_block([f"{'  ' * indent}{header} = do"])

        body = SelfFieldRewriter.rewrite_stmts(list(c.body), field_types)
        known = frozenset(arg_names) | {SELF}
        body_lines = [f"{'  ' * indent}    let {SELF} = this"]
        translator = self._translator(self_type=container_name, return_type=c.return_type)
        body_lines.extend(translator.render_body(body, indent + 2, known=known))
        self.lines_block(body_lines)
        self.record(c.src, "interface_choice_impl", f"{container_name}.{c.name}Impl", start)

    def emit_function(self, fn: IRFunction) -> None:
        start = len(self.lines) + 1
        in_update_block = fn.effectful
        return_type = (
            render_type(fn.return_type, as_arg=True) if fn.return_type is not None else "()"
        )
        full_return = f"Update {return_type}" if in_update_block else return_type
        arg_types = [
            render_type(a.type, as_arg=True) if a.type is not None else "()" for a in fn.args
        ]
        self.line(f"{fn.name} : {' -> '.join(arg_types + [full_return])}")
        header = f"{fn.name} {' '.join(a.name for a in fn.args)}".rstrip()
        self.line(f"{header} = do" if in_update_block else f"{header} =")
        translator = self._translator(return_type=fn.return_type)
        self.lines_block(
            translator.render_body(
                fn.body,
                1,
                in_update_block=in_update_block,
                known=frozenset(a.name for a in fn.args),
            )
        )
        self.record(fn.src, "function", fn.name, start)

    def emit_template(self, t: IRTemplate) -> None:
        start = len(self.lines) + 1
        parts = [text(f"template {t.name}")]
        if t.fields:
            field_lines = concat(
                concat([line(), text(f"{f.name} : {render_type(_required_field_type(f))}")])
                for f in t.fields
            )
            parts.append(nest(2, concat([line(), text("with"), nest(2, field_lines)])))

        signatory_expr = (
            render_expr(t.signatory) if t.signatory is not None else "-- MISSING signatory"
        )
        where_parts = [line(), text(f"signatory {signatory_expr}")]
        if t.observer is not None:
            where_parts.extend([line(), text(f"observer {render_expr(t.observer)}")])

        ensure_conditions = []
        if t.ensure is not None:
            ensure_conditions.append(render_expr(t.ensure))
        if t.init:
            for b in t.init.body:
                if (
                    isinstance(b, IRExprStmt)
                    and isinstance(b.value, IRCall)
                    and isinstance(b.value.func, IRName)
                    and b.value.func.id in ("ensure", "assert")
                    and b.value.args
                ):
                    ensure_conditions.append(render_expr(b.value.args[0]))
                if isinstance(b, IRAssert) and b.test is not None:
                    ensure_conditions.append(render_expr(b.test))
        if ensure_conditions:
            where_parts.extend([line(), text("ensure " + " && ".join(ensure_conditions))])
        if t.key is not None:
            if (error := self.settings.contract_keys_error()) is not None:
                message = f"Template `{t.name}` declares a contract key, but {error}"
                raise DamlForgeException(message, code=ErrorCodes.DF1234.value)
            key_type = f" : {render_type(t.key_type)}" if t.key_type is not None else ""
            where_parts.extend([line(), text(f"key {_render_call_arg(t.key)}{key_type}")])
        if t.maintainer is not None:
            where_parts.extend([line(), text(f"maintainer {render_expr(t.maintainer)}")])

        parts.append(nest(2, concat([line(), text("where"), nest(2, concat(where_parts))])))
        self.lines_block(render(concat(parts)))

        field_types = {f.name: f.type for f in t.fields}
        for c in t.choices:
            self.line("")
            self.emit_choice(c, t.name, indent=2, field_types=field_types)
        for ii in t.interface_instances:
            self.line("")
            self.emit_interface_instance(ii, t.name, field_types)
        if t.checked_fetch is not None and t.checked_fetch.return_type is not None:
            body = SelfFieldRewriter.rewrite_stmts(list(t.checked_fetch.body), field_types)
            value = body[-1].value if len(body) == 1 and isinstance(body[-1], IRReturn) else None
            if value is not None:
                self.line("")
                group_type = render_type(t.checked_fetch.return_type)
                self.line(f"instance HasCheckedFetch {t.name} {group_type} where")
                self.line(f"  contractGroupId self = {render_expr(value)}")
        self.record(t.src, "template", t.name, start)

    def emit_interface(self, i: IRInterface) -> None:
        start = len(self.lines) + 1
        requires_clause = f" requires {', '.join(i.requires)}" if i.requires else ""
        view_type = render_type(i.view) if i.view is not None else "()"

        header = concat(
            [
                text(f"interface {i.name}{requires_clause} where"),
                nest(2, concat([line(), text(f"viewtype {view_type}")])),
            ]
        )
        self.lines_block(render(header))

        for c in i.choices:
            self.line("")
            impl_name = f"{c.name}Impl"
            arg_types = [
                render_type(a.type, as_arg=True) if a.type is not None else "()" for a in c.args
            ]
            return_type_str = (
                render_type(c.return_type, as_arg=True) if c.return_type is not None else "()"
            )
            sig_types = " -> ".join(
                [f"ContractId {i.name}", *arg_types, f"Update {return_type_str}"]
            )
            impl_name = self.private_name(impl_name)

            self.lines_block([f"  {impl_name} : {sig_types}"])
            self.line("")
            self.emit_choice(c, i.name, indent=1, delegate_to=impl_name)
        self.record(i.src, "interface", i.name, start)

    def private_name(self, name: str) -> str:
        return name[0].lower() + name[1:] if name else name

    def emit_interface_instance(
        self, ii: IRInterfaceInstance, template_name: str, field_types
    ) -> None:
        start = len(self.lines) + 1

        rendered_view = "-- MISSING view"
        if ii.view is not None:
            view_body = SelfFieldRewriter.rewrite_stmts(list(ii.view.body), field_types)
            view_expr = (
                view_body[-1].value
                if len(view_body) == 1 and isinstance(view_body[-1], IRReturn)
                else None
            )
            if view_expr is not None:
                rendered_view = f"let {SELF} = this in {render_expr(view_expr)}"

        header = concat(
            [
                text(f"interface instance {ii.interface} for {template_name} where"),
                nest(2, concat([line(), text(f"view = {rendered_view}")])),
            ]
        )
        self.lines_block(render(header, initial_indent=4))

        for c in ii.choices:
            self.line("")
            self.emit_interface_choice_impl(c, template_name, indent=3, field_types=field_types)
        self.record(ii.src, "interface_instance", f"{ii.interface}_for_{template_name}", start)

    def _render_header(self, imports, module_name: Optional[str]) -> str:
        header = f"module {module_name} where\n\n" if module_name is not None else ""
        builtins = collect_daml_builtins()
        for imp in imports:
            if imp.alias is not None:
                qualified = "qualified " if imp.qualified else ""
                header += f"import {qualified}{imp.module} as {imp.alias}\n\n"
                continue
            if imp.names is None:
                header += f"import {imp.module}\n\n"
                continue
            present = set(imp.names)
            rendered_names = []
            for n in imp.names:
                constructor_of = builtins.constructor_of.get(n)
                if constructor_of == n:
                    constructor_of = None
                if constructor_of is not None and constructor_of in present:
                    continue
                if constructor_of is not None and n not in builtins.constructor_types:
                    rendered_names.append(f"{constructor_of}({n})")
                    continue
                renamed = _external_builtin_renames().get(n, n)
                if not (renamed[0].isalpha() or renamed[0] == "_"):
                    renamed = f"({renamed})"
                if renamed in builtins.constructor_types or n in imp.constructor_names:
                    renamed = f"{renamed}(..)"
                rendered_names.append(renamed)
            header += f"import {imp.module} ({', '.join(rendered_names)})\n\n"
        return header

    def generate(self, module: IRModule, module_name: Optional[str] = None):
        return self._generate(module, module_name)

    def _generate(self, module: IRModule, module_name: Optional[str] = None):
        for alias in module.aliases:
            head = " ".join([alias.name, *alias.params])
            self.line(f"type {head} = {render_type(alias.type)}")
            self.line("")
        for s in module.structs:
            self.emit_struct(s)
            self.line("")
        for enum in module.enums:
            self.emit_enum(enum)
            self.line("")
        for variant in module.variants:
            self.emit_variant(variant)
            self.line("")
        for fn in module.functions:
            self.emit_function(fn)
            self.line("")
        for i in module.interfaces:
            self.emit_interface(i)
            self.line("")
        for t in module.templates:
            self.emit_template(t)
            self.line("")
        if self.hoisted:
            self.lines_block(self.hoisted)
        used = set(re.findall(r"_damlforge_\w+", "\n".join(self.lines)))
        for name, definition in _HELPER_DEFINITIONS.items():
            if name in used:
                self.lines_block([*definition, ""])

        body_source = "\n".join(self.lines).rstrip("\n") + "\n"
        mappings = self.mappings
        header_imports = module.imports
        if "fromSome " in body_source and not any(
            imp.module == "DA.Optional" and imp.names and "fromSome" in imp.names
            for imp in header_imports
        ):
            header_imports = (*header_imports, IRImport("DA.Optional", ("fromSome",)))
        header = self._render_header(header_imports, module_name)
        if header:
            offset = header.count("\n")
            mappings = [
                {
                    **m,
                    "generated": {
                        "startLine": m["generated"]["startLine"] + offset,
                        "endLine": m["generated"]["endLine"] + offset,
                    },
                }
                for m in mappings
            ]
        source = header + body_source
        return source, {"mappings": mappings}


def generate_daml(
    module: IRModule,
    module_name: Optional[str] = None,
    settings: DamlCodegenSettings = DamlCodegenSettings(),
):
    with stub_scope(module.stub_scope):
        known_effect_fns = collect_daml_builtins().ledger_effect_fns | frozenset(
            module.effect_functions
        )
        return Emitter(known_effect_fns, settings).generate(module, module_name)
