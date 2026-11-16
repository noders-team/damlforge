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
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from typing import Optional

from forge.ast import nodes as vy_ast
from forge.ast.utils import type_aliases, type_var_names

BUILTINS_DIR = Path(__file__).resolve().parent / "builtins"
STDLIB_ROOTS = frozenset({"DA", "GHC"})

_CLAUSES = ("signatory", "observer", "key", "maintainer", "ensure")
_TIME_TYPES = {"datetime": "Timestamp", "date": "Date", "timedelta": "RelTime"}
_NAME_KEYED = {
    "function_renames",
    "ledger_effect_fns",
    "case_accessors",
    "function_return_types",
    "constructor_of",
}
_SCOPE: ContextVar = ContextVar("stub_scope", default=None)


def _decorator_name(dec) -> Optional[str]:
    return getattr(getattr(dec, "func", dec), "id", None)


def _decorator(node, name: str):
    decorators = getattr(node, "decorator_list", None) or ()
    return next((dec for dec in decorators if _decorator_name(dec) == name), None)


def _restrict(source, names: frozenset):
    if isinstance(source, dict):
        return {k: v for k, v in source.items() if k in names}
    return source & names


def _str_arg(dec) -> Optional[str]:
    args = getattr(dec, "args", None)
    return args[0].value if args and isinstance(args[0], vy_ast.Str) else None


def _bool_keyword(dec, name: str) -> bool:
    return any(
        keyword.arg == name
        and isinstance(keyword.value, vy_ast.NameConstant)
        and keyword.value.value is True
        for keyword in getattr(dec, "keywords", ())
    )


def _annotated_name(annotation) -> Optional[str]:
    if getattr(getattr(annotation, "value", None), "id", None) != "Annotated":
        return None
    metas = getattr(annotation.slice, "elements", ())[1:]
    return next((_str_arg(m) for m in metas if _decorator_name(m) == "DamlName"), None)


def _type(annotation):
    from forge.ir.lower import lower_type
    from forge.ir.nodes import TypeRef

    def normalize(ref):
        return TypeRef(_TIME_TYPES.get(ref.name, ref.name), tuple(map(normalize, ref.args)))

    return normalize(lower_type(annotation))


def _mentions(ref, names: set) -> bool:
    return ref.name in names or any(_mentions(a, names) for a in ref.args)


@dataclass
class StubMetadata:
    names: set = field(default_factory=set)
    imports: list = field(default_factory=list)
    function_renames: dict = field(default_factory=dict)
    ledger_effect_fns: set = field(default_factory=set)
    case_accessors: dict = field(default_factory=dict)
    function_return_types: dict = field(default_factory=dict)
    constructor_of: dict = field(default_factory=dict)
    constructor_types: set = field(default_factory=set)
    no_derive_types: set = field(default_factory=set)
    field_types: dict = field(default_factory=dict)
    type_field_renames: dict = field(default_factory=dict)
    type_aliases: dict = field(default_factory=dict)

    def record(self, owner: str, pairs) -> None:
        for name, annotation in pairs:
            if (daml_name := _annotated_name(annotation)) is not None:
                self.type_field_renames.setdefault(owner, {})[name] = daml_name
        self.field_types[owner] = {n: _type(a) for n, a in pairs if a is not None}
        self.constructor_types.add(owner)

    def choices(self, container) -> None:
        for fn in container.body:
            if isinstance(fn, vy_ast.FunctionDef) and _decorator(fn, "choice") is not None:
                name = _str_arg(_decorator(fn, "DamlName")) or fn.name
                self.record(name, [(a.arg, a.annotation) for a in fn.args.args if a.arg != "self"])

    def function(self, fn: vy_ast.FunctionDef, tvars: set) -> None:
        self.names.add(fn.name)
        if (daml_name := _str_arg(_decorator(fn, "DamlName"))) is not None:
            self.function_renames[fn.name] = daml_name
        daml_name_dec = _decorator(fn, "DamlName")
        if daml_name_dec is not None and _bool_keyword(daml_name_dec, "is_ledger_effect"):
            self.ledger_effect_fns.add(fn.name)
        if (accessor := _decorator(fn, "DamlCaseAccessor")) is not None:
            arity = next((k.value.value for k in accessor.keywords if k.arg == "arity"), 1)
            ctor = _str_arg(accessor)
            self.case_accessors[fn.name] = ctor if arity == 1 else (ctor, arity)
        if fn.returns is not None and not _mentions(returns := _type(fn.returns), tvars):
            self.function_return_types[fn.name] = returns

    def variant(self, node) -> None:
        self.constructor_types.add(node.name)
        for ctor in (c for c in node.body if isinstance(c, vy_ast.FunctionDef)):
            self.names.add(ctor.name)
            self.constructor_of[ctor.name] = node.name
            for arg in ctor.args.args:
                if (daml_name := _annotated_name(arg.annotation)) is not None:
                    self.type_field_renames.setdefault(ctor.name, {})[arg.arg] = daml_name

    @classmethod
    def of(cls, module: vy_ast.Module) -> "StubMetadata":
        meta = cls(imports=list(_stub_imports(module)))
        tvars = type_var_names(module)
        for node in module.body:
            if _decorator(node, "no_derive") is not None:
                meta.no_derive_types.add(node.name)
            if isinstance(node, (vy_ast.StructDef, vy_ast.TemplateDef)):
                body = [s for s in node.body if isinstance(s, vy_ast.AnnAssign)]
                meta.record(
                    node.name,
                    [(s.target.id, s.annotation) for s in body if s.target.id not in _CLAUSES],
                )
            if isinstance(node, (vy_ast.TemplateDef, vy_ast.InterfaceDef)):
                meta.choices(node)
            elif isinstance(node, vy_ast.VariantDef):
                meta.variant(node)
            elif isinstance(node, vy_ast.FunctionDef):
                meta.function(node, tvars)
        meta.type_aliases = {name: _type(value) for name, value in type_aliases(module).items()}
        return meta

    def merge(self, other: "StubMetadata", names: Optional[frozenset]) -> None:
        for f in fields(self):
            if f.name in ("names", "imports"):
                continue
            source = getattr(other, f.name)
            if f.name in _NAME_KEYED and names is not None:
                source = _restrict(source, names)
            if f.name == "type_field_renames":
                for owner, renames in source.items():
                    self.type_field_renames.setdefault(owner, {}).update(renames)
                continue
            getattr(self, f.name).update(source)


def _root(module: str | None) -> str:
    # ``from . import ...`` has no module name in the Python AST.  Keep stub
    # discovery total here; import analysis will report relative imports as a
    # normal DamlForge diagnostic later.
    return module.split(".", 1)[0] if module else ""


def _stub_imports(module: vy_ast.Module):
    for stmt in module.body:
        if (
            isinstance(stmt, vy_ast.ImportFrom)
            and stmt.module is not None
            and is_stub_root(_root(stmt.module))
        ):
            yield stmt.module, tuple(sorted(alias.name for alias in stmt.names))
        elif isinstance(stmt, vy_ast.Import):
            yield from ((a.name, None) for a in stmt.names if is_stub_root(_root(a.name)))


def scope_of(module: vy_ast.Module) -> frozenset:
    return frozenset(_stub_imports(module))


@contextmanager
def stub_scope(scope: Optional[frozenset]):
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current_scope() -> Optional[frozenset]:
    return _SCOPE.get()


@lru_cache(maxsize=1)
def stub_roots() -> frozenset:
    found = {p.relative_to(BUILTINS_DIR).parts[0].removesuffix(".vy") for p in _stub_files()}
    return frozenset(found | STDLIB_ROOTS)


def vendor_roots() -> frozenset:
    return stub_roots() - STDLIB_ROOTS


def is_stub_root(root: str) -> bool:
    return root in stub_roots()


def _stub_files() -> list:
    return sorted(BUILTINS_DIR.rglob("*.vy"))


@lru_cache(maxsize=1)
def stub_modules() -> dict:
    from forge.ast.parse import parse_to_ast

    modules = {}
    for path in _stub_files():
        rel = path.relative_to(BUILTINS_DIR)
        ast = parse_to_ast(path.read_text(), module_path=str(rel), resolved_path=str(path))
        modules[".".join(rel.with_suffix("").parts)] = StubMetadata.of(ast)
    return modules


def owner_module(module: str, name: str, seen: Optional[set] = None) -> Optional[str]:
    seen = set() if seen is None else seen
    meta = stub_modules().get(module)
    if meta is None or module in seen:
        return None
    seen.add(module)
    if name in meta.names:
        return module
    sources = (src for src, names in meta.imports if names is None or name in names)
    return next(filter(None, (owner_module(src, name, seen) for src in sources)), None)


def _closure(roots) -> list:
    modules = stub_modules()
    reached: set = set()
    pending = [m for m in roots if m in modules]
    while pending:
        if (module := pending.pop()) not in reached:
            reached.add(module)
            pending.extend(src for src, _ in modules[module].imports if src in modules)
    return sorted(reached)


@lru_cache(maxsize=256)
def collect_vy_stubs(scope: Optional[frozenset] = None) -> StubMetadata:
    modules = stub_modules()
    merged = StubMetadata()
    if scope is None:
        for name in (n for n in modules if _root(n) in vendor_roots()):
            merged.merge(modules[name], None)
        return merged
    for name in _closure(module for module, _ in scope):
        merged.merge(modules[name], frozenset())
    for module, names in sorted(scope, key=lambda entry: entry[0]):
        if names is None:
            if module in modules:
                merged.merge(modules[module], None)
            continue
        for item in names:
            if (owner := owner_module(module, item)) is not None:
                merged.merge(modules[owner], frozenset({item}))
    return merged
