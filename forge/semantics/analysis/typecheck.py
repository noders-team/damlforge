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

"""Bidirectional, statement-ordered type checking for DamlForge expressions.

The checker deliberately separates two ideas which are easy to conflate:

* statements extend the local environment in source order; and
* expression types are inferred with expected-type propagation and unification.

That means the first element of a list never permanently decides its type.
Every element is checked against the same expected type or inference variable.
"""

from dataclasses import dataclass, field

from forge.ast import nodes as vy_ast
from forge.exceptions import CompilerPanic, DamlForgeException, ExceptionList
from forge.ir.lower import lower_type
from forge.ir.nodes import TypeRef
from forge.models.error_codes import ErrorCodes
from forge.semantics.primitive_specs import canonical_type_name, generic_spec, primitive_spec

_NUMERIC_TYPES = {"Int", "Decimal", "RelTime"}


def _optional_depth(term) -> int:
    depth = 0
    while isinstance(term, AppliedType) and term.name == "Optional" and term.args:
        depth += 1
        term = term.args[0]
    return depth


_INT_CONVERSION = ("Int", {"Int": None, "Decimal": "truncate"})
_TEXT_CONVERSION = (
    "Text",
    {"Text": None, "Int": "show", "Decimal": "show", "Bool": "show", "Party": "partyToText"},
)
_CONVERSIONS = {
    "int": _INT_CONVERSION,
    "Int": _INT_CONVERSION,
    "str": _TEXT_CONVERSION,
    "Text": _TEXT_CONVERSION,
}
_COLLECTION_TYPES = {"list", "set", "frozenset", "Optional"}
_MAP_TYPES = {"TextMap", "GenMap"}


def _safe_lower_type(node) -> TypeRef:
    try:
        return lower_type(node)
    except CompilerPanic:
        return TypeRef(f"<invalid:{type(node).__name__}>")


@dataclass(frozen=True)
class TypeVariable:
    """A type placeholder which is solved by the inference context."""

    number: int


@dataclass(frozen=True)
class AppliedType:
    """A type constructor whose arguments may still contain type variables."""

    name: str
    args: tuple["TypeTerm", ...] = ()


TypeTerm = TypeVariable | AppliedType


def _term_from_ref(type_ref: TypeRef) -> AppliedType:
    return AppliedType(
        canonical_type_name(type_ref.name), tuple(_term_from_ref(arg) for arg in type_ref.args)
    )


def _display_term(term: TypeTerm) -> str:
    if isinstance(term, TypeVariable):
        return f"?T{term.number}"
    if not term.args:
        return term.name
    return f"{term.name}[{', '.join(_display_term(arg) for arg in term.args)}]"


def _type_ref_from_term(term: TypeTerm | None) -> TypeRef | None:
    """Convert a solved inference term to the typed IR representation."""

    if isinstance(term, TypeVariable):
        return None
    if term is None:
        return None
    args = []
    for arg in term.args:
        arg_ref = _type_ref_from_term(arg)
        if arg_ref is None:
            return None
        args.append(arg_ref)
    return TypeRef(term.name, tuple(args))


class InferenceContext:
    """Creates and solves equality constraints between type terms."""

    def __init__(self) -> None:
        self._next_variable = 0
        self._substitutions: dict[int, TypeTerm] = {}

    def fresh(self) -> TypeVariable:
        variable = TypeVariable(self._next_variable)
        self._next_variable += 1
        return variable

    def resolve(self, term: TypeTerm) -> TypeTerm:
        if isinstance(term, TypeVariable):
            replacement = self._substitutions.get(term.number)
            if replacement is None:
                return term
            resolved = self.resolve(replacement)
            self._substitutions[term.number] = resolved
            return resolved
        return AppliedType(term.name, tuple(self.resolve(arg) for arg in term.args))

    def _occurs(self, variable: TypeVariable, term: TypeTerm) -> bool:
        term = self.resolve(term)
        if isinstance(term, TypeVariable):
            return term.number == variable.number
        return any(self._occurs(variable, arg) for arg in term.args)

    def unify(self, left: TypeTerm, right: TypeTerm) -> bool:
        left = self.resolve(left)
        right = self.resolve(right)
        if left == right:
            return True
        if isinstance(left, TypeVariable):
            if self._occurs(left, right):
                return False
            self._substitutions[left.number] = right
            return True
        if isinstance(right, TypeVariable):
            if self._occurs(right, left):
                return False
            self._substitutions[right.number] = left
            return True
        if left.name != right.name or len(left.args) != len(right.args):
            return False
        return all(self.unify(a, b) for a, b in zip(left.args, right.args))


@dataclass
class TypeEnvironment:
    """Lexical value bindings visible while checking a function or container."""

    values: dict[str, TypeTerm] = field(default_factory=dict)

    def child(self) -> "TypeEnvironment":
        return TypeEnvironment(dict(self.values))

    def bind(self, name: str, type_term: TypeTerm) -> None:
        self.values[name] = type_term

    def get(self, name: str) -> TypeTerm | None:
        return self.values.get(name)


class BidirectionalTypeChecker:
    """Check one top-level declaration using expected types and constraints."""

    def __init__(
        self,
        node: vy_ast.TopLevel,
        field_types: dict,
        return_types: dict,
        function_param_types: dict,
        function_param_names: dict,
        choice_names: dict[str, str],
        errors: ExceptionList,
        known_names: set[str] | None = None,
    ) -> None:
        self.node = node
        self.field_types = field_types
        self.return_types = return_types
        self.function_param_types = function_param_types
        self.function_param_names = function_param_names
        self.choice_names = choice_names
        self.errors = errors
        self.known_names = known_names or set()
        self.inference = InferenceContext()
        self._reported: set[tuple[int, str]] = set()
        self.aliases = _type_aliases(node)
        self._choice_return: TypeTerm | None = None

    def _error(self, node, message: str, *, call: bool = False) -> None:
        key = (id(node), message)
        if key in self._reported:
            return
        self._reported.add(key)
        code = ErrorCodes.DF1226.value if call else ErrorCodes.DF1230.value
        self.errors.append(DamlForgeException(message, node, code=code))

    def _term(self, value: TypeRef | TypeTerm | None) -> TypeTerm | None:
        if value is None:
            return None
        if isinstance(value, (TypeVariable, AppliedType)):
            return value
        return _term_from_ref(value)

    def _resolved(self, value: TypeTerm | None) -> TypeTerm | None:
        return None if value is None else self.inference.resolve(value)

    def _name(self, value: TypeTerm | None) -> str | None:
        value = self._resolved(value)
        return value.name if isinstance(value, AppliedType) else None

    def _known_type_name(self, name: str) -> bool:
        return (
            name in self.known_names
            or primitive_spec(name) is not None
            or generic_spec(name) is not None
        )

    def _expand(self, term: TypeTerm) -> TypeTerm:
        seen: set = set()
        while isinstance(term, AppliedType) and not term.args and term.name in self.aliases:
            if term.name in seen:
                break
            seen.add(term.name)
            term = _term_from_ref(self.aliases[term.name])
        return term

    def _is_unknown(self, term: TypeTerm) -> bool:
        return isinstance(term, AppliedType) and not self._known_type_name(term.name)

    def _compatible(
        self, actual: TypeTerm | None, expected: TypeTerm | None, node, *, call: bool = False
    ) -> bool:
        if actual is None or expected is None:
            return True
        actual = self._resolved(actual)
        expected = self._resolved(expected)
        if actual is None or expected is None:
            return True
        actual = self._expand(actual)
        expected = self._expand(expected)
        if self._is_unknown(actual) or self._is_unknown(expected):
            return True

        if self._name(expected) == "Optional":
            return self._compatible_optional(actual, expected, node, call=call)
        if self._name(actual) == "Optional":
            return self._unwrap_optional(actual, expected, node, call=call)

        if (
            isinstance(actual, AppliedType)
            and isinstance(expected, AppliedType)
            and actual.name == expected.name
            and len(actual.args) == len(expected.args)
        ):
            return all(
                self._compatible(a, b, node, call=call) for a, b in zip(actual.args, expected.args)
            )

        if self.inference.unify(actual, expected):
            return True

        self._error(
            node,
            f"value has type `{_display_term(actual)}`, but `{_display_term(expected)}` "
            "was expected",
            call=call,
        )
        return False

    def _compatible_optional(
        self, actual: TypeTerm, expected: TypeTerm, node, *, call: bool
    ) -> bool:
        if not isinstance(expected, AppliedType) or not expected.args:
            return True
        if self._name(actual) == "None":
            return True
        if isinstance(actual, AppliedType) and actual.name == "Optional" and actual.args:
            return self._compatible(actual.args[0], expected.args[0], node, call=call)
        if self._compatible(actual, expected.args[0], node, call=call):
            if not isinstance(node, vy_ast.NameConstant):
                node._metadata["needs_some_wrap"] = _optional_depth(expected) - _optional_depth(
                    actual
                )
            return True
        return False

    def _unwrap_optional(self, actual: TypeTerm, expected: TypeTerm, node, *, call: bool) -> bool:
        if isinstance(actual, AppliedType) and actual.args:
            if self._compatible(actual.args[0], expected, node, call=call):
                node._metadata["needs_from_some_unwrap"] = True
                return True
        return False

    def _finish(
        self, expr, actual: TypeTerm | None, expected: TypeTerm | None, *, call: bool = False
    ) -> TypeTerm | None:
        if actual is None:
            return None
        if expected is not None:
            self._compatible(actual, expected, expr, call=call)
        actual = self._resolved(actual)
        if actual is not None:
            expr._metadata["inferred_type"] = _display_term(actual)
            if type_ref := _type_ref_from_term(actual):
                expr._metadata["inferred_type_ref"] = type_ref
        return actual

    @staticmethod
    def _is_numeric(name: str | None) -> bool:
        return name in _NUMERIC_TYPES

    def _literal(self, expr, expected: TypeTerm | None) -> TypeTerm | None:
        if isinstance(expr, vy_ast.Int):
            if self._name(expected) == "Decimal":
                return AppliedType("Decimal")
            return AppliedType("Int")
        if isinstance(expr, vy_ast.Decimal):
            return AppliedType("Decimal")
        if isinstance(expr, vy_ast.Str):
            return AppliedType("Text")
        if isinstance(expr, vy_ast.NameConstant):
            if expr.value is None:
                return AppliedType("None")
            if isinstance(expr.value, bool):
                return AppliedType("Bool")
        return None

    def _attribute_type(self, expr, env: TypeEnvironment) -> TypeTerm | None:
        if expr._metadata.get("is_enum_value"):
            enum_name = expr._metadata.get("enum_name")
            if enum_name is not None:
                return AppliedType(enum_name)
            if isinstance(expr.value, vy_ast.Name):
                return AppliedType(expr.value.id)
        if isinstance(expr.value, vy_ast.Name) and (
            primitive_spec(expr.value.id) is not None or generic_spec(expr.value.id) is not None
        ):
            self._error(
                expr,
                f"`{expr.value.id}` is a type name, not a value; qualified field access is "
                "not supported",
            )
            return None
        base_type = (
            env.get("self")
            if isinstance(expr.value, vy_ast.Name) and expr.value.id == "self"
            else self.check_expr(expr.value, None, env)
        )
        name = self._name(base_type)
        if name is None:
            return None
        fields = self.field_types.get(name)
        if fields is None:
            return None
        return self._term(fields.get(expr.attr))

    def _element_type(self, value: TypeTerm | None) -> TypeTerm | None:
        value = self._resolved(value)
        if not isinstance(value, AppliedType):
            return None
        if value.name in _COLLECTION_TYPES and value.args:
            return value.args[0]
        if value.name == "TextMap":
            return AppliedType("Text")
        if value.name in _MAP_TYPES and value.args:
            return value.args[-1]
        return None

    def _key_type(self, value: TypeTerm | None) -> TypeTerm | None:
        value = self._resolved(value)
        if not isinstance(value, AppliedType):
            return None
        if value.name == "TextMap":
            return AppliedType("Text")
        if value.name == "GenMap" and value.args:
            return value.args[0]
        if value.name in _COLLECTION_TYPES - {"Optional"} and value.args:
            return value.args[0]
        return None

    def _check_args(self, args, types, env: TypeEnvironment) -> None:
        for arg, type_ref in zip(args, types):
            self.check_expr(arg, self._term(type_ref), env, call=True)

    def _check_call_arguments(
        self, call: vy_ast.Call, env: TypeEnvironment, function_name: str | None
    ) -> None:
        if function_name is not None:
            expected = self.function_param_types.get(function_name)
            if expected is not None:
                names = self.function_param_names.get(function_name, ())
                if not call.keywords:
                    self._check_args(call.args, expected, env)
                    return

                if len(call.args) > len(expected):
                    self._error(
                        call,
                        f"`{function_name}(...)` called with {len(call.args)} positional "
                        f"arguments, but it takes {len(expected)} arguments",
                        call=True,
                    )

                supplied = set(range(min(len(call.args), len(expected))))
                self._check_args(call.args, expected, env)
                for keyword in call.keywords:
                    if keyword.arg not in names:
                        self._error(
                            keyword.value,
                            f"`{function_name}` has no argument `{keyword.arg}`",
                            call=True,
                        )
                        continue
                    index = names.index(keyword.arg)
                    if index in supplied:
                        self._error(
                            keyword.value,
                            f"`{function_name}` received argument `{keyword.arg}` more "
                            "than once",
                            call=True,
                        )
                        continue
                    supplied.add(index)
                    self.check_expr(keyword.value, self._term(expected[index]), env, call=True)
                return

            fields = self.field_types.get(function_name)
            if fields is not None:
                self._check_args(call.args, fields.values(), env)
                for keyword in call.keywords:
                    type_ref = fields.get(keyword.arg)
                    if type_ref is None:
                        self._error(
                            keyword.value,
                            f"`{function_name}` has no field `{keyword.arg}`",
                            call=True,
                        )
                        continue
                    self.check_expr(keyword.value, self._term(type_ref), env, call=True)
                return

        for arg in call.args:
            self.check_expr(arg, None, env, call=True)
        for keyword in call.keywords:
            self.check_expr(keyword.value, None, env, call=True)

    def _call_type(
        self, call: vy_ast.Call, expected: TypeTerm | None, env: TypeEnvironment
    ) -> TypeTerm | None:
        function_name = call.func.id if isinstance(call.func, vy_ast.Name) else None

        if return_type := call._metadata.get("return_type"):
            self._check_call_arguments(call, env, None)
            return AppliedType(canonical_type_name(return_type))

        for metadata, type_name in (
            ("datetime_combine_arguments", "Time"),
            ("fromtimestamp_argument", "Time"),
            ("date_arguments", "Date"),
            ("timedelta_arguments", "RelTime"),
        ):
            if metadata in call._metadata:
                self._check_call_arguments(call, env, None)
                return AppliedType(type_name)

        if isinstance(call.func, vy_ast.Attribute):
            if isinstance(call.func.value, vy_ast.Name):
                qualified = f"{call.func.value.id}.{call.func.attr}"
                if qualified in self.field_types:
                    self._check_call_arguments(call, env, qualified)
                    return None
                if (
                    primitive_spec(call.func.value.id) is not None
                    or generic_spec(call.func.value.id) is not None
                ):
                    self._error(
                        call,
                        f"`{call.func.value.id}` is a type name, not a value; qualified "
                        "method access is not supported",
                    )
                    return None
            if isinstance(call.func.value, vy_ast.Name):
                template_choice = f"{call.func.value.id}.{call.func.attr}"
                if template_choice in self.choice_names and env.get(call.func.value.id) is None:
                    self._check_call_arguments(call, env, template_choice)
                    call._metadata["daml_choice_call"] = self.choice_names[template_choice]
                    return self._term(self.return_types.get(template_choice))
            receiver = self.check_expr(call.func.value, None, env)
            receiver_name = self._name(receiver)
            if (
                receiver_name == "ContractId"
                and isinstance(receiver, AppliedType)
                and receiver.args
            ):
                receiver_name = self._name(receiver.args[0])
            method_name = f"{receiver_name}.{call.func.attr}" if receiver_name is not None else None
            if method_name is not None and method_name in self.function_param_types:
                self._check_call_arguments(call, env, method_name)
                choice_name = self.choice_names.get(method_name)
                if choice_name is not None:
                    # TODO: Refactor this hardcoded metadata key into a typed IR marker.
                    call._metadata["daml_choice_call"] = choice_name
                return self._term(self.return_types.get(method_name))
            if call.func.attr == "append":
                element = self._element_type(receiver)
                if call.args:
                    self.check_expr(call.args[0], element, env, call=True)
                return AppliedType("Unit")
            self._check_call_arguments(call, env, None)
            return None

        conversion = _CONVERSIONS.get(function_name) if function_name is not None else None
        if conversion is not None and len(call.args) == 1 and not call.keywords:
            conversion_result, converters = conversion
            source = self._name(self.check_expr(call.args[0], None, env, call=True))
            if source in converters:
                call._metadata["conversion"] = converters[source]
            elif source is not None:
                allowed = ", ".join(converters)
                message = f"`{function_name}(...)` cannot convert `{source}`; expected {allowed}"
                self._error(call, message, call=True)
            return AppliedType(conversion_result)

        self._check_call_arguments(call, env, function_name)
        builtin_types = {
            "int": "Int",
            "Int": "Int",
            "float": "Decimal",
            "Decimal": "Decimal",
            "str": "Text",
            "Text": "Text",
            "bool": "Bool",
            "Bool": "Bool",
            "len": "Int",
            "range": "list",
        }
        if function_name in builtin_types:
            type_name = builtin_types[function_name]
            if type_name == "list":
                return AppliedType("list", (AppliedType("Int"),))
            return AppliedType(type_name)
        if function_name == "fetch" and call.args:
            cid = self.check_expr(call.args[0], None, env)
            if isinstance(cid, AppliedType) and cid.name == "ContractId" and cid.args:
                return cid.args[0]
        if function_name == "exercise_by_key" and len(call.args) == 3:
            return self.check_expr(call.args[2], None, env)
        if function_name == "create" and call.args:
            payload = self.check_expr(call.args[0], None, env)
            if payload is not None:
                return AppliedType("ContractId", (payload,))
        if function_name == "error":
            return expected
        return_type = self.return_types.get(function_name) if function_name else None
        return self._term(return_type)

    def _binary_type(
        self, expr: vy_ast.BinOp, expected: TypeTerm | None, env: TypeEnvironment
    ) -> TypeTerm | None:
        if expr._metadata.get("add_rel_time"):
            self.check_expr(expr.left, AppliedType("Time"), env)
            self.check_expr(expr.right, AppliedType("RelTime"), env)
            return AppliedType("Time")
        if expr._metadata.get("sub_time"):
            self.check_expr(expr.left, AppliedType("Time"), env)
            self.check_expr(expr.right, AppliedType("Time"), env)
            return AppliedType("RelTime")

        op_type = type(expr.op)
        supported = (
            vy_ast.Add,
            vy_ast.Sub,
            vy_ast.Mult,
            vy_ast.Div,
            vy_ast.FloorDiv,
            vy_ast.Mod,
            vy_ast.Pow,
        )
        if op_type not in supported:
            operator = getattr(expr.op, "_pretty", op_type.__name__)
            self._error(expr, f"operator `{operator}` is not supported", call=False)
            self.check_expr(expr.left, None, env)
            self.check_expr(expr.right, None, env)
            return None
        if op_type in (vy_ast.Add, vy_ast.Sub, vy_ast.Mult, vy_ast.Div):
            operand_expected = expected if self._is_numeric(self._name(expected)) else None
            if op_type is vy_ast.Sub and self._name(expected) == "RelTime":
                operand_expected = None
            left = self.check_expr(expr.left, operand_expected, env)
            right = self.check_expr(expr.right, operand_expected, env)
            left_name = self._name(left)
            right_name = self._name(right)

            if left_name is None:
                return right
            if right_name is None:
                return left
            if op_type is vy_ast.Sub and {left_name, right_name} == {"Time"}:
                return AppliedType("RelTime")
            if op_type is vy_ast.Sub and (left_name, right_name) == ("Time", "RelTime"):
                return AppliedType("Time")
            if op_type is vy_ast.Add and {left_name, right_name} == {"Time", "RelTime"}:
                return AppliedType("Time")
            if left_name == right_name and self._is_numeric(left_name):
                return AppliedType(left_name)
            self._compatible(right, left, expr)
            return left

        if op_type is vy_ast.Pow and isinstance(expr.right, vy_ast.Int) and expr.right.value < 0:
            self._error(expr.right, "`**` exponent must be non-negative; Daml `^` rejects it")
        operand_type = AppliedType("Int") if op_type in (vy_ast.Mod, vy_ast.Pow) else None
        left = self.check_expr(expr.left, operand_type, env)
        right = self.check_expr(expr.right, operand_type, env)
        return left or right

    def _list_type(self, expr, expected, env, call: bool) -> TypeTerm:
        element_type = None
        if self._name(expected) == "list" and isinstance(expected, AppliedType):
            element_type = expected.args[0] if expected.args else None
        element_type = element_type or self.inference.fresh()
        for element in expr.elements:
            self.check_expr(element, element_type, env, call=call)
        return AppliedType("list", (self.inference.resolve(element_type),))

    def _tuple_type(self, expr, expected, env, call: bool) -> TypeTerm:
        expected_args: tuple[TypeTerm, ...] = ()
        if isinstance(expected, AppliedType) and self._name(expected) == "tuple":
            expected_args = expected.args

        actual_args = []
        for index, element in enumerate(expr.elements):
            item_expected = expected_args[index] if index < len(expected_args) else None
            item_type = self.check_expr(element, item_expected, env, call=call)
            actual_args.append(item_type if item_type is not None else self.inference.fresh())
        return AppliedType("tuple", tuple(actual_args))

    def _dict_type(self, expr, expected, env, call: bool) -> TypeTerm | None:
        map_name = self._name(expected)
        if map_name == "TextMap" and isinstance(expected, AppliedType):
            value_expected = expected.args[0] if expected.args else None
            for key, value in zip(expr.keys, expr.values):
                self.check_expr(key, AppliedType("Text"), env, call=call)
                self.check_expr(value, value_expected, env, call=call)
            expr._metadata["map_kind"] = "TextMap"
            return expected

        if map_name == "GenMap" and isinstance(expected, AppliedType):
            key_expected = expected.args[0] if expected.args else None
            value_expected = expected.args[1] if len(expected.args) > 1 else None
            for key, value in zip(expr.keys, expr.values):
                self.check_expr(key, key_expected, env, call=call)
                self.check_expr(value, value_expected, env, call=call)
            expr._metadata["map_kind"] = "GenMap"
            return expected

        for key, value in zip(expr.keys, expr.values):
            self.check_expr(key, None, env, call=call)
            self.check_expr(value, None, env, call=call)
        return None

    def _compare_type(self, expr, env: TypeEnvironment) -> TypeTerm:
        left = self.check_expr(expr.left, None, env)
        if isinstance(expr.op, (vy_ast.In, vy_ast.NotIn)):
            right = self.check_expr(expr.right, None, env)
            key_type = self._key_type(right)
            if left is not None and key_type is not None:
                self._compatible(left, key_type, expr.left)
        else:
            right = self.check_expr(expr.right, left, env)
            if isinstance(expr.op, (vy_ast.Eq, vy_ast.NotEq)) and left is not None:
                self._compatible(right, left, expr.right)
        return AppliedType("Bool")

    def _subscript_type(self, expr, env: TypeEnvironment) -> TypeTerm | None:
        value = self.check_expr(expr.value, None, env)
        tuple_value = self._resolved(value)
        if self._name(tuple_value) == "Optional" and isinstance(tuple_value, AppliedType):
            tuple_value = self._resolved(tuple_value.args[0]) if tuple_value.args else None
        if (
            isinstance(tuple_value, AppliedType)
            and tuple_value.name == "tuple"
            and isinstance(expr.slice, vy_ast.Int)
            and 0 <= expr.slice.value < len(tuple_value.args)
        ):
            self.check_expr(expr.slice, AppliedType("Int"), env)
            return tuple_value.args[expr.slice.value]
        if self._name(value) in _MAP_TYPES and isinstance(value, AppliedType):
            key_type = value.args[0] if value.args else None
        else:
            key_type = AppliedType("Int")
        self.check_expr(expr.slice, key_type, env)
        return self._element_type(value)

    def _conditional_type(self, expr, expected, env: TypeEnvironment) -> TypeTerm | None:
        self.check_expr(expr.test, AppliedType("Bool"), env)
        body = self.check_expr(expr.body, expected, env)
        orelse = self.check_expr(expr.orelse, expected or body, env)
        if body is not None and orelse is not None:
            self._compatible(orelse, body, expr)
        return body or orelse

    def check_expr(
        self, expr, expected: TypeRef | TypeTerm | None, env: TypeEnvironment, *, call: bool = False
    ) -> TypeTerm | None:
        if expr is None:
            return None
        expected_term = self._term(expected)

        literal = self._literal(expr, expected_term)
        if literal is not None:
            return self._finish(expr, literal, expected_term, call=call)
        if isinstance(expr, vy_ast.Name):
            return self._finish(expr, env.get(expr.id), expected_term, call=call)
        if isinstance(expr, vy_ast.Attribute):
            return self._finish(expr, self._attribute_type(expr, env), expected_term, call=call)
        if isinstance(expr, vy_ast.List):
            return self._finish(
                expr, self._list_type(expr, expected_term, env, call), expected_term, call=call
            )
        if isinstance(expr, vy_ast.Tuple):
            return self._finish(
                expr, self._tuple_type(expr, expected_term, env, call), expected_term, call=call
            )
        if isinstance(expr, vy_ast.Dict):
            actual = self._dict_type(expr, expected_term, env, call)
            return self._finish(expr, actual, expected_term, call=call)
        if isinstance(expr, vy_ast.Call):
            return self._finish(
                expr, self._call_type(expr, expected_term, env), expected_term, call=call
            )
        if isinstance(expr, vy_ast.UnaryOp):
            if isinstance(expr.op, vy_ast.Not):
                self.check_expr(expr.operand, AppliedType("Bool"), env)
                return self._finish(expr, AppliedType("Bool"), expected_term, call=call)
            if not isinstance(expr.op, vy_ast.USub):
                operator = getattr(expr.op, "_pretty", type(expr.op).__name__)
                self._error(expr, f"operator `{operator}` is not supported", call=False)
                self.check_expr(expr.operand, None, env)
                return None
            operand = self.check_expr(expr.operand, expected_term, env)
            return self._finish(expr, operand, expected_term, call=call)
        if isinstance(expr, vy_ast.BinOp):
            return self._finish(
                expr, self._binary_type(expr, expected_term, env), expected_term, call=call
            )
        if isinstance(expr, vy_ast.BoolOp):
            for value in expr.values:
                self.check_expr(value, AppliedType("Bool"), env)
            return self._finish(expr, AppliedType("Bool"), expected_term, call=call)
        if isinstance(expr, vy_ast.Compare):
            return self._finish(expr, self._compare_type(expr, env), expected_term, call=call)
        if isinstance(expr, vy_ast.Subscript):
            return self._finish(expr, self._subscript_type(expr, env), expected_term, call=call)
        if isinstance(expr, vy_ast.IfExp):
            return self._finish(
                expr, self._conditional_type(expr, expected_term, env), expected_term, call=call
            )

        for child in expr.get_descendants(vy_ast.ExprNode, include_self=False):
            self.check_expr(child, None, env, call=call)
        return None

    def _check_statements(
        self, statements: list, env: TypeEnvironment, return_type: TypeTerm | None
    ) -> None:
        for stmt in statements:
            if isinstance(stmt, vy_ast.AnnAssign):
                expected = self._term(_safe_lower_type(stmt.annotation))
                if stmt.value is not None:
                    self.check_expr(stmt.value, expected, env)
                if isinstance(stmt.target, vy_ast.Name) and expected is not None:
                    env.bind(stmt.target.id, expected)
                elif isinstance(stmt.target, vy_ast.Attribute):
                    self.check_expr(stmt.target, expected, env)
            elif isinstance(stmt, vy_ast.Assign):
                existing = None
                if isinstance(stmt.target, vy_ast.Name):
                    existing = env.get(stmt.target.id)
                elif isinstance(stmt.target, vy_ast.Attribute):
                    existing = self._attribute_type(stmt.target, env)
                actual = self.check_expr(stmt.value, existing, env)
                if isinstance(stmt.target, vy_ast.Name) and actual is not None:
                    env.bind(stmt.target.id, actual)
                elif isinstance(stmt.target, vy_ast.Attribute) and existing is not None:
                    self._compatible(actual, existing, stmt.value)
            elif isinstance(stmt, vy_ast.AugAssign):
                self.check_expr(stmt.target, None, env)
                self.check_expr(stmt.value, None, env)
            elif isinstance(stmt, vy_ast.Return):
                actual = self.check_expr(stmt.value, return_type, env)
                if return_type is None and self._choice_return is not None:
                    self._wrap_choice_return(stmt.value, actual, env)
            elif isinstance(stmt, vy_ast.If):
                self.check_expr(stmt.test, AppliedType("Bool"), env)
                body_env = env.child()
                orelse_env = env.child()
                self._check_statements(stmt.body, body_env, return_type)
                self._check_statements(stmt.orelse, orelse_env, return_type)
                for name in set(body_env.values) & set(orelse_env.values):
                    body_type = body_env.get(name)
                    orelse_type = orelse_env.get(name)
                    if body_type is not None and orelse_type is not None:
                        if self.inference.unify(body_type, orelse_type):
                            env.bind(name, self.inference.resolve(body_type))
            elif isinstance(stmt, vy_ast.For):
                iterator = self.check_expr(stmt.iter, None, env)
                element = self._element_type(iterator)
                body_env = env.child()
                target = stmt.target.target if hasattr(stmt.target, "target") else stmt.target
                annotation = getattr(stmt.target, "annotation", None)
                target_type = self._term(_safe_lower_type(annotation)) if annotation else element
                if isinstance(target, vy_ast.Name) and target_type is not None:
                    body_env.bind(target.id, target_type)
                self._check_statements(stmt.body, body_env, return_type)
            elif isinstance(stmt, vy_ast.Assert):
                self.check_expr(stmt.test, AppliedType("Bool"), env)
                self.check_expr(stmt.msg, AppliedType("Text"), env)
            elif isinstance(stmt, vy_ast.Expr):
                self.check_expr(stmt.value, None, env)
            elif isinstance(stmt, vy_ast.Log):
                self.check_expr(stmt.value, None, env)

    def _base_environment(self, container_name: str | None = None) -> TypeEnvironment:
        env = TypeEnvironment()
        if container_name is not None:
            env.bind("self", AppliedType(container_name))
            for name, type_ref in self.field_types.get(container_name, {}).items():
                term = self._term(type_ref)
                if term is not None:
                    env.bind(name, term)
        return env

    def _check_function(self, fn: vy_ast.FunctionDef, container_name: str | None) -> None:
        env = self._base_environment(container_name)
        for argument in fn.args.args:
            if argument.arg == "self":
                continue
            if argument.annotation is not None:
                term = self._term(_safe_lower_type(argument.annotation))
                if term is not None:
                    env.bind(argument.arg, term)
        return_type = self._term(_safe_lower_type(fn.returns)) if fn.returns is not None else None
        if any(
            (isinstance(decorator, vy_ast.Name) and decorator.id == "choice")
            or (
                isinstance(decorator, vy_ast.Call)
                and isinstance(decorator.func, vy_ast.Name)
                and decorator.func.id == "choice"
            )
            for decorator in fn.decorator_list
        ):
            # Choice bodies are lowered as ledger actions.  Their Python
            # return annotation describes the generated choice result, while
            # the source body may return the action's terminal value.
            self._choice_return = return_type if _optional_depth(return_type) else None
            return_type = None
        self._check_statements(fn.body, env, return_type)
        self._choice_return = None

    def _wrap_choice_return(self, value, actual: TypeTerm | None, env: TypeEnvironment) -> None:
        if actual is None and isinstance(value, (vy_ast.Dict, vy_ast.List)):
            inner = self._choice_return
            while isinstance(inner, AppliedType) and inner.name == "Optional" and inner.args:
                inner = inner.args[0]
            actual = self.check_expr(value, inner, env)
        if value is None or self._name(actual) in (None, "None"):
            return
        depth = _optional_depth(self._choice_return) - _optional_depth(actual)
        if depth > 0:
            value._metadata["needs_some_wrap"] = depth

    def _check_container_values(self, container: vy_ast.TopLevel) -> None:
        if not isinstance(container, (vy_ast.StructDef, vy_ast.TemplateDef)):
            return
        env = self._base_environment(container.name)
        for stmt in container.body:
            if not isinstance(stmt, vy_ast.AnnAssign) or not isinstance(stmt.target, vy_ast.Name):
                continue
            type_ref = self.field_types.get(container.name, {}).get(stmt.target.id)
            type_term = self._term(type_ref)
            if stmt.value is not None:
                self.check_expr(stmt.value, type_term, env)
            if type_term is not None:
                env.bind(stmt.target.id, type_term)

    def run(self) -> None:
        self._check_container_values(self.node)
        if isinstance(self.node, vy_ast.FunctionDef):
            self._check_function(self.node, None)
        elif isinstance(self.node, (vy_ast.TemplateDef, vy_ast.InterfaceDef)):
            for child in self.node.body:
                if isinstance(child, vy_ast.FunctionDef):
                    self._check_function(
                        child, self.node.name if isinstance(self.node, vy_ast.TemplateDef) else None
                    )


def _type_aliases(node) -> dict:
    from forge.ast.utils import type_aliases
    from forge.builtins import collect_daml_builtins
    from forge.ir.lower import lower_type

    module = node if isinstance(node, vy_ast.Module) else node.module_node
    local = type_aliases(module) if module is not None else {}
    return {
        **collect_daml_builtins().type_aliases,
        **{name: _safe_lower_type(value) for name, value in local.items()},
    }


def check_bidirectional_types(
    node: vy_ast.TopLevel,
    field_types: dict,
    return_types: dict,
    function_param_types: dict,
    function_param_names: dict,
    choice_names: dict[str, str],
    errors: ExceptionList,
    known_names: set[str] | None = None,
) -> None:
    """Run type-directed expression checking for one top-level AST node."""

    BidirectionalTypeChecker(
        node,
        field_types,
        return_types,
        function_param_types,
        function_param_names,
        choice_names,
        errors,
        known_names,
    ).run()
