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

from collections import defaultdict
from typing import Optional

from forge.ast import nodes as vy_ast
from forge.exceptions import DamlForgeException, ExceptionList
from forge.models.error_codes import ErrorCodes
from forge.semantics.analysis.intrinsics import DatetimeIntrinsicAnalyzer
from forge.semantics.analysis.primitive_values import check_primitive_policies
from forge.semantics.analysis.typecheck import check_bidirectional_types
from forge.semantics.analysis.structure import (
    _reachable_modules,
    check_container,
    check_contract_key_calls,
    check_local_imports,
    check_unsupported_control_flow,
    infer_ledger_effects,
)
from forge.semantics.analysis.types import (
    ModuleScope,
    check_call_arity,
    check_container_type_annotations,
    check_enum_value_access,
    check_unresolved_names,
    mark_list_subscripts,
    mark_optional_conversions,
)

_FnKey = tuple[str, tuple[str, ...], str]


class RecursionAnalyzer:
    """Validate recursive calls across the resolved import graph."""

    def __init__(self, module: vy_ast.Module, errors: ExceptionList) -> None:
        self.modules = _reachable_modules([module])
        self.errors = errors
        self.functions_by_key: dict[_FnKey, vy_ast.FunctionDef] = {}
        self.functions_by_module_name: dict[str, str] = {}
        self.top_level_by_module: defaultdict[str, dict[str, _FnKey]] = defaultdict(dict)
        self.methods_by_scope: defaultdict[tuple[str, tuple[str, ...]], dict[str, _FnKey]] = (
            defaultdict(dict)
        )
        self.module_aliases: defaultdict[str, dict[str, str]] = defaultdict(dict)
        self.direct_imports: defaultdict[str, dict[str, _FnKey]] = defaultdict(dict)
        self.edges: defaultdict[_FnKey, list[tuple[_FnKey, vy_ast.Call]]] = defaultdict(list)
        self._recursive_calls_cache: Optional[list[tuple[vy_ast.FunctionDef, vy_ast.Call]]] = None

        self._index_modules()
        self._index_imports()
        self._index_edges()

    @staticmethod
    def _module_names(module: vy_ast.Module) -> tuple[str, ...]:
        names: list[str] = []
        for value in (getattr(module, "resolved_path", None), getattr(module, "path", None)):
            if not value:
                continue
            text = str(value)
            names.extend((text, text.rsplit("/", 1)[-1].rsplit(".", 1)[0]))
            names.append(text.rsplit(".", 1)[0].replace("/", "."))
        return tuple(dict.fromkeys(names))

    @classmethod
    def _module_key(cls, module: vy_ast.Module) -> str:
        names = cls._module_names(module)
        return names[0] if names else f"<module:{id(module)}>"

    @staticmethod
    def _function_scope(fn: vy_ast.FunctionDef) -> tuple[str, ...]:
        parent = fn.parent
        if isinstance(parent, vy_ast.Module):
            return ()
        if isinstance(parent, vy_ast.TopLevel):
            return (parent.name,)
        return ()

    @classmethod
    def _function_key(cls, fn: vy_ast.FunctionDef) -> _FnKey:
        return (cls._module_key(fn.module_node), cls._function_scope(fn), fn.name)

    @classmethod
    def _function_display(cls, fn: vy_ast.FunctionDef) -> str:
        scope = (*cls._function_scope(fn), fn.name)
        names = cls._module_names(fn.module_node)
        return ".".join((names[-1], *scope)) if names else ".".join(scope)

    def _index_modules(self) -> None:
        for module in self.modules:
            key = self._module_key(module)
            for name in self._module_names(module):
                self.functions_by_module_name[name] = key
            for fn in module.get_descendants(vy_ast.FunctionDef):
                fn_key = self._function_key(fn)
                self.functions_by_key[fn_key] = fn
                if self._function_scope(fn):
                    self.methods_by_scope[(key, self._function_scope(fn))][fn.name] = fn_key
                else:
                    self.top_level_by_module[key][fn.name] = fn_key

    def _find_module_key(self, name: str) -> Optional[str]:
        return self.functions_by_module_name.get(name) or self.functions_by_module_name.get(
            name.rsplit(".", 1)[-1]
        )

    def _index_imports(self) -> None:
        for module in self.modules:
            module_key = self._module_key(module)
            for stmt in module.body:
                if isinstance(stmt, vy_ast.Import):
                    for alias in stmt.names:
                        imported_key = self._find_module_key(alias.name)
                        if imported_key is not None:
                            self.module_aliases[module_key][
                                alias.asname or alias.name
                            ] = imported_key
                elif isinstance(stmt, vy_ast.ImportFrom):
                    imported_key = self._find_module_key(stmt.module)
                    if imported_key is None:
                        continue
                    for alias in stmt.names:
                        imported_fn = self.top_level_by_module[imported_key].get(alias.name)
                        if imported_fn is not None:
                            self.direct_imports[module_key][
                                alias.asname or alias.name
                            ] = imported_fn

    def _resolve_call(self, fn: vy_ast.FunctionDef, call: vy_ast.Call) -> Optional[_FnKey]:
        module_key, scope, _ = self._function_key(fn)
        call_fn = call.func
        if isinstance(call_fn, vy_ast.Name):
            if scope and (target := self.methods_by_scope[(module_key, scope)].get(call_fn.id)):
                return target
            return self.top_level_by_module[module_key].get(call_fn.id) or self.direct_imports[
                module_key
            ].get(call_fn.id)
        if not isinstance(call_fn, vy_ast.Attribute):
            return None
        value = call_fn.value
        if not isinstance(value, vy_ast.Name):
            return None
        if value.id == "self" and scope:
            return self.methods_by_scope[(module_key, scope)].get(call_fn.attr)
        imported_key = self.module_aliases[module_key].get(value.id)
        if imported_key is None:
            return None
        return self.top_level_by_module[imported_key].get(call_fn.attr)

    def _index_edges(self) -> None:
        for fn_key, fn in self.functions_by_key.items():
            for call in fn.get_descendants(vy_ast.Call):
                target = self._resolve_call(fn, call)
                if target is not None:
                    self.edges[fn_key].append((target, call))

    def _recursive_calls(self) -> list[tuple[vy_ast.FunctionDef, vy_ast.Call]]:
        if self._recursive_calls_cache is not None:
            return self._recursive_calls_cache

        calls = []
        index = 0
        stack = []
        indexes = {}
        lowlinks = {}
        on_stack = set()

        def strongconnect(fn_key):
            nonlocal index
            indexes[fn_key] = lowlinks[fn_key] = index
            index += 1
            stack.append(fn_key)
            on_stack.add(fn_key)
            for target, _ in self.edges.get(fn_key, ()):
                if target not in indexes:
                    strongconnect(target)
                    lowlinks[fn_key] = min(lowlinks[fn_key], lowlinks[target])
                elif target in on_stack:
                    lowlinks[fn_key] = min(lowlinks[fn_key], indexes[target])
            if lowlinks[fn_key] != indexes[fn_key]:
                return
            component = set()
            while True:
                target = stack.pop()
                on_stack.remove(target)
                component.add(target)
                if target == fn_key:
                    break
            if len(component) == 1:
                only = next(iter(component))
                calls.extend(
                    (self.functions_by_key[only], call)
                    for target, call in self.edges.get(only, ())
                    if target == only
                )
                return
            calls.extend(
                (self.functions_by_key[source], call)
                for source in component
                for target, call in self.edges.get(source, ())
                if target in component
            )

        for fn_key in self.functions_by_key:
            if fn_key not in indexes:
                strongconnect(fn_key)
        self._recursive_calls_cache = calls
        return calls

    def analyze(self) -> None:
        self._check_recursion()

    def _check_recursion(self) -> None:
        for fn, call in self._recursive_calls():
            self.errors.append(
                DamlForgeException(
                    f"Function `{self._function_display(fn)}` is recursive, which is not supported",
                    call,
                    code=ErrorCodes.DF1209.value,
                )
            )


class ModuleAnalyzer:
    """Analyze one module and recursively analyze its imported modules."""

    def __init__(
        self, module: vy_ast.Module, errors: ExceptionList, analyzed: set[int] | None = None
    ) -> None:
        self.module = module
        self.errors = errors
        self.scope = ModuleScope(module)
        self.analyzed = analyzed if analyzed is not None else set()

    def analyze(self) -> None:
        marker = id(self.module)
        if marker in self.analyzed:
            return
        self.analyzed.add(marker)

        # Top level analyzers that need to run before visiting the module body
        self._analyze_imports()
        self._check_enum_constructor_collisions()
        self._check_duplicate_top_level_functions()
        DatetimeIntrinsicAnalyzer(self.module, self.errors).analyze()
        infer_ledger_effects(self.module)

        # Run child level analyzing, i.e. body of the module, which will recursively
        for node in self.module.body:
            self.visit(node)

        # Type checking annotates ContractId choice calls and other resolved
        # operations, so run inference once more after child analysis.
        infer_ledger_effects(self.module)

    def _analyze_imports(self) -> None:
        for node in self.module.body:
            if not isinstance(node, (vy_ast.Import, vy_ast.ImportFrom)):
                continue
            for info in node._metadata.get("import_infos", ()):
                imported_module = getattr(info, "ast", None)
                if imported_module is None:
                    continue
                ModuleAnalyzer(imported_module, self.errors, self.analyzed).analyze()

    def _check_enum_constructor_collisions(self) -> None:
        """Daml constructors share one module-level namespace.

        Vyper permits the same member spelling in different enums, but emitting
        both as unqualified Daml constructors makes damlc reject the module.
        Report it at the source declaration instead.
        """
        seen: dict[str, str] = {}
        template_names = {
            node.name for node in self.module.body if isinstance(node, vy_ast.TemplateDef)
        }
        for enum in (node for node in self.module.body if isinstance(node, vy_ast.EnumDef)):
            for stmt in enum.body:
                name = vy_ast.enum_member_name(stmt)
                if name is None:
                    continue
                if name in template_names:
                    self.errors.append(
                        DamlForgeException(
                            f"Enum member `{name}` conflicts with template `{name}`; Daml "
                            "constructors must be unique within a module",
                            stmt,
                            code=ErrorCodes.DF1231.value,
                        )
                    )
                previous = seen.get(name)
                if previous is not None and previous != enum.name:
                    self.errors.append(
                        DamlForgeException(
                            f"Enum member `{name}` conflicts with enum `{previous}`; Daml "
                            "constructors must be unique within a module",
                            stmt,
                            code=ErrorCodes.DF1231.value,
                        )
                    )
                else:
                    seen[name] = enum.name

    def _check_duplicate_top_level_functions(self) -> None:
        """Reject duplicate Python function definitions before Daml emission."""
        first_by_name: dict[str, vy_ast.FunctionDef] = {}
        for node in self.module.body:
            if not isinstance(node, vy_ast.FunctionDef):
                continue
            previous = first_by_name.setdefault(node.name, node)
            if previous is node:
                continue
            self.errors.append(
                DamlForgeException(
                    f"Function `{node.name}` is defined more than once in this module",
                    node,
                    prev_decl=previous,
                    code=ErrorCodes.DF1232.value,
                )
            )

    def visit(self, node: vy_ast.TopLevel) -> None:
        visitor = getattr(self, f"visit_{type(node).__name__}", self.visit_top_level)
        visitor(node)

    def visit_Import(self, node: vy_ast.Import) -> None:
        return None

    def visit_ImportFrom(self, node: vy_ast.ImportFrom) -> None:
        return None

    def visit_top_level(self, node: vy_ast.TopLevel) -> None:
        check_container(node, self.errors, self.scope.declarations)
        check_local_imports(node, self.errors)
        check_unsupported_control_flow(node, self.errors)
        check_container_type_annotations(node, self.scope.type_names, self.errors)
        check_primitive_policies(
            node,
            self.scope.field_types,
            self.scope.return_types,
            self.scope.function_param_types,
            self.errors,
        )
        check_enum_value_access(node, self.scope.declarations, self.errors)
        check_contract_key_calls(node, self.scope.declarations, self.errors)
        mark_list_subscripts(node, self.scope.list_returning_functions)
        check_unresolved_names(node, self.scope.declarations, self.scope.global_names, self.errors)
        check_call_arity(node, self.scope.function_param_types, self.errors)
        check_bidirectional_types(
            node,
            self.scope.field_types,
            self.scope.return_types,
            self.scope.function_param_types,
            self.scope.function_param_names,
            self.scope.choice_names,
            self.errors,
            self.scope.type_names,
        )
        mark_optional_conversions(node, self.scope.field_types, self.scope.return_types)
