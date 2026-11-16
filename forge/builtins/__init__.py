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

import dataclasses
import datetime as datetime_module
import importlib
import inspect
import pkgutil
import typing
from abc import ABC, abstractmethod
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Optional, Tuple

from forge.ast import nodes as vy_ast
from forge.exceptions import DamlForgeException, ExceptionList

if TYPE_CHECKING:
    from forge.ir.nodes import TypeRef


@dataclasses.dataclass(frozen=True)
class VyDecorator(ABC):
    def __call__(self, fn):
        fn.__vy_decorator__ = True
        return fn

    @classmethod
    @abstractmethod
    def __post_vy_check__(
        cls, container: vy_ast.TopLevel, fn: vy_ast.FunctionDef, dec, errors: ExceptionList
    ):
        pass  # This method can be overridden in subclasses to perform additional checks


@dataclasses.dataclass(frozen=True)
class DamlName(VyDecorator):
    name: str
    module: Optional[str] = None
    is_ledger_effect: bool = False

    def __call__(self, fn):
        fn = super().__call__(fn)

        fn.__daml_name__ = self.name
        fn.__daml_module__ = self.module
        fn.__daml_ledger_effect__ = self.is_ledger_effect
        return fn

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DamlName decorator requires a non-empty name")

    @classmethod
    def __post_vy_check__(
        cls, container: vy_ast.TopLevel, fn: vy_ast.FunctionDef, dec, errors: ExceptionList
    ):
        from forge.models.error_codes import ErrorCodes

        if (
            isinstance(dec, vy_ast.Call)
            and len(dec.args) == 1
            and all(
                keyword.arg == "is_ledger_effect"
                and isinstance(keyword.value, vy_ast.NameConstant)
                and isinstance(keyword.value.value, bool)
                for keyword in dec.keywords
            )
            and isinstance(dec.args[0], vy_ast.Str)
        ):
            return

        errors.append(
            DamlForgeException(
                f"`@{cls.__name__}` on `{container.name}.{fn.name}` takes exactly one "
                "string literal argument",
                fn,
                code=ErrorCodes.DF1221.value,
            )
        )


class Consuming(Enum):
    PRE_CONSUMING = "preconsuming"
    CONSUMING = "consuming"
    NON_CONSUMING = "nonconsuming"
    POST_CONSUMING = "postconsuming"


def _normalize_consuming(consuming) -> Consuming:
    if isinstance(consuming, Consuming):
        return consuming
    if isinstance(consuming, bool):
        return Consuming.CONSUMING if consuming else Consuming.NON_CONSUMING
    if isinstance(consuming, str):
        try:
            return Consuming(consuming)
        except ValueError:
            pass
    valid_strings = [c.value for c in Consuming]
    raise TypeError(
        "choice(consuming=...) must be True/False, a Consuming member, or one of "
        f"{valid_strings!r}; got {consuming!r}"
    )


@dataclasses.dataclass(frozen=True)
class ChoiceMeta:
    controller: Any
    consuming: Consuming
    observer: Optional[Any] = None


CONSUMING_VARIANTS = {"preconsuming", "consuming", "nonconsuming", "postconsuming"}


def _consuming_variant_from_ast(node) -> Optional[str]:
    if isinstance(node, vy_ast.NameConstant) and isinstance(node.value, bool):
        return "consuming" if node.value else "nonconsuming"
    if isinstance(node, vy_ast.Str) and node.value in CONSUMING_VARIANTS:
        return node.value
    return None


@dataclasses.dataclass(frozen=True)
class choice(VyDecorator):
    controller: Any
    consuming: Any
    observer: Optional[Any] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "consuming", _normalize_consuming(self.consuming))

    def __call__(self, fn):
        fn = super().__call__(fn)
        fn.__choice__ = ChoiceMeta(
            controller=self.controller, consuming=self.consuming, observer=self.observer
        )
        return fn

    @classmethod
    def __post_vy_check__(
        cls, container: vy_ast.TopLevel, fn: vy_ast.FunctionDef, dec, errors: ExceptionList
    ):
        from forge.models.error_codes import ErrorCodes

        kwargs = {kw.arg: kw.value for kw in dec.keywords} if isinstance(dec, vy_ast.Call) else {}
        qualified_name = f"{container.name}.{fn.name}"

        if fn.name.startswith("_"):
            errors.append(
                DamlForgeException(
                    f"Choice `{qualified_name}` starts with `_` - Python's own "
                    "convention for a private method, which contradicts `@choice` "
                    "making it a public, externally-exercisable Daml choice",
                    fn,
                    code=ErrorCodes.DF1218.value,
                )
            )

        if "controller" not in kwargs:
            errors.append(
                DamlForgeException(
                    f"Choice `{qualified_name}` must declare an explicit `controller`",
                    fn,
                    code=ErrorCodes.DF1203.value,
                )
            )

        consuming = kwargs.get("consuming")
        variant = None if consuming is None else _consuming_variant_from_ast(consuming)
        if variant is None:
            errors.append(
                DamlForgeException(
                    f"Choice `{qualified_name}` must explicitly declare `consuming` as "
                    f"True/False or one of {sorted(CONSUMING_VARIANTS)!r}",
                    fn,
                    code=ErrorCodes.DF1203.value,
                )
            )

        cls._check_controller_observer_names(container, fn, kwargs, qualified_name, errors)

    @staticmethod
    def _check_controller_observer_names(
        container: vy_ast.TopLevel,
        fn: vy_ast.FunctionDef,
        kwargs: dict,
        qualified_name: str,
        errors: ExceptionList,
    ) -> None:
        from forge.models.error_codes import ErrorCodes

        if not isinstance(container, vy_ast.TemplateDef):
            # interface/instance choices commonly reference the interface's view
            # type (e.g. Daml's `(view this).admin`), which `.vy` has no bare-name
            # equivalent for yet - only template choices have concrete fields and
            # with-arguments to validate names against.
            return

        with_arg_names = {a.arg for a in fn.args.args if a.arg != "self"}

        reserved = ("signatory", "observer", "key", "maintainer", "ensure")
        field_names = {
            stmt.target.id
            for stmt in container.body
            if (
                isinstance(stmt, vy_ast.AnnAssign)
                and isinstance(stmt.target, vy_ast.Name)
                and stmt.target.id not in reserved
            )
        }

        valid_names = with_arg_names | field_names | {"self"}

        for kw_name in ("controller", "observer"):
            expr = kwargs.get(kw_name)
            if expr is None:
                continue
            for name in expr.get_descendants(vy_ast.Name, include_self=True):
                parent = name.parent
                if isinstance(parent, vy_ast.Call) and parent.func is name:
                    continue
                if isinstance(parent, vy_ast.Attribute) and parent.value is name:
                    if name.id == "self" and parent.attr not in field_names:
                        errors.append(
                            DamlForgeException(
                                f"`{kw_name}=` of choice `{qualified_name}` references "
                                f"`self.{parent.attr}`, but `{parent.attr}` is not a field of "
                                f"template `{container.name}` ({sorted(field_names)!r}) - "
                                "Daml would either resolve it to an unrelated field of the "
                                "same name elsewhere in the module, or fail with a confusing "
                                "'Ambiguous occurrence'/'not in scope' error at `damlc` time",
                                parent,
                                code=ErrorCodes.DF1224.value,
                            )
                        )
                    continue
                if name.id in valid_names:
                    continue
                errors.append(
                    DamlForgeException(
                        f"`{kw_name}=` of choice `{qualified_name}` references `{name.id}`, "
                        f"which is not one of this choice's own arguments "
                        f"({sorted(with_arg_names)!r})"
                        + (
                            f", the template's fields ({sorted(field_names)!r})"
                            if field_names
                            else ""
                        )
                        + " or `self` - Daml's own field resolution would otherwise "
                        "either silently pick a different, unrelated field of the same "
                        "name elsewhere in the module, or fail with a confusing "
                        "'Ambiguous occurrence'/'not in scope' error at `damlc` time",
                        name,
                        code=ErrorCodes.DF1224.value,
                    )
                )


class template(VyDecorator):
    def __new__(cls, cls_obj):
        cls_obj.__vy_decorator__ = True
        cls_obj.__template__ = True
        return cls_obj

    @classmethod
    def __post_vy_check__(
        cls, container: vy_ast.TopLevel, fn: vy_ast.FunctionDef, dec, errors: ExceptionList
    ):
        from forge.models.error_codes import ErrorCodes

        if isinstance(dec, vy_ast.Call):
            errors.append(
                DamlForgeException(
                    f"`@{cls.__name__}` on `{container.name}` takes no arguments",
                    fn,
                    code=ErrorCodes.DF1205.value,
                )
            )


class variant(VyDecorator):
    def __new__(cls, cls_obj):
        cls_obj.__vy_decorator__ = True
        cls_obj.__variant__ = True
        return cls_obj

    @classmethod
    def __post_vy_check__(
        cls, container: vy_ast.TopLevel, fn: vy_ast.FunctionDef, dec, errors: ExceptionList
    ):
        from forge.models.error_codes import ErrorCodes

        if isinstance(dec, vy_ast.Call):
            errors.append(
                DamlForgeException(
                    f"`@{cls.__name__}` on `{container.name}` takes no arguments",
                    fn,
                    code=ErrorCodes.DF1205.value,
                )
            )


class view(VyDecorator):
    def __new__(cls, fn):
        fn.__vy_decorator__ = True
        fn.__view__ = True
        return fn

    @classmethod
    def __post_vy_check__(
        cls, container: vy_ast.TopLevel, fn: vy_ast.FunctionDef, dec, errors: ExceptionList
    ):
        from forge.models.error_codes import ErrorCodes

        if isinstance(dec, vy_ast.Call):
            errors.append(
                DamlForgeException(
                    f"`@{cls.__name__}` on `{container.name}.{fn.name}` takes no arguments",
                    fn,
                    code=ErrorCodes.DF1206.value,
                )
            )

        if isinstance(container, vy_ast.TemplateDef) and not container.bases:
            errors.append(
                DamlForgeException(
                    f"`@{cls.__name__}` on `{container.name}.{fn.name}` is only valid "
                    "inside `interface`, or a `template` that implements one via "
                    f"`template {container.name}(SomeInterface):`",
                    fn,
                    code=ErrorCodes.DF1207.value,
                )
            )


@dataclasses.dataclass(frozen=True)
class InterfaceMeta:
    view: Any
    requires: Tuple[Any, ...] = dataclasses.field(default_factory=tuple)


def interface(*, view, requires=()):
    def wrap(cls):
        cls.__interface__ = InterfaceMeta(view=view, requires=tuple(requires))
        return cls

    return wrap


@dataclasses.dataclass(frozen=True)
class DamlCaseAccessor(VyDecorator):
    constructor: str
    arity: int = 1
    result_constructor: Optional[str] = None
    field_names: Tuple[str, ...] = dataclasses.field(default_factory=tuple)

    def __call__(self, fn):
        fn.__daml_case_accessor__ = self.constructor
        fn.__daml_case_accessor_arity__ = self.arity
        fn.__daml_case_accessor_result_constructor__ = self.result_constructor
        fn.__daml_case_accessor_field_names__ = self.field_names
        return fn

    @classmethod
    def __post_vy_check__(cls, container, fn, dec, errors):
        return None


class variant_record(VyDecorator):
    """Mark a variant constructor as using Daml's named ``with`` payload syntax."""

    def __new__(cls, fn):
        fn.__vy_decorator__ = True
        fn.__variant_record__ = True
        return fn

    @classmethod
    def __post_vy_check__(cls, container, fn, dec, errors):
        return None


class checked_fetch(VyDecorator):
    """Declare a ``HasCheckedFetch`` instance for the enclosing template."""

    def __new__(cls, fn):
        fn.__vy_decorator__ = True
        fn.__checked_fetch__ = True
        return fn

    @classmethod
    def __post_vy_check__(cls, container, fn, dec, errors):
        return None


class no_derive(VyDecorator):
    def __new__(cls, obj):
        obj.__vy_decorator__ = True
        obj.__daml_no_derive__ = True
        return obj

    @classmethod
    def __post_vy_check__(cls, container, fn, dec, errors):
        return None


@dataclasses.dataclass(frozen=True)
class BuiltinMetadata:
    function_renames: dict
    function_import_modules: dict
    ledger_effect_fns: frozenset
    constructor_types: frozenset
    constructor_of: dict
    case_accessors: dict
    field_types: dict
    function_param_names: dict
    function_param_types: dict
    function_return_types: dict
    no_derive_types: frozenset
    type_field_renames: dict
    type_aliases: dict


def _walk_submodules(package_name: str):
    package = importlib.import_module(package_name)
    for info in pkgutil.walk_packages(package.__path__, package_name + "."):
        yield importlib.import_module(info.name)


@lru_cache(maxsize=1)
def known_builtin_function_names() -> frozenset:
    return frozenset(
        name
        for module in _walk_submodules("forge.builtins")
        for name, obj in vars(module).items()
        if getattr(obj, "__module__", None) == module.__name__
        and getattr(obj, "__daml_name__", None) is not None
        and callable(obj)
    )


def known_decorator_names() -> frozenset:
    from forge.models import decorators as vy_decorators_module

    own_module = importlib.import_module(__name__)
    names = set()
    for module in (own_module, vy_decorators_module):
        for name, obj in vars(module).items():
            is_decorator = getattr(obj, "__vy_decorator__", False) or (
                isinstance(obj, type) and obj is not VyDecorator and issubclass(obj, VyDecorator)
            )
            if is_decorator:
                names.add(name)
    return frozenset(names)


def _python_type_to_type_ref(py_type) -> "Optional[TypeRef]":
    from forge.ir.nodes import TypeRef

    if py_type is None:
        return None
    daml_name = {
        datetime_module.date: "Date",
        datetime_module.datetime: "Timestamp",
        datetime_module.timedelta: "RelTime",
    }.get(py_type)
    if daml_name is not None:
        return TypeRef(daml_name)
    origin = typing.get_origin(py_type)
    if origin is None:
        name = getattr(py_type, "__name__", None)
        return TypeRef(name) if name else None
    args = typing.get_args(py_type)
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(non_none) != len(args):
            inner = _python_type_to_type_ref(non_none[0])
            return TypeRef("Optional", (inner,) if inner is not None else ())
        return None
    name = getattr(origin, "__name__", None)
    if name is None:
        return None
    arg_refs = tuple(ref for a in args if (ref := _python_type_to_type_ref(a)) is not None)
    return TypeRef(name, arg_refs)


def collect_daml_builtins() -> BuiltinMetadata:
    from forge.builtin_stubs import current_scope

    return _collect_daml_builtins(current_scope())


@lru_cache(maxsize=256)
def _collect_daml_builtins(scope) -> BuiltinMetadata:
    function_renames: dict = {}
    function_import_modules: dict = {}
    ledger_effect_fns: set = set()
    marked_constructor_types: set = set()
    constructor_of: dict = {}
    case_accessors: dict = {}
    field_types: dict = {}
    function_param_names: dict = {}
    function_param_types: dict = {}
    function_return_types: dict = {}
    no_derive_types: set = set()
    type_field_renames: dict = {}

    for module in _walk_submodules("forge.builtins"):
        for name, obj in vars(module).items():
            if getattr(obj, "__module__", None) != module.__name__:
                continue

            daml_name = getattr(obj, "__daml_name__", None)
            if daml_name is not None and callable(obj):
                function_renames[name] = daml_name
                signature = inspect.signature(obj)
                hints = typing.get_type_hints(obj)
                function_param_names[name] = tuple(signature.parameters)
                is_ledger_effect = getattr(obj, "__daml_ledger_effect__", False)
                function_param_types[name] = tuple(
                    (
                        None
                        if is_ledger_effect
                        else _python_type_to_type_ref(hints.get(parameter.name))
                    )
                    for parameter in signature.parameters.values()
                )
                required_module = getattr(obj, "__daml_module__", None)
                if required_module is not None:
                    function_import_modules[daml_name] = required_module
                if is_ledger_effect:
                    ledger_effect_fns.add(name)

            case_constructor = getattr(obj, "__daml_case_accessor__", None)
            if case_constructor is not None and callable(obj):
                arity = getattr(obj, "__daml_case_accessor_arity__", 1)
                result_constructor = getattr(obj, "__daml_case_accessor_result_constructor__", None)
                field_names = getattr(obj, "__daml_case_accessor_field_names__", ())
                if result_constructor is not None:
                    case_accessors[name] = (
                        case_constructor,
                        arity,
                        result_constructor,
                        field_names,
                    )
                else:
                    case_accessors[name] = (
                        case_constructor if arity == 1 else (case_constructor, arity)
                    )

            if isinstance(obj, type):
                if getattr(obj, "__daml_no_derive__", False):
                    no_derive_types.add(name)
                if dataclasses.is_dataclass(obj):
                    marked_constructor_types.add(name)
                    hints = typing.get_type_hints(obj, include_extras=True)
                    stripped_hints = typing.get_type_hints(obj, include_extras=False)
                    fields: dict = {}
                    for field in dataclasses.fields(obj):
                        for meta in getattr(hints.get(field.name), "__metadata__", ()):
                            if isinstance(meta, DamlName):
                                type_field_renames.setdefault(name, {})[field.name] = meta.name
                        type_ref = _python_type_to_type_ref(stripped_hints.get(field.name))
                        if type_ref is not None:
                            fields[field.name] = type_ref
                    field_types[name] = fields
                continue

            if inspect.isfunction(obj):
                return_type = typing.get_type_hints(obj).get("return")
                if name[:1].isupper() and return_type is not None:
                    constructor_of[name] = getattr(return_type, "__name__", str(return_type))
                type_ref = _python_type_to_type_ref(return_type)
                if type_ref is not None:
                    function_return_types[name] = type_ref

    from forge.builtin_stubs import collect_vy_stubs

    stubs = collect_vy_stubs(scope)
    function_renames.update(stubs.function_renames)
    ledger_effect_fns |= stubs.ledger_effect_fns
    marked_constructor_types |= stubs.constructor_types
    constructor_of.update(stubs.constructor_of)
    case_accessors.update(stubs.case_accessors)
    field_types.update(stubs.field_types)
    function_return_types.update(stubs.function_return_types)
    no_derive_types |= stubs.no_derive_types
    for owner, renames in stubs.type_field_renames.items():
        type_field_renames.setdefault(owner, {}).update(renames)

    return BuiltinMetadata(
        function_renames=function_renames,
        function_import_modules=function_import_modules,
        ledger_effect_fns=frozenset(ledger_effect_fns),
        constructor_types=frozenset(marked_constructor_types | set(constructor_of.values())),
        constructor_of=constructor_of,
        case_accessors=case_accessors,
        field_types=field_types,
        function_param_names=function_param_names,
        function_param_types=function_param_types,
        function_return_types=function_return_types,
        no_derive_types=frozenset(no_derive_types),
        type_field_renames=type_field_renames,
        type_aliases=dict(stubs.type_aliases),
    )
