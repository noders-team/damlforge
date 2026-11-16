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

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

from forge.models.decorators import Consuming


@dataclass(frozen=True)
class SourceSpan:
    lineno: Optional[int]
    col_offset: Optional[int]
    end_lineno: Optional[int]
    end_col_offset: Optional[int]


@dataclass(frozen=True)
class TypeRef:
    name: str
    args: tuple["TypeRef", ...] = ()


@dataclass(frozen=True)
class Field:
    name: str
    type: Optional[TypeRef]


# ---- expressions ----


@dataclass(frozen=True)
class IRName:
    id: str
    type: Optional[TypeRef] = None


@dataclass(frozen=True)
class IRAttribute:
    value: "IRExpr"
    attr: str


@dataclass(frozen=True)
class IRSubscript:
    value: "IRExpr"
    index: "IRExpr"
    is_list: bool = False
    tuple_arity: int | None = None


@dataclass(frozen=True)
class IRCall:
    func: "IRExpr"
    args: tuple["IRExpr", ...] = ()
    keywords: tuple[tuple[str, "IRExpr"], ...] = ()


@dataclass(frozen=True)
class IRConstant:
    value: Any
    type: Optional[TypeRef] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRList:
    elements: tuple["IRExpr", ...]


@dataclass(frozen=True)
class IRTuple:
    elements: tuple["IRExpr", ...]


@dataclass(frozen=True)
class IRDict:
    keys: tuple["IRExpr", ...]
    values: tuple["IRExpr", ...]
    kind: Optional[str] = None


@dataclass(frozen=True)
class IRBinOp:
    op: str
    left: "IRExpr"
    right: "IRExpr"


@dataclass(frozen=True)
class IRBoolOp:
    op: str
    values: tuple["IRExpr", ...]


@dataclass(frozen=True)
class IRUnaryOp:
    op: str
    operand: "IRExpr"


@dataclass(frozen=True)
class IRCompare:
    left: "IRExpr"
    op: str
    right: "IRExpr"


@dataclass(frozen=True)
class IRRecordUpdate:
    """A Daml/Haskell record update: `base { f1 = v1, f2 = v2 }`. Daml records are
    immutable - this is how `self.field = value` inside a choice body actually
    compiles: not a mutation, but a new record value built from the old one."""

    base: "IRExpr"
    updates: tuple[tuple[str, "IRExpr"], ...]


@dataclass(frozen=True)
class IRIfExp:
    test: "IRExpr"
    body: "IRExpr"
    orelse: "IRExpr"


IRExpr = Union[
    IRName,
    IRAttribute,
    IRSubscript,
    IRCall,
    IRConstant,
    IRList,
    IRTuple,
    IRDict,
    IRBinOp,
    IRBoolOp,
    IRUnaryOp,
    IRCompare,
    IRRecordUpdate,
    IRIfExp,
]


# ---- statements ----


@dataclass(frozen=True)
class IRAssert:
    test: IRExpr
    msg: Optional[IRExpr] = None
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRAssign:
    target: IRExpr
    value: IRExpr
    type: Optional[TypeRef] = None
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRIf:
    test: IRExpr
    body: tuple["IRStmt", ...]
    orelse: tuple["IRStmt", ...] = ()
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRFor:
    target: str
    target_type: Optional[TypeRef]
    iter: IRExpr
    body: tuple["IRStmt", ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRFold:
    """A loop normalized to a pure or action-aware Daml fold."""

    target: str
    target_type: Optional[TypeRef]
    iter: IRExpr
    body: tuple["IRStmt", ...]
    accumulators: tuple[str, ...]
    captured: tuple[str, ...]
    in_update_block: bool
    # Names introduced by the Python loop whose value is read after the loop.
    # They are carried as Maybe values because Python leaves a loop target/local
    # unbound when the iterable is empty.
    post_loop: tuple[str, ...] = ()
    post_loop_initially_bound: tuple[bool, ...] = ()
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRAssertLoop:
    """A loop containing one assertion, rendered with `all`."""

    target: str
    target_type: Optional[TypeRef]
    iter: IRExpr
    assertion: IRAssert
    in_update_block: bool
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IREarlyReturnLoop:
    """A loop normalized to a recursive helper that can return early."""

    target: str
    target_type: Optional[TypeRef]
    iter: IRExpr
    body: tuple["IRStmt", ...]
    captured: tuple[str, ...]
    in_update_block: bool
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRPureAssertionLoop:
    """A loop of pure assertions normalized to a recursive validation helper."""

    target: str
    target_type: Optional[TypeRef]
    iter: IRExpr
    body: tuple["IRStmt", ...]
    captured: tuple[str, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRReturn:
    value: Optional[IRExpr] = None
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRPass:
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRExprStmt:
    value: IRExpr
    src: Optional[SourceSpan] = field(default=None, compare=False)


IRStmt = Union[
    IRAssert,
    IRAssign,
    IRIf,
    IRFor,
    IRFold,
    IRAssertLoop,
    IREarlyReturnLoop,
    IRPureAssertionLoop,
    IRReturn,
    IRPass,
    IRExprStmt,
]


# ---- containers ----


@dataclass(frozen=True)
class IRFunction:
    name: str
    args: tuple[Field, ...]
    return_type: Optional[TypeRef]
    decorators: tuple[str, ...]
    body: tuple[IRStmt, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)
    effectful: bool = False


@dataclass(frozen=True)
class IRChoice:
    name: str
    controller: Optional[IRExpr]
    consuming: Optional[Consuming]
    observer: Optional[IRExpr]
    args: tuple[Field, ...]
    return_type: Optional[TypeRef]
    body: tuple[IRStmt, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRStruct:
    name: str
    fields: tuple[Field, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class IREnum:
    name: str
    values: tuple[str, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRVariantConstructor:
    name: str
    fields: tuple[Field, ...]
    record: bool = False


@dataclass(frozen=True)
class IRVariant:
    name: str
    constructors: tuple[IRVariantConstructor, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class IRTypeAlias:
    name: str
    type: TypeRef
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class IRInterface:
    name: str
    view: Optional[TypeRef]
    requires: tuple[str, ...]
    keywords: tuple[tuple[str, IRExpr], ...]
    choices: tuple[IRChoice, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRInterfaceInstance:
    interface: str
    view: Optional[IRFunction]
    choices: tuple[IRChoice, ...]
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRTemplate:
    name: str
    fields: tuple[Field, ...]
    signatory: Optional[IRExpr]
    observer: Optional[IRExpr]
    ensure: Optional[IRExpr]
    init: Optional[IRFunction]
    choices: tuple[IRChoice, ...]
    interface_instances: tuple[IRInterfaceInstance, ...] = ()
    checked_fetch: Optional[IRFunction] = None
    key: Optional[IRExpr] = None
    key_type: Optional[TypeRef] = None
    maintainer: Optional[IRExpr] = None
    src: Optional[SourceSpan] = field(default=None, compare=False)


@dataclass(frozen=True)
class IRImport:
    module: str
    names: Optional[tuple[str, ...]] = None
    constructor_names: frozenset = frozenset()
    alias: Optional[str] = None
    qualified: bool = True


@dataclass(frozen=True)
class IRModule:
    structs: tuple[IRStruct, ...] = ()
    enums: tuple[IREnum, ...] = ()
    variants: tuple[IRVariant, ...] = ()
    interfaces: tuple[IRInterface, ...] = ()
    templates: tuple[IRTemplate, ...] = ()
    functions: tuple[IRFunction, ...] = ()
    imports: tuple[IRImport, ...] = ()
    required_modules: tuple[str, ...] = ()
    effect_functions: tuple[str, ...] = ()
    aliases: tuple[IRTypeAlias, ...] = ()
    stub_scope: Optional[frozenset] = None
