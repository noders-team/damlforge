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

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from forge.ast import nodes as vy_ast
from forge.builtin_stubs import BUILTINS_DIR, StubMetadata

_SCOPE: ContextVar = ContextVar("field_scope", default=None)
_CONTAINERS = (vy_ast.StructDef, vy_ast.TemplateDef, vy_ast.VariantDef, vy_ast.InterfaceDef)


class FieldScope:
    def __init__(
        self, renames: dict, user_fields: set, user_types: set, user_renames: Optional[dict] = None
    ):
        self.renames = {k: dict(v) for k, v in renames.items() if k not in user_types}
        for owner, fields in (user_renames or {}).items():
            self.renames.setdefault(owner, {}).update(fields)
        self.user_fields = user_fields
        targets: dict = {}
        for fields in self.renames.values():
            for name, daml_name in fields.items():
                targets.setdefault(name, set()).add(daml_name)
        self.unique = {name: d.pop() for name, d in targets.items() if len(d) == 1}

    def attribute(self, type_name: Optional[str], name: str) -> str:
        if type_name is not None:
            return self.keyword(type_name, name)
        return name if name in self.user_fields else self.unique.get(name, name)

    def keyword(self, owner: Optional[str], name: str) -> str:
        if owner not in self.renames and owner is not None and "." in owner:
            owner = owner.rsplit(".", 1)[1]
        return self.renames.get(owner, {}).get(name, name)


_EMPTY = FieldScope({}, set(), set())


def _declarations(module: vy_ast.Module, types: set, fields: set) -> None:
    for node in (n for n in module.body if isinstance(n, _CONTAINERS)):
        if not isinstance(node, vy_ast.InterfaceDef):
            types.add(node.name)
        for member in node.body:
            if isinstance(member, vy_ast.AnnAssign) and isinstance(member.target, vy_ast.Name):
                fields.add(member.target.id)
            elif isinstance(member, vy_ast.FunctionDef):
                types.add(member.name)
                fields.update(arg.arg for arg in member.args.args if arg.arg != "self")


@contextmanager
def field_scope(module: vy_ast.Module):
    from forge.builtins import collect_daml_builtins

    types: set = set()
    fields: set = set()
    modules = [module] + [
        info.ast
        for stmt in module.body
        for info in stmt._metadata.get("import_infos", ())
        if BUILTINS_DIR not in Path(info.resolved_path).resolve().parents
    ]
    user_renames: dict = {}
    for item in modules:
        _declarations(item, types, fields)
        metadata = StubMetadata.of(item)
        user_renames.update(metadata.type_field_renames)
    for renamed in user_renames.values():
        fields.difference_update(renamed)
    builtin_renames = collect_daml_builtins().type_field_renames
    token = _SCOPE.set(FieldScope(builtin_renames, fields, types, user_renames))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current() -> FieldScope:
    return _SCOPE.get() or _EMPTY
