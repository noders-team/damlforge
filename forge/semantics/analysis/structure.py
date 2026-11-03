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
from typing import Optional

from forge.ast import nodes as vy_ast
from forge.builtins import choice, collect_daml_builtins
from forge.builtins.daml_lang import TEMPLATE_ARGUMENT_FUNCTIONS
from forge.exceptions import DamlForgeException, ExceptionList
from forge.models.error_codes import ErrorCodes

ENUM_MEMBER_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# preconsuming/postconsuming are template-only; not supported on interface choices
# (compiler/damlc/daml-lf-conversion/src/DA/Daml/LFConversion.hs, "Preconsuming/Postconsuming
# choice for interface" is an unsupported-operation error there)
TEMPLATE_ONLY_CONSUMING_VARIANTS = {"preconsuming", "postconsuming"}


def _choice_calls(fn: vy_ast.FunctionDef):
    for dec in fn.decorator_list:
        if not isinstance(dec, vy_ast.Call):
            continue
        if isinstance(dec.func, vy_ast.Name) and dec.func.id == choice.__name__:
            yield dec


def _has_decorator(fn: vy_ast.FunctionDef, name: str) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, vy_ast.Call) else dec
        if isinstance(target, vy_ast.Name) and target.id == name:
            return True
    return False


def check_unknown_decorators(container: vy_ast.TopLevel, errors: ExceptionList) -> None:
    import forge.builtins as builtins_module
    from forge.models import decorators as vy_decorators_module
    from forge.semantics.analysis.imports import ImportsBuilder

    known_decorators = builtins_module.known_decorator_names()
    imported_names = ImportsBuilder.imported_names(container.module_node)

    marker = (
        "template"
        if isinstance(container, vy_ast.TemplateDef)
        else "variant" if isinstance(container, vy_ast.VariantDef) else None
    )
    if marker is not None and marker not in imported_names:
        errors.append(
            DamlForgeException(
                f"`{marker}` is not defined - import the decorator before using it",
                container,
                code=ErrorCodes.DF1219.value,
            )
        )

    for fn in container.body:
        if not isinstance(fn, vy_ast.FunctionDef):
            continue

        for dec in fn.decorator_list:
            target = dec.func if isinstance(dec, vy_ast.Call) else dec
            if not (isinstance(target, vy_ast.Name) and target.id in known_decorators):
                name = target.id if isinstance(target, vy_ast.Name) else "<expression>"
                errors.append(
                    DamlForgeException(
                        f"Unknown decorator `@{name}` on `{container.name}.{fn.name}` - only "
                        f"{sorted(known_decorators)!r} are recognized decorators",
                        dec,
                        code=ErrorCodes.DF1220.value,
                    )
                )
                continue

            resolved = getattr(builtins_module, target.id, None) or getattr(
                vy_decorators_module, target.id, None
            )
            target._metadata["_type"] = resolved

            if isinstance(resolved, type) and issubclass(resolved, builtins_module.VyDecorator):
                resolved.__post_vy_check__(container, fn, dec, errors)

            if target.id not in imported_names:
                errors.append(
                    DamlForgeException(
                        f"`{target.id}` is not defined - import the decorator before using it",
                        target,
                        code=ErrorCodes.DF1219.value,
                    )
                )


def check_consuming_variant_scope(
    container: vy_ast.TopLevel, errors: ExceptionList, only_names: Optional[frozenset] = None
) -> None:
    from forge.builtins import _consuming_variant_from_ast

    for fn in container.body:
        if not isinstance(fn, vy_ast.FunctionDef):
            continue
        if only_names is not None and fn.name not in only_names:
            continue

        for call in _choice_calls(fn):
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            consuming = kwargs.get("consuming")
            variant = None if consuming is None else _consuming_variant_from_ast(consuming)

            if variant in TEMPLATE_ONLY_CONSUMING_VARIANTS:
                errors.append(
                    DamlForgeException(
                        f"Choice `{container.name}.{fn.name}` declares "
                        f'`consuming="{variant}"`, which is not supported on '
                        "interface choices — only `consuming`/`nonconsuming` are",
                        fn,
                        code=ErrorCodes.DF1208.value,
                    )
                )


def _interface_choice_names(interface_decl: vy_ast.InterfaceDef) -> frozenset:
    return frozenset(
        stmt.name
        for stmt in interface_decl.body
        if isinstance(stmt, vy_ast.FunctionDef) and any(_choice_calls(stmt))
    )


def _interface_view_type_name(interface_decl: vy_ast.InterfaceDef) -> Optional[str]:
    for kw in interface_decl.keywords:
        if kw.arg == "view" and isinstance(kw.value, vy_ast.Name):
            return kw.value.id
    return None


def _template_implemented_interfaces(template: vy_ast.TemplateDef, declarations: dict) -> list:
    result = []
    for base in template.bases:
        if isinstance(base, vy_ast.Name):
            decl = declarations.get(base.id)
            if isinstance(decl, vy_ast.InterfaceDef):
                result.append((base.id, decl))
    return result


def check_template_interface_bases(
    template: vy_ast.TemplateDef, declarations: dict, errors: ExceptionList
) -> None:
    template_method_names = {
        stmt.name for stmt in template.body if isinstance(stmt, vy_ast.FunctionDef)
    }

    for base in template.bases:
        if not isinstance(base, vy_ast.Name):
            errors.append(
                DamlForgeException(
                    f"Template `{template.name}` base must be a plain interface name",
                    base,
                    code=ErrorCodes.DF1227.value,
                )
            )
            continue

        decl = declarations.get(base.id)
        if not isinstance(decl, vy_ast.InterfaceDef):
            errors.append(
                DamlForgeException(
                    f"Template `{template.name}` declares `{base.id}` as a base, but "
                    f"`{base.id}` is not a known `interface`",
                    base,
                    code=ErrorCodes.DF1227.value,
                )
            )
            continue

        missing = _interface_choice_names(decl) - template_method_names
        for name in sorted(missing):
            errors.append(
                DamlForgeException(
                    f"Template `{template.name}` implements interface `{base.id}` but "
                    f"does not implement its choice `{name}`",
                    template,
                    code=ErrorCodes.DF1228.value,
                )
            )

        view_type = _interface_view_type_name(decl)
        if view_type is not None:
            has_matching_view = any(
                isinstance(stmt, vy_ast.FunctionDef)
                and _has_decorator(stmt, "view")
                and isinstance(stmt.returns, vy_ast.Name)
                and stmt.returns.id == view_type
                for stmt in template.body
            )
            if not has_matching_view:
                errors.append(
                    DamlForgeException(
                        f"Template `{template.name}` implements interface `{base.id}` but "
                        f"has no `@view` method returning `{view_type}`",
                        template,
                        code=ErrorCodes.DF1228.value,
                    )
                )


def check_template_interface_choice_consuming_variants(
    template: vy_ast.TemplateDef, declarations: dict, errors: ExceptionList
) -> None:
    interface_choice_names: set = set()
    for _, decl in _template_implemented_interfaces(template, declarations):
        interface_choice_names |= _interface_choice_names(decl)

    if interface_choice_names:
        check_consuming_variant_scope(
            template, errors, only_names=frozenset(interface_choice_names)
        )


def _call_name(node) -> str | None:
    if isinstance(node, vy_ast.Name):
        return node.id
    if isinstance(node, vy_ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _ledger_effect_name(call: vy_ast.Call, ledger_effect_fns) -> str | None:
    effect_name = _call_name(call.func) if call._metadata.get("is_ledger_effect") else None
    if effect_name is None:
        effect_name = call._metadata.get("daml_choice_call")
    if effect_name is None and (
        isinstance(call.func, vy_ast.Name) and call.func.id in ledger_effect_fns
    ):
        effect_name = call.func.id
    return effect_name


def _imported_effect_names(module: vy_ast.Module) -> set[str]:
    effects: set[str] = set()
    for stmt in module.body:
        if not isinstance(stmt, (vy_ast.Import, vy_ast.ImportFrom)):
            continue
        for info in stmt._metadata.get("import_infos", ()) or ():
            imported = getattr(info, "ast", None)
            if imported is None:
                continue
            imported_effects = set(imported._metadata.get("effect_functions", ()))
            if isinstance(stmt, vy_ast.ImportFrom):
                effects.update(
                    alias.asname or alias.name
                    for alias in stmt.names
                    if alias.name in imported_effects
                )
            else:
                module_alias = next(
                    (
                        alias.asname or alias.name
                        for alias in stmt.names
                        if alias.name == getattr(info, "alias", None)
                    ),
                    getattr(info, "alias", None),
                )
                if module_alias:
                    effects.update(f"{module_alias}.{name}" for name in imported_effects)
    return effects


def _function_has_ledger_effect(fn: vy_ast.FunctionDef, known_effects: set[str]) -> bool:
    # Daml's Update expressions are documented at:
    # https://docs.digitalasset.com/build/3.4/reference/daml/structure.html
    # Forge treats calls that construct or inspect an update (create, exercise, fetch,
    # fetchByKey/lookupByKey, archive, getTime, and imported Daml update helpers),
    # choice calls, and `assert` statements as ledger effects.  The effect is
    # transitive: a function calling another inferred effectful function is effectful too.
    if any(isinstance(node, vy_ast.Assert) for node in fn.get_descendants(vy_ast.Assert)):
        return True
    return any(
        _ledger_effect_name(call, known_effects) is not None
        for call in fn.get_descendants(vy_ast.Call)
    )


def infer_ledger_effects(module: vy_ast.Module) -> None:
    """Infer which module-level functions return Daml ``Update`` actions.

    User code does not need an effect marker.  External stub declarations may
    still carry effect information in their ``DamlName`` metadata because a
    declaration with a ``pass`` body contains no operation for inference to see.
    """

    known_effects = set(collect_daml_builtins().ledger_effect_fns)
    known_effects.update(module._metadata.get("intrinsic_effect_fns", ()))
    known_effects.update(_imported_effect_names(module))
    functions = [node for node in module.body if isinstance(node, vy_ast.FunctionDef)]
    inferred: set[str] = set()

    changed = True
    while changed:
        changed = False
        for fn in functions:
            if fn.name in inferred or not _function_has_ledger_effect(fn, known_effects):
                continue
            inferred.add(fn.name)
            known_effects.add(fn.name)
            fn._metadata["is_ledger_effect"] = True
            changed = True

    effects = set(module._metadata.get("intrinsic_effect_fns", ()))
    effects.update(_imported_effect_names(module))
    effects.update(inferred)
    module._metadata["effect_functions"] = tuple(sorted(effects))


def check_local_imports(container: vy_ast.TopLevel, errors: ExceptionList) -> None:
    """Reject imports in function bodies before lowering encounters them."""
    functions = (
        [container]
        if isinstance(container, vy_ast.FunctionDef)
        else container.get_descendants(vy_ast.FunctionDef)
    )
    for fn in functions:
        for import_node in fn.get_descendants((vy_ast.Import, vy_ast.ImportFrom)):
            errors.append(
                DamlForgeException(
                    "Imports are only supported at module scope",
                    import_node,
                    code=ErrorCodes.DF1213.value,
                )
            )


def check_unsupported_control_flow(container: vy_ast.TopLevel, errors: ExceptionList) -> None:
    """Reject control-flow statements that have no DamlForge IR equivalent."""
    functions = (
        [container]
        if isinstance(container, vy_ast.FunctionDef)
        else container.get_descendants(vy_ast.FunctionDef)
    )
    for fn in functions:
        for stmt in fn.get_descendants((vy_ast.Break, vy_ast.Continue, vy_ast.Raise)):
            errors.append(
                DamlForgeException(
                    f"`{type(stmt).__name__.lower()}` is not supported",
                    stmt,
                    code=ErrorCodes.DF1230.value,
                )
            )
        for nested in fn.get_descendants(vy_ast.FunctionDef):
            errors.append(
                DamlForgeException(
                    "Nested function definitions are not supported",
                    nested,
                    code=ErrorCodes.DF1230.value,
                )
            )


def check_template_observer(template: vy_ast.TemplateDef, errors: ExceptionList) -> None:
    stmt = _find_reserved_declaration(template, "observer")
    if stmt is None:
        return

    if isinstance(stmt, (vy_ast.AnnAssign, vy_ast.Assign)) and isinstance(stmt.value, vy_ast.List):
        if len(stmt.value.elements) == 0:
            errors.append(
                DamlForgeException(
                    f"Template `{template.name}` declares `observer` with an empty list",
                    stmt,
                    code=ErrorCodes.DF1201.value,
                )
            )

    return


def check_template_signatory(template: vy_ast.TemplateDef, errors: ExceptionList) -> None:
    for stmt in template.body:
        target = None
        if isinstance(stmt, (vy_ast.AnnAssign, vy_ast.Assign)):
            target = stmt.target

        if isinstance(target, vy_ast.Name) and target.id == "signatory":
            if isinstance(target.parent, vy_ast.AnnAssign) and isinstance(
                target.parent.value, vy_ast.List
            ):
                if len(target.parent.value.elements) == 0:
                    errors.append(
                        DamlForgeException(
                            f"Template `{template.name}` declares `signatory` with an empty list",
                            target.parent,
                            code=ErrorCodes.DF1201.value,
                        )
                    )
            return

    errors.append(
        DamlForgeException(
            f"Template `{template.name}` must declare an explicit `signatory`",
            template,
            code=ErrorCodes.DF1201.value,
        )
    )


def check_template_reserved_declarations(
    template: vy_ast.TemplateDef, errors: ExceptionList
) -> None:
    """Reject duplicate template clauses before lowering keeps only the first one."""
    first_by_name: dict[str, object] = {}
    reserved_names = {"signatory", "observer", "key", "maintainer", "ensure"}

    for stmt in template.body:
        if not isinstance(stmt, (vy_ast.AnnAssign, vy_ast.Assign)):
            continue
        target = stmt.target
        if not isinstance(target, vy_ast.Name) or target.id not in reserved_names:
            continue

        previous = first_by_name.setdefault(target.id, stmt)
        if previous is not stmt:
            errors.append(
                DamlForgeException(
                    f"Template `{template.name}` declares `{target.id}` more than once",
                    stmt,
                    prev_decl=previous,
                    code=ErrorCodes.DF1232.value,
                )
            )


def check_duplicate_record_fields(
    container: vy_ast.StructDef | vy_ast.TemplateDef, errors: ExceptionList
) -> None:
    """Reject duplicate Daml record fields before code generation."""
    reserved_names = {"signatory", "observer", "key", "maintainer", "ensure"}
    first_by_name: dict[str, object] = {}
    for stmt in container.body:
        if not isinstance(stmt, vy_ast.AnnAssign) or not isinstance(stmt.target, vy_ast.Name):
            continue
        if isinstance(container, vy_ast.TemplateDef) and stmt.target.id in reserved_names:
            continue
        previous = first_by_name.setdefault(stmt.target.id, stmt)
        if previous is not stmt:
            errors.append(
                DamlForgeException(
                    f"{type(container).__name__.removesuffix('Def')} `{container.name}` declares "
                    f"field `{stmt.target.id}` more than once",
                    stmt,
                    prev_decl=previous,
                    code=ErrorCodes.DF1232.value,
                )
            )


def check_pure_container_values(
    container: vy_ast.StructDef | vy_ast.TemplateDef, errors: ExceptionList
) -> None:
    """Reject ledger actions in record initializers and template clauses."""
    known_effects = set(collect_daml_builtins().ledger_effect_fns)
    known_effects.update(container.module_node._metadata.get("effect_functions", ()))
    for stmt in container.body:
        if not isinstance(stmt, (vy_ast.AnnAssign, vy_ast.Assign)) or stmt.value is None:
            continue
        for call in stmt.value.get_descendants(vy_ast.Call, include_self=True):
            effect_name = _ledger_effect_name(call, known_effects)
            if effect_name is None:
                continue
            errors.append(
                DamlForgeException(
                    f"Ledger effect `{effect_name}` cannot be used in the pure value of "
                    f"`{container.name}.{getattr(stmt.target, 'id', '<expression>')}`",
                    call,
                    code=ErrorCodes.DF1230.value,
                )
            )
            break


def check_template_methods(template: vy_ast.TemplateDef, errors: ExceptionList) -> None:
    """Reject template methods that lowering would otherwise silently discard."""
    ledger_effect_fns = collect_daml_builtins().ledger_effect_fns
    for fn in template.body:
        if not isinstance(fn, vy_ast.FunctionDef):
            continue
        if fn.name == "__init__" or any(
            _has_decorator(fn, name) for name in ("choice", "checked_fetch", "view")
        ):
            continue
        if any(
            _ledger_effect_name(call, ledger_effect_fns) is not None
            for call in fn.get_descendants(vy_ast.Call)
        ):
            continue

        errors.append(
            DamlForgeException(
                f"Template method `{template.name}.{fn.name}` is not supported; "
                "decorate it with `@choice` or move it to module scope",
                fn,
                code=ErrorCodes.DF1233.value,
            )
        )


def _find_reserved_declaration(template: vy_ast.TemplateDef, name: str):
    for stmt in template.body:
        target = None
        if isinstance(stmt, (vy_ast.AnnAssign, vy_ast.Assign)):
            target = stmt.target

        if isinstance(target, vy_ast.Name) and target.id == name:
            return stmt
    return None


def _has_reserved_declaration(template: vy_ast.TemplateDef, name: str) -> bool:
    return _find_reserved_declaration(template, name) is not None


NON_BOOLEAN_EXPRESSION_TYPES = (
    vy_ast.Int,
    vy_ast.Decimal,
    vy_ast.Str,
    vy_ast.List,
    vy_ast.Tuple,
    vy_ast.Dict,
    vy_ast.BinOp,
)


def _is_boolean_shaped(node) -> bool:
    if node is None:
        return False
    if isinstance(node, vy_ast.NameConstant):
        return isinstance(node.value, bool)
    if isinstance(node, NON_BOOLEAN_EXPRESSION_TYPES):
        return False
    return True


def _declared_annotation_rejects_bool(stmt) -> bool:
    if not isinstance(stmt, vy_ast.AnnAssign):
        return False
    annotation = stmt.annotation
    return not (isinstance(annotation, vy_ast.Name) and annotation.id == "bool")


def check_template_ensure(template: vy_ast.TemplateDef, errors: ExceptionList) -> None:
    stmt = _find_reserved_declaration(template, "ensure")
    if stmt is None:
        return

    value = stmt.value if isinstance(stmt, (vy_ast.AnnAssign, vy_ast.Assign)) else None
    invalid = not _is_boolean_shaped(value) or _declared_annotation_rejects_bool(stmt)
    if invalid:
        errors.append(
            DamlForgeException(
                f"Template `{template.name}` declares `ensure` with a value that isn't "
                "a boolean-shaped expression (comparison, boolean operator, `not`, or a call)",
                stmt,
                code=ErrorCodes.DF1212.value,
            )
        )


def check_template_key_maintainer(template: vy_ast.TemplateDef, errors: ExceptionList) -> None:
    has_key = _has_reserved_declaration(template, "key")
    has_maintainer = _has_reserved_declaration(template, "maintainer")

    if has_key and not has_maintainer:
        errors.append(
            DamlForgeException(
                f"Template `{template.name}` declares `key` without a `maintainer`",
                template,
                code=ErrorCodes.DF1210.value,
            )
        )
    elif has_maintainer and not has_key:
        errors.append(
            DamlForgeException(
                f"Template `{template.name}` declares `maintainer` without a `key`",
                template,
                code=ErrorCodes.DF1211.value,
            )
        )
    elif has_key:
        check_template_key_declarations(template, errors)


def check_template_key_declarations(template: vy_ast.TemplateDef, errors: ExceptionList) -> None:
    key = _find_reserved_declaration(template, "key")
    if not isinstance(key, vy_ast.AnnAssign):
        message = f"Template `{template.name}` declares `key` without a type annotation"
        errors.append(DamlForgeException(message, key, code=ErrorCodes.DF1213.value))
    maintainer = _find_reserved_declaration(template, "maintainer")
    names = {n.id for n in maintainer.value.get_descendants(vy_ast.Name, include_self=True)}
    fields = {getattr(getattr(s, "target", None), "id", None) for s in template.body} | {"self"}
    if "key" not in names or names & (fields - {"key"}):
        message = f"Template `{template.name}` must compute `maintainer` from `key` only"
        errors.append(DamlForgeException(message, maintainer, code=ErrorCodes.DF1236.value))


def check_contract_key_calls(
    node: vy_ast.TopLevel, declarations: dict, errors: ExceptionList
) -> None:
    for call in node.get_descendants(vy_ast.Call):
        name = getattr(call.func, "id", None)
        if name not in TEMPLATE_ARGUMENT_FUNCTIONS:
            continue
        template = declarations.get(getattr(call.args[0], "id", None)) if call.args else None
        has_key = isinstance(template, vy_ast.TemplateDef) and _has_reserved_declaration(
            template, "key"
        )
        if not has_key:
            message = f"The first argument of `{name}` must be a template with a contract key"
            errors.append(DamlForgeException(message, call, code=ErrorCodes.DF1235.value))


def _target_module_from_import_info(info):
    for attr in ("module", "module_ast", "ast", "resolved_module"):
        module = getattr(info, attr, None)
        if isinstance(module, vy_ast.Module):
            return module
    if isinstance(info, dict):
        for attr in ("module", "module_ast", "ast", "resolved_module"):
            module = info.get(attr)
            if isinstance(module, vy_ast.Module):
                return module
    return None


def _reachable_modules(modules) -> list[vy_ast.Module]:
    seen = set()
    ordered = []

    while modules:
        module = modules.pop()
        marker = id(module)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(module)

        for stmt in module.body:
            if not isinstance(stmt, (vy_ast.Import, vy_ast.ImportFrom)):
                continue
            for info in stmt._metadata.get("import_infos", ()) or ():
                imported_module = _target_module_from_import_info(info)
                if imported_module is not None:
                    modules.append(imported_module)

    return ordered


def check_enum_member_names(node: vy_ast.EnumDef, errors: ExceptionList) -> None:
    members = set()
    for stmt in node.body:
        name = vy_ast.enum_member_name(stmt)
        if name is None:
            errors.append(
                DamlForgeException(
                    f"Enum `{node.name}` members must be bare names or assigned `auto()`",
                    stmt,
                    code=ErrorCodes.DF1231.value,
                )
            )
            continue
        if name in members:
            errors.append(
                DamlForgeException(
                    f"Enum member `{name}` is declared more than once",
                    stmt,
                    code=ErrorCodes.DF1231.value,
                )
            )
        members.add(name)
        if not ENUM_MEMBER_NAME.match(name):
            errors.append(
                DamlForgeException(
                    f"Enum member `{name}` of `{node.name}` must be SCREAMING_SNAKE_CASE",
                    stmt,
                    code=ErrorCodes.DF1217.value,
                )
            )
        if isinstance(stmt, vy_ast.Assign) and not _is_auto_call(stmt.value):
            errors.append(
                DamlForgeException(
                    f"Enum member `{name}` must use `auto()`; explicit values are not supported",
                    stmt.value,
                    code=ErrorCodes.DF1231.value,
                )
            )


def _is_auto_call(value) -> bool:
    return (
        isinstance(value, vy_ast.Call)
        and isinstance(value.func, vy_ast.Name)
        and value.func.id == "auto"
        and not value.args
        and not value.keywords
    )


def check_container(
    node: vy_ast.TopLevel, errors: ExceptionList, declarations: Optional[dict] = None
) -> None:
    if isinstance(node, vy_ast.EnumDef):
        check_enum_member_names(node, errors)
    elif isinstance(node, vy_ast.StructDef):
        check_duplicate_record_fields(node, errors)
        check_pure_container_values(node, errors)
    elif isinstance(node, vy_ast.VariantDef):
        check_unknown_decorators(node, errors)
        for constructor in node.body:
            if isinstance(constructor, vy_ast.Pass) and len(node.body) == 1:
                continue
            if not isinstance(constructor, vy_ast.FunctionDef):
                errors.append(
                    DamlForgeException(
                        f"Variant `{node.name}` may contain only constructor declarations",
                        constructor,
                        code=ErrorCodes.DF1203.value,
                    )
                )
                continue
            for arg in constructor.args.args:
                if arg.arg != "self" and arg.annotation is None:
                    errors.append(
                        DamlForgeException(
                            f"Variant constructor `{constructor.name}` argument "
                            f"`{arg.arg}` requires a type annotation",
                            arg,
                            code=ErrorCodes.DF1213.value,
                        )
                    )
    elif isinstance(node, vy_ast.TemplateDef):
        check_duplicate_record_fields(node, errors)
        check_pure_container_values(node, errors)
        check_template_reserved_declarations(node, errors)
        check_template_methods(node, errors)
        check_template_signatory(node, errors)
        check_template_observer(node, errors)
        check_template_key_maintainer(node, errors)
        check_template_ensure(node, errors)
        check_unknown_decorators(node, errors)
        if node.bases:
            check_template_interface_bases(node, declarations or {}, errors)
            check_template_interface_choice_consuming_variants(node, declarations or {}, errors)
    elif isinstance(node, vy_ast.InterfaceDef):
        check_unknown_decorators(node, errors)
        check_consuming_variant_scope(node, errors)
