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

import importlib
import sys
from pathlib import Path
from typing import Optional

from forge.ast import nodes as vy_ast
from forge.ast.parse import parse_to_ast
from forge.ast.utils import module_assigns
from forge.builtin_stubs import BUILTINS_DIR, is_stub_root, vendor_roots
from forge.exceptions import DamlForgeException, ExceptionList
from forge.models.error_codes import ErrorCodes
from forge.utils import sha256sum

_EXPORTED_DECLARATIONS = (
    vy_ast.StructDef,
    vy_ast.EnumDef,
    vy_ast.VariantDef,
    vy_ast.InterfaceDef,
    vy_ast.TemplateDef,
    vy_ast.FunctionDef,
)


class ImportInfo:
    __slots__ = ("alias", "qualified_module_name", "resolved_path", "ast")

    def __init__(
        self, alias: str, qualified_module_name: str, resolved_path: Path, ast: vy_ast.Module
    ):
        self.alias = alias
        self.qualified_module_name = qualified_module_name
        self.resolved_path = resolved_path
        self.ast = ast


class ImportGraph:
    def __init__(self) -> None:
        self.modules: dict = {}
        self.edges: dict = {}
        self.sibling_module_strs: set = set()

    def add_module(self, key: str, module: vy_ast.Module) -> None:
        self.modules[key] = module

    def add_edge(self, from_key: str, to_key: str) -> None:
        self.edges.setdefault(from_key, []).append(to_key)

    def __contains__(self, key: str) -> bool:
        return key in self.modules

    def __iter__(self):
        return iter(self.modules.values())

    def __len__(self) -> int:
        return len(self.modules)


class ImportsBuilder:
    def __init__(self) -> None:
        self.graph = ImportGraph()
        self._integrity_cache: dict = {}

    @classmethod
    def builtin_stub(cls, module_str: str) -> Optional[Path]:
        if not is_stub_root(cls._root_of(module_str)):
            return None
        path = (BUILTINS_DIR / cls._module_str_to_relpath(module_str)).with_suffix(".vy")
        return path if path.is_file() else None

    @staticmethod
    def is_builtin_stub_path(path) -> bool:
        return BUILTINS_DIR in Path(path).resolve().parents

    def _uses_stubs(self, node) -> bool:
        module_strs = self._module_strs_for(node)
        for module_str in module_strs:
            root = self._root_of(module_str)
            if root in vendor_roots() and not self.builtin_stub(module_str):
                raise DamlForgeException(
                    f"No module named `{module_str}` - no generated stub "
                    f"`{self._module_str_to_relpath(module_str)}.vy` in `{BUILTINS_DIR}`; "
                    f"generate it with `forge stubs <dar> -o {BUILTINS_DIR}`",
                    node,
                    code=ErrorCodes.DF1213.value,
                )
            if (
                isinstance(node, vy_ast.Import)
                and self.is_builtin_root(root)
                and not self.builtin_stub(module_str)
            ):
                raise DamlForgeException(
                    f"No module named `{module_str}` - no builtin stub is available",
                    node,
                    code=ErrorCodes.DF1213.value,
                )
        return bool(module_strs) and all(self.builtin_stub(m) for m in module_strs)

    @staticmethod
    def _root_of(module_path: str | None) -> str:
        return module_path.split(".", 1)[0] if module_path else ""

    @staticmethod
    def is_annotation_only_root(root: str) -> bool:
        if root in sys.stdlib_module_names:
            return True
        try:
            root_mod = importlib.import_module(root)
        except ImportError:
            return False
        return bool(getattr(root_mod, "__daml_annotation_only__", False))

    @staticmethod
    def is_builtin_root(root: str) -> bool:
        return is_stub_root(root)

    @classmethod
    def _import_real_module(cls, module_path: str):
        if cls.is_annotation_only_root(cls._root_of(module_path)):
            return importlib.import_module(module_path)
        return None

    @classmethod
    def check_import_names_resolve(cls, node, errors: ExceptionList) -> None:
        if isinstance(node, vy_ast.Import):
            for alias in node.names:
                root = cls._root_of(alias.name)
                if not cls.is_annotation_only_root(root):
                    continue
                try:
                    cls._import_real_module(alias.name)
                except ModuleNotFoundError:
                    errors.append(
                        DamlForgeException(
                            f"No module named `{alias.name}`", node, code=ErrorCodes.DF1213.value
                        )
                    )
            return
        if not isinstance(node, vy_ast.ImportFrom) or node.module is None:
            return

        try:
            mod = cls._import_real_module(node.module)
        except ModuleNotFoundError:
            errors.append(
                DamlForgeException(
                    f"No module named `{node.module}`", node, code=ErrorCodes.DF1213.value
                )
            )
            return
        if mod is None:
            return

        for alias in node.names:
            if alias.name == "*" or hasattr(mod, alias.name):
                continue
            errors.append(
                DamlForgeException(
                    f"No name `{alias.name}` in module `{node.module}`",
                    node,
                    code=ErrorCodes.DF1213.value,
                )
            )

    @classmethod
    def needs_daml_import(cls, module_path: str, name: str) -> bool:
        if name == "*":
            return True
        return not cls.is_annotation_only_root(cls._root_of(module_path))

    @classmethod
    def imported_names(cls, module: vy_ast.Module) -> set:
        from forge.builtins import known_builtin_function_names, known_decorator_names
        from forge.semantics.analysis.intrinsics import COMPILER_INTRINSIC_IMPORTS
        from forge.semantics.analysis.types import daml_builtin_type_names

        names = set()
        allowed_from_annotation_only = (
            daml_builtin_type_names() | known_decorator_names() | known_builtin_function_names()
        )
        for stmt in module.body:
            if isinstance(stmt, vy_ast.Import):
                for alias in stmt.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(stmt, vy_ast.ImportFrom):
                root = cls._root_of(stmt.module)
                for alias in stmt.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name
                    if (
                        cls.is_annotation_only_root(root)
                        and alias.name not in allowed_from_annotation_only
                        and alias.name
                        not in COMPILER_INTRINSIC_IMPORTS.get(stmt.module, frozenset())
                    ):
                        continue
                    names.add(bound)
        return names

    @staticmethod
    def check_no_aliased_from_import(node, errors: ExceptionList) -> None:
        if not isinstance(node, vy_ast.ImportFrom):
            return

        for alias in node.names:
            if alias.asname and alias.name != "*":
                errors.append(
                    DamlForgeException(
                        f"Aliased imports (`{alias.name} as {alias.asname}`) are not "
                        "supported - Daml has no per-symbol import alias; import "
                        f"`{alias.name}` directly, or use `import {node.module} as ...` "
                        "for whole-module qualified aliasing instead",
                        node,
                        code=ErrorCodes.DF1213.value,
                    )
                )

    @staticmethod
    def check_import_aliases(node, errors: ExceptionList) -> None:
        if not isinstance(node, vy_ast.Import):
            return

        for alias in node.names:
            if (
                alias.asname is not None
                and not ImportsBuilder.is_annotation_only_root(ImportsBuilder._root_of(alias.name))
                and not alias.asname[:1].isupper()
            ):
                errors.append(
                    DamlForgeException(
                        f"Module alias `{alias.asname}` must start with an uppercase letter "
                        "because Daml module aliases are constructors",
                        alias,
                        code=ErrorCodes.DF1213.value,
                    )
                )

    @staticmethod
    def _module_str_to_relpath(module_str: str) -> Path:
        return Path(*module_str.split("."))

    @staticmethod
    def _search_base(module: vy_ast.Module) -> Path:
        resolved = getattr(module, "resolved_path", None) or getattr(module, "path", None)
        if resolved:
            return Path(resolved).resolve().parent
        return Path.cwd()

    @classmethod
    def _find_sibling_vy_file(cls, module_str: str, search_base: Path) -> Optional[Path]:
        candidate = (search_base / cls._module_str_to_relpath(module_str)).with_suffix(".vy")
        return candidate if candidate.is_file() else None

    @staticmethod
    def _module_strs_for(node) -> list:
        if isinstance(node, vy_ast.ImportFrom):
            return [node.module]
        if isinstance(node, vy_ast.Import):
            return [alias.name for alias in node.names]
        return []

    @classmethod
    def _load_import(cls, module_str: str, search_base: Path, node) -> Optional[Path]:
        path = cls._find_sibling_vy_file(module_str, search_base)
        if path is not None:
            return path

        root = cls._root_of(module_str)
        if cls.is_annotation_only_root(root) or cls.is_builtin_root(root):
            return None

        raise DamlForgeException(
            f"No module named `{module_str}` - no sibling `.vy` file "
            f"`{cls._module_str_to_relpath(module_str)}.vy` found next to this file, and "
            f"`{root}` is not an importable Daml-backed or annotation-only Python package",
            node,
            code=ErrorCodes.DF1213.value,
        )

    def resolve_imports(
        self, module: vy_ast.Module, errors: ExceptionList, _stack: tuple = ()
    ) -> ImportGraph:
        search_base = self._search_base(module)
        resolved = getattr(module, "resolved_path", None) or getattr(module, "path", None)
        this_key = str(Path(resolved).resolve()) if resolved else f"<module:{id(module)}>"
        stack = _stack + (this_key,)
        self.graph.add_module(this_key, module)

        seen_in_module: dict = {}
        for node in module.body:
            if not isinstance(node, (vy_ast.Import, vy_ast.ImportFrom)):
                continue

            if isinstance(node, vy_ast.ImportFrom) and (node.level or node.module is None):
                errors.append(
                    DamlForgeException(
                        "Relative imports are not supported; use an absolute module import",
                        node,
                        code=ErrorCodes.DF1213.value,
                    )
                )
                continue

            self.check_no_aliased_from_import(node, errors)
            self.check_import_aliases(node, errors)
            stubbed = self._uses_stubs(node)
            if not stubbed:
                self.check_import_names_resolve(node, errors)

            infos = []
            for module_str in self._module_strs_for(node):
                if stubbed:
                    path = self.builtin_stub(module_str)
                else:
                    path = self._load_import(module_str, search_base, node)
                if path is None:
                    continue

                key = str(path.resolve())
                if key in stack:
                    cycle = " imports ".join((*stack, key))
                    raise DamlForgeException(
                        f"Import cycle detected: {cycle}", node, code=ErrorCodes.DF1213.value
                    )

                if key in seen_in_module:
                    raise DamlForgeException(
                        f"`{module_str}` is already imported earlier in this module",
                        seen_in_module[key],
                        node,
                        code=ErrorCodes.DF1213.value,
                    )
                seen_in_module[key] = node

                if not stubbed:
                    self.graph.sibling_module_strs.add(module_str)
                self.graph.add_edge(this_key, key)

                if key not in self.graph:
                    current_module_path = Path(getattr(module, "path", "") or "")
                    sibling_module_path = (
                        self._module_str_to_relpath(module_str)
                        if stubbed
                        else current_module_path.parent / self._module_str_to_relpath(module_str)
                    ).with_suffix(".vy")
                    sibling_ast = parse_to_ast(
                        path.read_text(),
                        module_path=str(sibling_module_path),
                        resolved_path=str(path),
                    )
                    self.graph.add_module(key, sibling_ast)
                    self.resolve_imports(sibling_ast, errors, _stack=stack)

                infos.append(ImportInfo(module_str, module_str, path, self.graph.modules[key]))

            if infos:
                node._metadata["import_infos"] = infos

            self.check_sibling_import_names_resolve(node, errors)

        return self.graph

    @staticmethod
    def _choice_export_name(node) -> "str | None":
        if not isinstance(node, vy_ast.FunctionDef):
            return None
        has_choice = False
        override = None
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, vy_ast.Call) else decorator
            if not isinstance(target, vy_ast.Name):
                continue
            if target.id == "choice":
                has_choice = True
            elif (
                target.id == "DamlName"
                and isinstance(decorator, vy_ast.Call)
                and decorator.args
                and isinstance(decorator.args[0], vy_ast.Str)
            ):
                override = decorator.args[0].value
        if not has_choice:
            return None
        return override or node.name[:1].upper() + node.name[1:]

    @classmethod
    def _exported_names(cls, info: ImportInfo) -> set:
        body = info.ast.body
        names = {n.name for n in body if isinstance(n, _EXPORTED_DECLARATIONS)}
        names |= module_assigns(info.ast).keys()
        for container in body:
            variant = isinstance(container, vy_ast.VariantDef)
            for child in getattr(container, "body", ()):
                if (export_name := cls._choice_export_name(child)) is not None:
                    names.add(export_name)
                elif variant and isinstance(child, vy_ast.FunctionDef):
                    names.add(child.name)
        if cls.is_builtin_stub_path(info.resolved_path):
            names |= {a.name for s in body if isinstance(s, vy_ast.ImportFrom) for a in s.names}
        return names

    @classmethod
    def check_sibling_import_names_resolve(cls, node, errors: ExceptionList) -> None:
        if not isinstance(node, vy_ast.ImportFrom):
            return

        infos = node._metadata.get("import_infos")
        if not infos:
            return

        declared = set().union(*(cls._exported_names(info) for info in infos))

        for alias in node.names:
            if alias.name == "*" or alias.name in declared:
                continue
            errors.append(
                DamlForgeException(
                    f"No name `{alias.name}` in module `{node.module}`",
                    node,
                    code=ErrorCodes.DF1213.value,
                )
            )

    def integrity_sum(self, module: vy_ast.Module) -> str:
        return self._calculate_integrity_sum_r(module)

    def _calculate_integrity_sum_r(self, module: vy_ast.Module) -> str:
        if id(module) in self._integrity_cache:
            return self._integrity_cache[id(module)]

        acc = [module.source_sha256sum]
        for node in module.body:
            if not isinstance(node, (vy_ast.Import, vy_ast.ImportFrom)):
                continue
            for info in node._metadata.get("import_infos", ()):
                acc.append(self._calculate_integrity_sum_r(info.ast))

        ret = sha256sum("".join(acc))
        self._integrity_cache[id(module)] = ret
        return ret
