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

from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass, replace

from forge.builtins import collect_daml_builtins
from forge.builtins.maps import TextMap, known_map_types
from forge.ir.nodes import (
    IRAttribute,
    IRCall,
    IRConstant,
    IRDict,
    IRExpr,
    IRFold,
    IRIfExp,
    IRImport,
    IRModule,
    IRName,
    IRSubscript,
    TypeRef,
)
from forge.ir.normalize import LoopAnalysis

_OPTIONAL_METHODS = frozenset({"isSome", "isNone", "fromSome", "fromSomeNote", "fromOptional"})
_MAX_TUPLE_ACCESSOR_INDEX = 8


def _walk_ir(value):
    yield value
    if is_dataclass(value):
        for field in dataclass_fields(value):
            yield from _walk_ir(getattr(value, field.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_ir(item)


def _merge_imports(*groups) -> tuple[IRImport, ...]:
    merged = []
    for group in groups:
        for import_ in group:
            if import_ not in merged:
                merged.append(import_)
    return tuple(merged)


def _type_fields(module: IRModule) -> dict[str, dict[str, TypeRef]]:
    result = {}
    for struct in module.structs:
        result[struct.name] = {field.name: field.type for field in struct.fields if field.type}
    for template in module.templates:
        result[template.name] = {field.name: field.type for field in template.fields if field.type}
    return result


def _expr_type(expr: IRExpr, context_fields: dict[str, TypeRef], fields: dict) -> TypeRef | None:
    if isinstance(expr, IRName):
        return expr.type
    if isinstance(expr, IRAttribute):
        if isinstance(expr.value, IRName) and expr.value.id == "self":
            return context_fields.get(expr.attr)
        base = _expr_type(expr.value, context_fields, fields)
        return fields.get(base.name, {}).get(expr.attr) if base is not None else None
    if isinstance(expr, IRSubscript):
        base = _expr_type(expr.value, context_fields, fields)
        if base is None or not base.args:
            return None
        return base.args[-1] if base.name in ("GenMap", "TextMap") else base.args[0]
    if isinstance(expr, IRIfExp):
        return _expr_type(expr.body, context_fields, fields) or _expr_type(
            expr.orelse, context_fields, fields
        )
    return None


def _uses_dynamic_index(
    expr: IRSubscript, context_fields: dict[str, TypeRef], fields: dict
) -> bool:
    if expr.is_list:
        return True
    if (
        isinstance(expr.index, IRConstant)
        and isinstance(expr.index.value, int)
        and 0 <= expr.index.value <= _MAX_TUPLE_ACCESSOR_INDEX
    ):
        return False
    receiver_type = _expr_type(expr.value, context_fields, fields)
    return receiver_type is None or receiver_type.name not in ("GenMap", "TextMap")


def _scan_expr(
    expr: object | None, context_fields: dict[str, TypeRef], fields: dict, imports: list[IRImport]
) -> None:
    if expr is None:
        return
    if isinstance(expr, IRSubscript) and _uses_dynamic_index(expr, context_fields, fields):
        import_ = IRImport("DA.List", ("!!",))
        if import_ not in imports:
            imports.append(import_)
    if isinstance(expr, IRCall) and isinstance(expr.func, IRAttribute):
        receiver_type = _expr_type(expr.func.value, context_fields, fields)
        if receiver_type is not None and receiver_type.name == "Optional":
            import_ = IRImport("DA.Optional")
            if import_ not in imports:
                imports.append(import_)
    for value in _walk_ir(expr):
        if value is expr:
            continue
        if isinstance(value, IRSubscript):
            if _uses_dynamic_index(value, context_fields, fields):
                import_ = IRImport("DA.List", ("!!",))
                if import_ not in imports:
                    imports.append(import_)


def required_imports(module: IRModule) -> tuple[IRImport, ...]:
    imports = []
    builtins = collect_daml_builtins()
    builtin_import_modules = builtins.function_import_modules

    def add(import_: IRImport) -> None:
        if import_ not in imports:
            imports.append(import_)

    for value in _walk_ir(module):
        if isinstance(value, IRFold) and value.post_loop:
            add(IRImport("DA.Optional", ("fromSome",)))
        if isinstance(value, TypeRef):
            if value.name == "GenMap":
                add(IRImport("DA.Map", alias="Map", qualified=False))
            elif value.name == "TextMap":
                add(IRImport("DA.TextMap", alias="TextMap", qualified=False))
            elif value.name in ("set", "frozenset"):
                add(IRImport("DA.Set", alias="Set", qualified=False))
        elif isinstance(value, IRDict):
            module_name = known_map_types().get(value.kind, TextMap).module
            add(IRImport(f"DA.{module_name}", alias=module_name, qualified=False))
        elif isinstance(value, IRName):
            if value.id == "foldlA":
                add(IRImport("DA.Action", ("foldlA",)))
            elif value.id.startswith("Text."):
                add(IRImport("DA.Text", alias="Text"))
            elif value.id in _OPTIONAL_METHODS or value.id == "DA.Optional":
                add(IRImport("DA.Optional"))
            elif value.id.startswith("Map."):
                add(IRImport("DA.Map", alias="Map", qualified=False))
            elif value.id.startswith("TextMap."):
                add(IRImport("DA.TextMap", alias="TextMap", qualified=False))
            elif value.id.startswith("Set."):
                add(IRImport("DA.Set", alias="Set", qualified=False))
            elif (daml_name := builtins.function_renames.get(value.id)) in builtin_import_modules:
                add(IRImport(builtin_import_modules[daml_name]))

    fields = _type_fields(module)
    for fn in module.functions:
        for stmt in fn.body:
            _scan_expr(stmt, {}, fields, imports)
    for template in module.templates:
        context_fields = fields.get(template.name, {})
        for expr in (template.signatory, template.observer, template.ensure):
            _scan_expr(expr, context_fields, fields, imports)
        for choice in template.choices + tuple(
            c for ii in template.interface_instances for c in ii.choices
        ):
            _scan_expr(choice.controller, context_fields, fields, imports)
            _scan_expr(choice.observer, context_fields, fields, imports)
            for stmt in choice.body:
                _scan_expr(stmt, context_fields, fields, imports)
        if template.checked_fetch is not None and template.checked_fetch.return_type is not None:
            add(
                IRImport(
                    "Splice.Util",
                    ("HasCheckedFetch",),
                    constructor_names=frozenset(("HasCheckedFetch",)),
                )
            )

    effect_functions = collect_daml_builtins().ledger_effect_fns | frozenset(
        module.effect_functions
    )
    for fn in module.functions:
        if fn.effectful and LoopAnalysis.requires_foldl_a(
            LoopAnalysis.normalize_for_analysis(fn.body, effect_functions), True, effect_functions
        ):
            add(IRImport("DA.Action", ("foldlA",)))
    for template in module.templates:
        for choice in template.choices + tuple(
            c for ii in template.interface_instances for c in ii.choices
        ):
            if LoopAnalysis.requires_foldl_a(
                LoopAnalysis.normalize_for_analysis(choice.body, effect_functions),
                True,
                effect_functions,
            ):
                add(IRImport("DA.Action", ("foldlA",)))
    return tuple(imports)


def prepare_ir_module(module: IRModule) -> IRModule:
    required_modules = tuple(IRImport(name) for name in sorted(module.required_modules))
    return replace(
        module, imports=_merge_imports(module.imports, required_modules, required_imports(module))
    )
