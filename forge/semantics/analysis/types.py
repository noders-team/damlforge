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

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from forge.ast import nodes as vy_ast
from forge.ast.utils import module_assigns
from forge.exceptions import CompilerPanic, DamlForgeException, ExceptionList
from forge.ir.lower import lower_type
from forge.ir.nodes import TypeRef
from forge.models.error_codes import ErrorCodes
from forge.semantics.analysis.imports import ImportsBuilder
from forge.semantics.analysis.structure import _reachable_modules
from forge.semantics.primitive_specs import generic_spec, type_is_ordered
from forge.utils import daml_module_name


def _safe_lower_type(node) -> TypeRef:
    """Keep malformed annotations in semantic analysis as normal diagnostics."""
    try:
        return lower_type(node)
    except CompilerPanic:
        return TypeRef(f"<invalid:{type(node).__name__}>")


class _PrimT:
    _id: str


class PartyT(_PrimT):
    _id = "Party"


class TextT(_PrimT):
    _id = "Text"


class IntT(_PrimT):
    _id = "Int"


class PyIntT(_PrimT):
    _id = "int"


class PyFloatT(_PrimT):
    _id = "float"


class PyStrT(_PrimT):
    _id = "str"


class DecimalT(_PrimT):
    _id = "Decimal"


class DateT(_PrimT):
    _id = "Date"


class TimestampT(_PrimT):
    _id = "Timestamp"


class RelTimeT(_PrimT):
    _id = "RelTime"


class BoolT(_PrimT):
    _id = "bool"


class NoneT(_PrimT):
    _id = "None"


def _get_primitive_types() -> dict:
    types = [
        PartyT(),
        TextT(),
        IntT(),
        PyIntT(),
        PyFloatT(),
        PyStrT(),
        DecimalT(),
        DateT(),
        TimestampT(),
        RelTimeT(),
        BoolT(),
        NoneT(),
    ]
    return {t._id: t for t in types}


class _GenericT:
    _id: str


class ContractIdT(_GenericT):
    _id = "ContractId"


class OptionalT(_GenericT):
    _id = "Optional"


class ListT(_GenericT):
    _id = "list"


class TupleT(_GenericT):
    _id = "tuple"


class SetT(_GenericT):
    _id = "set"


class FrozenSetT(_GenericT):
    _id = "frozenset"


def _get_generic_types() -> dict:
    types = [ContractIdT(), OptionalT(), ListT(), TupleT(), SetT(), FrozenSetT()]
    return {t._id: t for t in types}


def daml_builtin_type_names() -> set:
    from forge.builtins.maps import known_map_types

    return set(_get_primitive_types()) | set(_get_generic_types()) | set(known_map_types())


_DECLARATION_TYPES = (
    vy_ast.StructDef,
    vy_ast.EnumDef,
    vy_ast.VariantDef,
    vy_ast.InterfaceDef,
    vy_ast.TemplateDef,
)


class ModuleScope:
    """Names and type information visible from one module."""

    def __init__(self, module: vy_ast.Module):
        self.module = module

    @cached_property
    def imported_declarations(self) -> dict:
        return _direct_imported_declarations(self.module)

    @cached_property
    def type_names(self) -> set:
        return known_type_names(self)

    @cached_property
    def declarations(self) -> dict:
        return known_declarations(self)

    @cached_property
    def field_types(self) -> dict:
        return known_field_types(self)

    @cached_property
    def return_types(self) -> dict:
        return known_return_types(self, self.field_types)

    @cached_property
    def global_names(self) -> frozenset:
        return known_global_names(self)

    @cached_property
    def function_param_types(self) -> dict:
        return known_function_param_types(self)

    @cached_property
    def function_param_names(self) -> dict:
        return known_function_param_names(self)

    @cached_property
    def choice_names(self) -> dict[str, str]:
        return known_choice_names(self)

    @cached_property
    def list_returning_functions(self) -> frozenset:
        return known_list_returning_functions(self)


def _choice_exports(ast: vy_ast.Module) -> dict:
    return {
        name: child
        for container in ast.body
        for child in getattr(container, "body", ())
        if (name := ImportsBuilder._choice_export_name(child)) is not None
    }


def _exported_declarations(ast: vy_ast.Module, reexport: bool) -> dict:
    kinds = (*_DECLARATION_TYPES, vy_ast.FunctionDef)
    exported = {node.name: node for node in ast.body if isinstance(node, kinds)}
    exported.update(_choice_exports(ast))
    for stmt in (s for s in ast.body if reexport and isinstance(s, vy_ast.ImportFrom)):
        for info in stmt._metadata.get("import_infos", ()):
            nested = _exported_declarations(info.ast, True)
            for alias in (a for a in stmt.names if a.name in nested):
                exported.setdefault(alias.name, nested[alias.name])
    return exported


def _direct_imported_declarations(module: vy_ast.Module) -> dict:
    declarations = {}
    for stmt in module.body:
        if not isinstance(stmt, vy_ast.ImportFrom):
            continue
        imported = {}
        for info in stmt._metadata.get("import_infos", ()):
            reexport = ImportsBuilder.is_builtin_stub_path(info.resolved_path)
            imported.update(_exported_declarations(info.ast, reexport))
        for alias in stmt.names:
            if alias.name == "*":
                declarations.update(imported)
            elif alias.name in imported:
                declarations[alias.asname or alias.name] = imported[alias.name]
    return declarations


def _scope_modules(modules) -> list[vy_ast.Module]:
    if isinstance(modules, ModuleScope):
        return [modules.module]
    return _reachable_modules(modules)


def _scope_nodes(modules):
    for module in _scope_modules(modules):
        yield from module.body
        if isinstance(modules, ModuleScope):
            yield from modules.imported_declarations.values()


def known_declarations(modules) -> dict:
    declarations = {}
    for module in _scope_modules(modules):
        for node in module.body:
            if isinstance(node, _DECLARATION_TYPES):
                declarations[node.name] = node
        if isinstance(modules, ModuleScope):
            declarations.update(
                {
                    name: node
                    for name, node in modules.imported_declarations.items()
                    if isinstance(node, _DECLARATION_TYPES)
                }
            )
    return declarations


def _qualified_import_type_names(modules) -> set:
    names: set[str] = set()
    for module in _scope_modules(modules):
        for stmt in module.body:
            if not isinstance(stmt, vy_ast.Import):
                continue
            infos = stmt._metadata.get("import_infos", ())
            for alias in stmt.names:
                stub = next((i.ast for i in infos if i.alias == alias.name), None)
                if stub is not None:
                    bound_name = alias.asname or alias.name
                    daml_name = bound_name
                    if alias.asname is None:
                        path_str = getattr(stub, "path", None)
                        if path_str:
                            path = Path(path_str).with_suffix("")
                            daml_name = ".".join(daml_module_name(part) for part in path.parts)
                    types = {n.name for n in stub.body if isinstance(n, _DECLARATION_TYPES)}
                    types |= _choice_exports(stub).keys() | module_assigns(stub).keys()
                    names.update(f"{bound_name}.{name}" for name in types)
                    names.update(f"{daml_name}.{name}" for name in types)
                    continue
                if alias.asname is None:
                    continue
                mod = ImportsBuilder._import_real_module(alias.name)
                if mod is None:
                    continue
                for attr_name, obj in vars(mod).items():
                    if getattr(obj, "__module__", None) != mod.__name__:
                        continue
                    if isinstance(obj, type):
                        names.add(f"{alias.asname}.{attr_name}")
    return names


def _attribute_parts(node: vy_ast.VyperNode) -> list[str] | None:
    parts = []
    current = node
    while isinstance(current, vy_ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, vy_ast.Name):
        return None
    parts.append(current.id)
    return list(reversed(parts))


@dataclass(frozen=True)
class ImportedEnumValue:
    enum: vy_ast.EnumDef
    qualifier: str | None


def _imported_enum_value(attr: vy_ast.Attribute) -> ImportedEnumValue | None:
    parts = _attribute_parts(attr)
    if parts is None or len(parts) != 3:
        return None
    module = attr.module_node
    mod_name, enum_name, member_name = parts
    for stmt in module.body:
        if not isinstance(stmt, vy_ast.Import):
            continue
        aliases = stmt.names
        for alias in aliases:
            if (alias.asname or alias.name) != mod_name:
                continue
            info = None
            infos = stmt._metadata.get("import_infos", ())
            for item in infos:
                if item.alias == alias.name:
                    info = item
                    break
            if info is None:
                continue
            enum = None
            declarations = info.ast.body
            for declaration in declarations:
                if isinstance(declaration, vy_ast.EnumDef) and declaration.name == enum_name:
                    enum = declaration
                    break
            if enum is None or member_name not in _enum_members(enum):
                return None
            return ImportedEnumValue(enum, alias.asname)
    return None


def known_type_names(modules) -> set:
    names = daml_builtin_type_names()
    for module in _scope_modules(modules):
        names |= ImportsBuilder.imported_names(module)
        names |= module_assigns(module).keys()
        names |= _choice_exports(module).keys()
    names |= known_declarations(modules).keys()
    names |= _qualified_import_type_names(modules)
    return names


def _enum_members(node: vy_ast.EnumDef) -> frozenset:
    return frozenset(
        name for stmt in node.body if (name := vy_ast.enum_member_name(stmt)) is not None
    )


def check_enum_value_access(
    node: vy_ast.TopLevel, declarations: dict, errors: ExceptionList
) -> None:
    for attr in node.get_descendants(vy_ast.Attribute):
        enum_value = _imported_enum_value(attr)
        if enum_value is not None:
            enum = enum_value.enum
            attr._metadata["is_enum_value"] = True
            attr._metadata["enum_name"] = enum.name
            attr._metadata["enum_qualifier"] = enum_value.qualifier
            continue
        if not isinstance(attr.value, vy_ast.Name):
            parent = getattr(attr, "parent", None)
            is_called_method = (
                isinstance(parent, vy_ast.Call)
                and parent.func is attr
                and isinstance(attr.value, (vy_ast.Name, vy_ast.Attribute))
            )
            if attr.attr[:1].isupper() and not is_called_method:
                errors.append(
                    DamlForgeException(
                        f"Uppercase member `{attr.attr}` cannot be selected from an expression; "
                        "use an enum or module name",
                        attr,
                        code=ErrorCodes.DF1213.value,
                    )
                )
            continue
        declaration = declarations.get(attr.value.id)
        if not isinstance(declaration, vy_ast.EnumDef):
            continue
        members = _enum_members(declaration)
        if attr.attr in members:
            attr._metadata["is_enum_value"] = True
            attr._metadata["enum_name"] = declaration.name
            continue
        errors.append(
            DamlForgeException(
                f"`{attr.attr}` is not a member of enum `{attr.value.id}` "
                f"(known members: {', '.join(sorted(members))})",
                attr,
                code=ErrorCodes.DF1216.value,
            )
        )


_NON_VARIABLE_DECLARATION_TYPES = (vy_ast.StructDef, vy_ast.InterfaceDef, vy_ast.TemplateDef)

_LANGUAGE_BUILTIN_NAMES = frozenset({"false", "true", "len", "ceil", "floor", "range"})


def known_global_names(modules) -> frozenset:
    from forge.builtins import collect_daml_builtins, known_builtin_function_names

    names = set(daml_builtin_type_names())
    for module in _scope_modules(modules):
        names |= ImportsBuilder.imported_names(module)
        for node in module.body:
            if isinstance(node, vy_ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, vy_ast.VariantDef):
                names.update(fn.name for fn in node.body if isinstance(fn, vy_ast.FunctionDef))
    names |= _LANGUAGE_BUILTIN_NAMES
    names |= set(collect_daml_builtins().function_renames.keys()) - known_builtin_function_names()
    return frozenset(names)


def _extract_bound_names(target, names: set) -> None:
    if isinstance(target, vy_ast.Name):
        names.add(target.id)
    elif isinstance(target, vy_ast.Tuple):
        for el in target.elements:
            _extract_bound_names(el, names)
    elif hasattr(target, "target"):
        _extract_bound_names(target.target, names)


def _local_bound_names(fn: vy_ast.FunctionDef) -> frozenset:
    names = {a.arg for a in fn.args.args}
    for assign in fn.get_descendants((vy_ast.Assign, vy_ast.AnnAssign)):
        _extract_bound_names(assign.target, names)
    for for_stmt in fn.get_descendants(vy_ast.For):
        _extract_bound_names(for_stmt.target, names)
    return frozenset(names)


_CONSTRUCTIBLE_DECLARATION_TYPES = (vy_ast.StructDef, vy_ast.TemplateDef)


def _is_template_choice_call(attr: vy_ast.Attribute, declaration) -> bool:
    parent = getattr(attr, "parent", None)
    return (
        isinstance(declaration, vy_ast.TemplateDef)
        and isinstance(parent, vy_ast.Call)
        and parent.func is attr
        and any(getattr(stmt, "name", None) == attr.attr for stmt in declaration.body)
    )


def check_unresolved_names(
    node: vy_ast.TopLevel, declarations: dict, global_names: frozenset, errors: ExceptionList
) -> None:
    fns = (
        [node] if isinstance(node, vy_ast.FunctionDef) else node.get_descendants(vy_ast.FunctionDef)
    )
    enclosing_fields = {
        stmt.target.id
        for stmt in getattr(node, "body", ())
        if isinstance(node, vy_ast.TemplateDef)
        and isinstance(stmt, vy_ast.AnnAssign)
        and isinstance(stmt.target, vy_ast.Name)
    }
    for fn in fns:
        local_names = set(_local_bound_names(fn) | enclosing_fields)
        if fn.get_ancestor(vy_ast.TemplateDef) is not None:
            # Template methods may omit ``self`` from their Python signature;
            # Forge still binds the current contract as ``self`` in Daml.
            local_names.add("self")
        if fn.get_ancestor(vy_ast.InterfaceDef) is not None:
            local_names.add("view")
        annotation_names: set[int] = set()
        for argument in fn.args.args:
            if argument.annotation is not None:
                annotation_names.update(
                    id(name)
                    for name in argument.annotation.get_descendants(vy_ast.Name, include_self=True)
                )
        if fn.returns is not None:
            annotation_names.update(
                id(name) for name in fn.returns.get_descendants(vy_ast.Name, include_self=True)
            )
        for declaration in fn.get_descendants(vy_ast.AnnAssign):
            annotation_names.update(
                id(name)
                for name in declaration.annotation.get_descendants(vy_ast.Name, include_self=True)
            )

        for name_node in fn.get_descendants(vy_ast.Name):
            if id(name_node) in annotation_names:
                continue
            parent = getattr(name_node, "parent", None)
            if (isinstance(parent, vy_ast.Attribute) and parent.value is name_node) or (
                isinstance(parent, vy_ast.Call) and parent.func is name_node
            ):
                continue
            if name_node.id in local_names or name_node.id in global_names:
                continue
            if isinstance(declarations.get(name_node.id), _CONSTRUCTIBLE_DECLARATION_TYPES):
                continue
            errors.append(
                DamlForgeException(
                    f"`{name_node.id}` is not defined - no variable, parameter, import, or "
                    "declaration named "
                    f"`{name_node.id}` exists in scope here",
                    name_node,
                    code=ErrorCodes.DF1219.value,
                )
            )

        for attr in fn.get_descendants(vy_ast.Attribute):
            if not isinstance(attr.value, vy_ast.Name):
                continue
            name = attr.value.id
            if name in local_names:
                continue
            declaration = declarations.get(name)
            if isinstance(declaration, vy_ast.EnumDef):
                continue
            if _is_template_choice_call(attr, declaration):
                continue
            if isinstance(declaration, _NON_VARIABLE_DECLARATION_TYPES):
                errors.append(
                    DamlForgeException(
                        f"`{name}` is a type name, not a variable - no variable named `{name}` "
                        f"exists in this scope; use `self.{attr.attr}` to access the current "
                        "contract's fields",
                        attr,
                        code=ErrorCodes.DF1219.value,
                    )
                )
                continue
            if name in global_names:
                continue
            errors.append(
                DamlForgeException(
                    f"`{name}` is not defined - no variable, parameter, import, or declaration "
                    f"named `{name}` exists in scope here",
                    attr,
                    code=ErrorCodes.DF1219.value,
                )
            )

        for call in fn.get_descendants(vy_ast.Call):
            if not isinstance(call.func, vy_ast.Name):
                continue
            name = call.func.id
            if name in local_names or name in global_names:
                continue
            if isinstance(declarations.get(name), _CONSTRUCTIBLE_DECLARATION_TYPES):
                continue
            errors.append(
                DamlForgeException(
                    f"`{name}` is not defined - no function, import, or struct/template "
                    f"declaration named `{name}` exists in scope here",
                    call,
                    code=ErrorCodes.DF1219.value,
                )
            )


def known_function_param_types(modules) -> dict:
    from forge.builtins import collect_daml_builtins

    types = dict(collect_daml_builtins().function_param_types)
    for node in _scope_nodes(modules):
        if isinstance(node, vy_ast.FunctionDef):
            types[node.name] = tuple(
                _safe_lower_type(a.annotation) if a.annotation is not None else None
                for a in node.args.args
                if a.arg != "self"
            )
        elif isinstance(node, vy_ast.VariantDef):
            for constructor in node.body:
                if not isinstance(constructor, vy_ast.FunctionDef):
                    continue
                types[constructor.name] = tuple(
                    _safe_lower_type(a.annotation) if a.annotation is not None else None
                    for a in constructor.args.args
                    if a.arg != "self"
                )
        elif isinstance(node, (vy_ast.InterfaceDef, vy_ast.TemplateDef)):
            for function in node.body:
                if not isinstance(function, vy_ast.FunctionDef):
                    continue
                signature = tuple(
                    _safe_lower_type(a.annotation) if a.annotation is not None else None
                    for a in function.args.args
                    if a.arg != "self"
                )
                types[function.name] = signature
                types[f"{node.name}.{function.name}"] = signature
    return types


def known_function_param_names(modules) -> dict:
    from forge.builtins import collect_daml_builtins

    names = dict(collect_daml_builtins().function_param_names)
    for node in _scope_nodes(modules):
        if isinstance(node, vy_ast.FunctionDef):
            names[node.name] = tuple(a.arg for a in node.args.args if a.arg != "self")
        elif isinstance(node, vy_ast.VariantDef):
            for constructor in node.body:
                if not isinstance(constructor, vy_ast.FunctionDef):
                    continue
                names[constructor.name] = tuple(
                    a.arg for a in constructor.args.args if a.arg != "self"
                )
        elif isinstance(node, (vy_ast.InterfaceDef, vy_ast.TemplateDef)):
            for function in node.body:
                if not isinstance(function, vy_ast.FunctionDef):
                    continue
                signature = tuple(a.arg for a in function.args.args if a.arg != "self")
                names[function.name] = signature
                names[f"{node.name}.{function.name}"] = signature
    return names


def known_choice_names(modules) -> dict[str, str]:
    """Return template/interface method names and their Daml choice names."""

    choices: dict[str, str] = {}
    for node in _scope_nodes(modules):
        if not isinstance(node, (vy_ast.InterfaceDef, vy_ast.TemplateDef)):
            continue
        for function in node.body:
            if not isinstance(function, vy_ast.FunctionDef):
                continue
            choice_name = ImportsBuilder._choice_export_name(function)
            if choice_name is None:
                continue
            choices[function.name] = choice_name
            choices[f"{node.name}.{function.name}"] = choice_name
    return choices


def _calls_to_check(fn: vy_ast.FunctionDef, param_types: dict):
    decorator_call_ids = {id(d) for d in fn.decorator_list if isinstance(d, vy_ast.Call)}
    for call in fn.get_descendants(vy_ast.Call):
        if id(call) in decorator_call_ids:
            continue
        if not isinstance(call.func, vy_ast.Name) or call.keywords:
            continue
        expected_params = param_types.get(call.func.id)
        if expected_params is None:
            continue
        yield call, expected_params


def check_call_arity(node: vy_ast.TopLevel, param_types: dict, errors: ExceptionList) -> None:
    fns = (
        [node] if isinstance(node, vy_ast.FunctionDef) else node.get_descendants(vy_ast.FunctionDef)
    )
    for fn in fns:
        for call, expected_params in _calls_to_check(fn, param_types):
            expected = len(expected_params)
            actual = len(call.args)
            if actual == expected:
                continue
            errors.append(
                DamlForgeException(
                    f"`{call.func.id}(...)` called with {actual} argument"
                    f"{'' if actual == 1 else 's'}, but `{call.func.id}` takes {expected} "
                    f"argument{'' if expected == 1 else 's'}",
                    call,
                    code=ErrorCodes.DF1225.value,
                )
            )


def _render_type_ref(t: TypeRef) -> str:
    if not t.args:
        return t.name
    return f"{t.name}[{', '.join(_render_type_ref(a) for a in t.args)}]"


def _is_list_type_annotation(node) -> bool:
    return (
        isinstance(node, vy_ast.Subscript)
        and isinstance(node.value, vy_ast.Name)
        and node.value.id == "list"
    )


def known_list_returning_functions(modules) -> frozenset:
    names = set()
    for node in _scope_nodes(modules):
        if isinstance(node, vy_ast.FunctionDef) and _is_list_type_annotation(node.returns):
            names.add(node.name)
    return frozenset(names)


def _assigns_list_value(value, list_returning_fns: frozenset, list_names: set) -> bool:
    if isinstance(value, vy_ast.List):
        return True
    if isinstance(value, vy_ast.Name):
        return value.id in list_names
    if (
        isinstance(value, vy_ast.Call)
        and isinstance(value.func, vy_ast.Name)
        and value.func.id in list_returning_fns
    ):
        return True
    return False


def _mark_list_subscripts_in_body(
    stmts, list_returning_fns: frozenset, list_names: set, list_self_fields: set
) -> None:
    for stmt in stmts:
        for sub in stmt.get_descendants(vy_ast.Subscript, include_self=True):
            is_list_literal = isinstance(sub.value, vy_ast.List)
            is_list_name = isinstance(sub.value, vy_ast.Name) and sub.value.id in list_names
            is_list_self_field = (
                isinstance(sub.value, vy_ast.Attribute)
                and isinstance(sub.value.value, vy_ast.Name)
                and sub.value.value.id == "self"
                and sub.value.attr in list_self_fields
            )
            if is_list_literal or is_list_name or is_list_self_field:
                sub._metadata["is_list_value"] = True
        if isinstance(stmt, vy_ast.Assign) and isinstance(stmt.target, vy_ast.Name):
            if _assigns_list_value(stmt.value, list_returning_fns, list_names):
                list_names.add(stmt.target.id)
            else:
                list_names.discard(stmt.target.id)


def mark_list_subscripts(node: vy_ast.TopLevel, list_returning_fns: frozenset) -> None:
    fns = (
        [node] if isinstance(node, vy_ast.FunctionDef) else node.get_descendants(vy_ast.FunctionDef)
    )
    for fn in fns:
        list_names = {
            arg.arg
            for arg in fn.args.args
            if arg.annotation is not None and _is_list_type_annotation(arg.annotation)
        }
        template = fn.get_ancestor(vy_ast.TemplateDef)
        if isinstance(template, vy_ast.TemplateDef):
            list_self_fields = {
                stmt.target.id
                for stmt in template.body
                if isinstance(stmt, vy_ast.AnnAssign)
                and isinstance(stmt.target, vy_ast.Name)
                and _is_list_type_annotation(stmt.annotation)
            }
        else:
            list_self_fields = set()
        _mark_list_subscripts_in_body(fn.body, list_returning_fns, list_names, list_self_fields)


_UNMAPPABLE_PYTHON_TYPES = frozenset(
    {"bytes", "bytearray", "memoryview", "complex", "type", "object", "slice"}
)


def _walk_type_ref(
    type_ref: TypeRef, known_names: set, node, context: str, errors: ExceptionList
) -> None:
    if type_ref.name in _UNMAPPABLE_PYTHON_TYPES:
        errors.append(
            DamlForgeException(
                f"`{type_ref.name}` has no Daml equivalent and cannot be used as a "
                f"type ({context})",
                node,
                code=ErrorCodes.DF1222.value,
            )
        )
    elif type_ref.name not in known_names:
        errors.append(
            DamlForgeException(
                f"No builtin or user-defined type named `{type_ref.name}` ({context})",
                node,
                code=ErrorCodes.DF1213.value,
            )
        )
    for arg in type_ref.args:
        _walk_type_ref(arg, known_names, node, context, errors)


def check_type_annotation(node, known_names: set, context: str, errors: ExceptionList) -> None:
    if node is None:
        return
    type_ref = _safe_lower_type(node)
    _walk_type_ref(type_ref, known_names, node, context, errors)
    _check_generic_policy(type_ref, node, context, errors)


def _check_generic_policy(type_ref: TypeRef, node, context: str, errors: ExceptionList) -> None:
    """Validate arity and container-specific Daml policies in one place."""

    spec = generic_spec(type_ref.name)
    if spec is not None:
        arity = len(type_ref.args)
        if not spec.min_arity <= arity <= spec.max_arity:
            expected = (
                str(spec.min_arity)
                if spec.min_arity == spec.max_arity
                else f"{spec.min_arity}..{spec.max_arity}"
            )
            errors.append(
                DamlForgeException(
                    f"`{type_ref.name}` expects {expected} type argument(s), got {arity} "
                    f"({context})",
                    node,
                    code=ErrorCodes.DF1229.value,
                )
            )
        if spec.ordered_key_position is not None and type_ref.args:
            key = type_ref.args[spec.ordered_key_position]
            if not type_is_ordered(key):
                errors.append(
                    DamlForgeException(
                        f"`{type_ref.name}` keys must be ordered Daml values; "
                        f"`{_render_type_ref(key)}` "
                        f"cannot be used as a key ({context})",
                        node,
                        code=ErrorCodes.DF1229.value,
                    )
                )
        if spec.ordered_element and type_ref.args and not type_is_ordered(type_ref.args[0]):
            element = type_ref.args[0]
            errors.append(
                DamlForgeException(
                    f"`{type_ref.name}` elements must be ordered Daml values; "
                    f"`{_render_type_ref(element)}` cannot be used as an element ({context})",
                    node,
                    code=ErrorCodes.DF1229.value,
                )
            )
    for arg in type_ref.args:
        _check_generic_policy(arg, node, context, errors)


def check_function_type_annotations(
    fn: vy_ast.FunctionDef, known_names: set, errors: ExceptionList
) -> None:
    for arg in fn.args.args:
        if arg.arg == "self" or arg.annotation is None:
            continue
        check_type_annotation(
            arg.annotation, known_names, f"argument `{arg.arg}` of `{fn.name}`", errors
        )
    if fn.returns is not None:
        check_type_annotation(fn.returns, known_names, f"return type of `{fn.name}`", errors)
    for stmt in fn.get_descendants(vy_ast.AnnAssign):
        check_type_annotation(
            stmt.annotation,
            known_names,
            f"local variable `{getattr(stmt.target, 'id', '<expression>')}` in `{fn.name}`",
            errors,
        )


def check_struct_type_annotations(
    node: vy_ast.StructDef, known_names: set, errors: ExceptionList
) -> None:
    for stmt in node.body:
        if not isinstance(stmt, vy_ast.AnnAssign):
            continue
        if not isinstance(stmt.target, vy_ast.Name):
            errors.append(
                DamlForgeException(
                    f"Struct field target must be a name, got `{type(stmt.target).__name__}`",
                    stmt.target,
                    code=ErrorCodes.DF1213.value,
                )
            )
        field_name = getattr(stmt.target, "id", "<expression>")
        check_type_annotation(
            stmt.annotation, known_names, f"field `{field_name}` of `{node.name}`", errors
        )


def check_template_field_type_annotations(
    node: vy_ast.TemplateDef, known_names: set, errors: ExceptionList
) -> None:
    for stmt in node.body:
        if not isinstance(stmt, vy_ast.AnnAssign):
            continue
        if not isinstance(stmt.target, vy_ast.Name):
            errors.append(
                DamlForgeException(
                    f"Template field target must be a name, got `{type(stmt.target).__name__}`",
                    stmt.target,
                    code=ErrorCodes.DF1213.value,
                )
            )
        field_name = getattr(stmt.target, "id", "<expression>")
        check_type_annotation(
            stmt.annotation, known_names, f"field `{field_name}` of `{node.name}`", errors
        )


def check_interface_view_type_annotation(
    node: vy_ast.InterfaceDef, known_names: set, errors: ExceptionList
) -> None:
    for kw in node.keywords:
        if kw.arg == "view":
            check_type_annotation(kw.value, known_names, f"`view=` of `{node.name}`", errors)


def _stub_field_types(module: vy_ast.Module, registry: dict) -> None:
    from forge.builtin_stubs import owner_module, stub_modules

    stubs = stub_modules()
    for stmt in module.body:
        for alias in getattr(stmt, "names", None) or ():
            if isinstance(stmt, vy_ast.Import) and alias.asname and alias.name in stubs:
                types = stubs[alias.name].field_types.items()
                registry.update({f"{alias.asname}.{name}": fields for name, fields in types})
            elif isinstance(stmt, vy_ast.ImportFrom):
                owner = owner_module(stmt.module, alias.name)
                if owner is not None and alias.name in stubs[owner].field_types:
                    registry[alias.name] = stubs[owner].field_types[alias.name]


def known_field_types(modules) -> dict:
    from forge.builtins import collect_daml_builtins

    registry = dict(collect_daml_builtins().field_types)
    for module in _scope_modules(modules):
        _stub_field_types(module, registry)
    for node in _scope_nodes(modules):
        if isinstance(node, vy_ast.VariantDef):
            for constructor in node.body:
                if not isinstance(constructor, vy_ast.FunctionDef):
                    continue
                registry[constructor.name] = {
                    arg.arg: _safe_lower_type(arg.annotation)
                    for arg in constructor.args.args
                    if arg.arg != "self" and arg.annotation is not None
                }
            continue
        if not isinstance(node, (vy_ast.StructDef, vy_ast.TemplateDef)):
            continue
        fields = {}
        for stmt in node.body:
            if isinstance(stmt, vy_ast.AnnAssign) and isinstance(stmt.target, vy_ast.Name):
                fields[stmt.target.id] = _safe_lower_type(stmt.annotation)
        registry[node.name] = fields
    return registry


def known_return_types(modules, field_types: dict) -> dict:
    from forge.builtins import collect_daml_builtins

    types = {name: TypeRef(name) for name in field_types}
    types.update(collect_daml_builtins().function_return_types)
    for node in _scope_nodes(modules):
        if isinstance(node, vy_ast.FunctionDef) and node.returns is not None:
            types[node.name] = _safe_lower_type(node.returns)
        elif isinstance(node, (vy_ast.InterfaceDef, vy_ast.TemplateDef)):
            for function in node.body:
                if isinstance(function, vy_ast.FunctionDef) and function.returns is not None:
                    types[f"{node.name}.{function.name}"] = _safe_lower_type(function.returns)
        elif isinstance(node, vy_ast.VariantDef):
            types.update(
                {
                    constructor.name: TypeRef(node.name)
                    for constructor in node.body
                    if isinstance(constructor, vy_ast.FunctionDef)
                }
            )
    return types


_TIME_ARITHMETIC_MARKERS = ("add_rel_time", "sub_time", "sub_rel_time")


def _infer_expr_type(
    expr, enclosing_type, local_types: dict, field_types: dict, return_types: dict
):
    if isinstance(expr, vy_ast.Call) and (return_type := expr._metadata.get("return_type")):
        return TypeRef(return_type)
    if isinstance(expr, vy_ast.Call) and "datetime_combine_arguments" in expr._metadata:
        return TypeRef("Timestamp")
    if isinstance(expr, vy_ast.Call) and "fromtimestamp_argument" in expr._metadata:
        return TypeRef("Timestamp")
    if isinstance(expr, vy_ast.Call) and "date_arguments" in expr._metadata:
        return TypeRef("Date")
    if isinstance(expr, vy_ast.Call) and "timedelta_arguments" in expr._metadata:
        return TypeRef("RelTime")
    if isinstance(expr, vy_ast.BinOp) and (
        expr._metadata.get("add_rel_time") or expr._metadata.get("sub_rel_time")
    ):
        return TypeRef("Timestamp")
    if isinstance(expr, vy_ast.BinOp) and expr._metadata.get("sub_time"):
        return TypeRef("RelTime")
    if isinstance(expr, vy_ast.NameConstant) and expr.value is None:
        return TypeRef("None")
    if isinstance(expr, vy_ast.List):
        return TypeRef("list")
    if isinstance(expr, vy_ast.Tuple):
        return TypeRef("tuple")
    if isinstance(expr, (vy_ast.Int, vy_ast.Decimal, vy_ast.Str, vy_ast.NameConstant)):
        return TypeRef(type(expr).__name__)
    if isinstance(expr, (vy_ast.BinOp, vy_ast.BoolOp, vy_ast.UnaryOp, vy_ast.Compare)):
        return TypeRef(type(expr).__name__)
    if isinstance(expr, vy_ast.Name):
        return local_types.get(expr.id)
    if isinstance(expr, vy_ast.Attribute):
        if isinstance(expr.value, vy_ast.Name) and expr.value.id == "self":
            base_type_name = enclosing_type
        else:
            base_type = _infer_expr_type(
                expr.value, enclosing_type, local_types, field_types, return_types
            )
            base_type_name = base_type.name if base_type is not None else None
        if base_type_name is None:
            return None
        return field_types.get(base_type_name, {}).get(expr.attr)
    if isinstance(expr, vy_ast.Call) and isinstance(expr.func, vy_ast.Name):
        fn = expr.func.id
        if fn == "fetch" and expr.args:
            cid_type = _infer_expr_type(
                expr.args[0], enclosing_type, local_types, field_types, return_types
            )
            if cid_type is not None and cid_type.name == "ContractId" and cid_type.args:
                return cid_type.args[0]
            return None
        if fn == "create" and expr.args:
            payload_type = _infer_expr_type(
                expr.args[0], enclosing_type, local_types, field_types, return_types
            )
            return TypeRef("ContractId", (payload_type,) if payload_type is not None else ())
        return return_types.get(fn)
    return None


def _functions_and_enclosing_type(node: vy_ast.TopLevel):
    if isinstance(node, (vy_ast.TemplateDef, vy_ast.InterfaceDef)):
        functions = [n for n in node.body if isinstance(n, vy_ast.FunctionDef)]
        enclosing_type = node.name if isinstance(node, vy_ast.TemplateDef) else None
        return functions, enclosing_type
    if isinstance(node, vy_ast.FunctionDef):
        return [node], None
    return [], None


def _mark_dict_kind(value, target_type) -> None:
    if target_type is None or not isinstance(value, vy_ast.Dict):
        return
    from forge.builtins.maps import known_map_types

    if target_type.name in known_map_types():
        value._metadata["map_kind"] = target_type.name


def _call_constructor_name(func, registry: "dict | None" = None) -> "str | None":
    if isinstance(func, vy_ast.Name):
        return func.id
    if isinstance(func, vy_ast.Attribute):
        if isinstance(func.value, vy_ast.Name) and registry is not None:
            qualified = f"{func.value.id}.{func.attr}"
            if qualified in registry:
                return qualified
        return func.attr
    return None


def _mark_call_optional_conversions(
    call, enclosing_type, local_types: dict, field_types: dict, return_types: dict
) -> None:
    constructor_name = _call_constructor_name(call.func, field_types)
    if constructor_name is not None and constructor_name in field_types:
        fields = field_types[constructor_name]
        for kw in call.keywords:
            field_type = fields.get(kw.arg)
            _mark_dict_kind(kw.value, field_type)
            if field_type is None or field_type.name != "Optional":
                continue
            value_type = _infer_expr_type(
                kw.value, enclosing_type, local_types, field_types, return_types
            )
            if value_type is not None and value_type.name not in ("Optional", "None"):
                kw.value._metadata["needs_some_wrap"] = True

    if (
        isinstance(call.func, vy_ast.Attribute)
        and call.func.attr == "append"
        and isinstance(call.func.value, vy_ast.Name)
        and len(call.args) == 1
    ):
        list_type = local_types.get(call.func.value.id)
        if list_type is not None and list_type.name == "list" and list_type.args:
            element_type = list_type.args[0]
            if element_type.name != "Optional":
                value_type = _infer_expr_type(
                    call.args[0], enclosing_type, local_types, field_types, return_types
                )
                if value_type is not None and value_type.name == "Optional":
                    call.args[0]._metadata["needs_from_some_unwrap"] = True


def _mark_expr_optional_conversions(
    expr, enclosing_type, local_types: dict, field_types: dict, return_types: dict
) -> None:
    if expr is None:
        return
    for binary in reversed(list(expr.get_descendants(vy_ast.BinOp, include_self=True))):
        if not isinstance(binary.op, (vy_ast.Add, vy_ast.Sub)):
            continue
        left_type = _infer_expr_type(
            binary.left, enclosing_type, local_types, field_types, return_types
        )
        right_type = _infer_expr_type(
            binary.right, enclosing_type, local_types, field_types, return_types
        )
        if isinstance(binary.op, vy_ast.Add) and (
            left_type is not None
            and left_type.name in ("Timestamp", "Time")
            and right_type is not None
            and right_type.name == "RelTime"
        ):
            binary._metadata["add_rel_time"] = True
        elif (
            isinstance(binary.op, vy_ast.Sub)
            and left_type is not None
            and left_type.name in ("Timestamp", "Time")
            and right_type is not None
            and right_type.name in ("Timestamp", "Time")
        ):
            binary._metadata["sub_time"] = True
        elif (
            isinstance(binary.op, vy_ast.Sub)
            and left_type is not None
            and left_type.name in ("Timestamp", "Time")
            and right_type is not None
            and right_type.name == "RelTime"
        ):
            binary._metadata["sub_rel_time"] = True
    for call in expr.get_descendants(vy_ast.Call, include_self=True):
        _mark_call_optional_conversions(
            call, enclosing_type, local_types, field_types, return_types
        )


def _mark_stmts_optional_conversions(
    stmts, enclosing_type, local_types: dict, field_types: dict, return_types: dict, return_type
) -> None:
    for stmt in stmts:
        if isinstance(stmt, vy_ast.AnnAssign):
            _mark_expr_optional_conversions(
                stmt.value, enclosing_type, local_types, field_types, return_types
            )
            target_type = _safe_lower_type(stmt.annotation)
            _mark_dict_kind(stmt.value, target_type)
            if stmt.value is not None and target_type.name != "Optional":
                value_type = _infer_expr_type(
                    stmt.value, enclosing_type, local_types, field_types, return_types
                )
                if value_type is not None and value_type.name == "Optional":
                    stmt.value._metadata["needs_from_some_unwrap"] = True
            if isinstance(stmt.target, vy_ast.Name):
                local_types[stmt.target.id] = target_type
        elif isinstance(stmt, vy_ast.Assign):
            _mark_expr_optional_conversions(
                stmt.value, enclosing_type, local_types, field_types, return_types
            )
            if isinstance(stmt.target, vy_ast.Name):
                existing_type = local_types.get(stmt.target.id)
                _mark_dict_kind(stmt.value, existing_type)
                if existing_type is not None and existing_type.name == "Optional":
                    value_type = _infer_expr_type(
                        stmt.value, enclosing_type, local_types, field_types, return_types
                    )
                    if value_type is not None and value_type.name not in ("Optional", "None"):
                        stmt.value._metadata["needs_some_wrap"] = True
                elif existing_type is None:
                    value_type = _infer_expr_type(
                        stmt.value, enclosing_type, local_types, field_types, return_types
                    )
                    if value_type is not None:
                        local_types[stmt.target.id] = value_type
        elif isinstance(stmt, vy_ast.If):
            _mark_expr_optional_conversions(
                stmt.test, enclosing_type, local_types, field_types, return_types
            )
            body_types = dict(local_types)
            orelse_types = dict(local_types)
            _mark_stmts_optional_conversions(
                stmt.body, enclosing_type, body_types, field_types, return_types, return_type
            )
            _mark_stmts_optional_conversions(
                stmt.orelse, enclosing_type, orelse_types, field_types, return_types, return_type
            )
            for target_name, body_type in body_types.items():
                if local_types.get(target_name) == body_type:
                    continue
                if orelse_types.get(target_name) == body_type:
                    local_types[target_name] = body_type
        elif isinstance(stmt, vy_ast.For):
            _mark_expr_optional_conversions(
                stmt.iter, enclosing_type, local_types, field_types, return_types
            )
            body_types = dict(local_types)
            target = stmt.target.target if hasattr(stmt.target, "target") else stmt.target
            annotation = getattr(stmt.target, "annotation", None)
            if isinstance(target, vy_ast.Name) and annotation is not None:
                body_types[target.id] = _safe_lower_type(annotation)
            _mark_stmts_optional_conversions(
                stmt.body, enclosing_type, body_types, field_types, return_types, return_type
            )
        elif isinstance(stmt, vy_ast.Return):
            _mark_expr_optional_conversions(
                stmt.value, enclosing_type, local_types, field_types, return_types
            )
            _mark_dict_kind(stmt.value, return_type)
            if (
                stmt.value is not None
                and return_type is not None
                and return_type.name != "Optional"
            ):
                value_type = _infer_expr_type(
                    stmt.value, enclosing_type, local_types, field_types, return_types
                )
                if value_type is not None and value_type.name == "Optional":
                    stmt.value._metadata["needs_from_some_unwrap"] = True
        elif isinstance(stmt, vy_ast.Assert):
            _mark_expr_optional_conversions(
                stmt.test, enclosing_type, local_types, field_types, return_types
            )
            _mark_expr_optional_conversions(
                stmt.msg, enclosing_type, local_types, field_types, return_types
            )
        elif isinstance(stmt, vy_ast.Expr):
            _mark_expr_optional_conversions(
                stmt.value, enclosing_type, local_types, field_types, return_types
            )


def mark_optional_conversions(node: vy_ast.TopLevel, field_types: dict, return_types: dict) -> None:
    functions, enclosing_type = _functions_and_enclosing_type(node)
    for fn in functions:
        local_types = {}
        for arg in fn.args.args:
            if arg.arg == "self" or arg.annotation is None:
                continue
            local_types[arg.arg] = _safe_lower_type(arg.annotation)
        return_type = _safe_lower_type(fn.returns) if fn.returns is not None else None
        _mark_stmts_optional_conversions(
            fn.body, enclosing_type, local_types, field_types, return_types, return_type
        )
    if any(
        any(binary._metadata.get(marker) for marker in _TIME_ARITHMETIC_MARKERS)
        for binary in node.get_descendants(vy_ast.BinOp, include_self=True)
    ):
        required = set(node.module_node._metadata.get("required_daml_modules", ()))
        required.add("DA.Time")
        node.module_node._metadata["required_daml_modules"] = tuple(sorted(required))


def check_container_type_annotations(
    node: vy_ast.TopLevel, known_names: set, errors: ExceptionList
) -> None:
    if isinstance(node, vy_ast.StructDef):
        check_struct_type_annotations(node, known_names, errors)
        return

    if isinstance(node, vy_ast.VariantDef):
        for constructor in node.body:
            if isinstance(constructor, vy_ast.FunctionDef):
                check_function_type_annotations(constructor, known_names, errors)
        return

    if isinstance(node, vy_ast.TemplateDef):
        check_template_field_type_annotations(node, known_names, errors)
    elif isinstance(node, vy_ast.InterfaceDef):
        check_interface_view_type_annotation(node, known_names, errors)

    if isinstance(node, (vy_ast.TemplateDef, vy_ast.InterfaceDef)):
        for stmt in node.body:
            if isinstance(stmt, vy_ast.FunctionDef):
                check_function_type_annotations(stmt, known_names, errors)
    elif isinstance(node, vy_ast.FunctionDef):
        check_function_type_annotations(node, known_names, errors)
