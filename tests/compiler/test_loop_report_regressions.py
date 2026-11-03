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

import pytest

from forge.ast.parse import parse_to_ast
from forge.codegen import generate_daml
from forge.exceptions import DamlForgeException
from forge.ir import lower_module
from forge.semantics import analyze_module


def _compile(source: str) -> str:
    module = parse_to_ast(source, module_path="loop_report.vy", resolved_path="loop_report.vy")
    analyze_module(module)
    ir = lower_module(module)
    return generate_daml(ir, module_name="LoopReport")[0]


def test_nested_fold_uses_current_captured_binding_and_tuple_assignment_is_simultaneous():
    daml = _compile("""
from forge.models import Party

def f(xs: list[int]) -> int:
    a: int = 0
    b: int = 0
    for x: int in xs:
        a = a + x
        for y: int in xs:
            b = b + a * y
    return a * 1000 + b

def swap(xs: list[int]) -> int:
    a: int = 0
    b: int = 1
    for x: int in xs:
        a, b = b, a + x
    return a * 1000 + b
""")
    assert "_damlforge_a1 * y" in daml
    assert "_damlforge_tuple" in daml
    assert "let (a, b) = (b, a)" not in daml


def test_struct_and_self_list_mutation_become_fold_state():
    daml = _compile("""
from forge.models import Party, choice, template

struct Acc:
    s: int

def sum_fields(xs: list[int]) -> int:
    acc: Acc = Acc(s=0)
    for x: int in xs:
        acc.s = acc.s + x
    return acc.s

@template
class Box:
    owner: Party
    xs: list[int]
    signatory: list[Party] = [owner]

    @choice(controller=owner, consuming=False)
    def Add(self) -> int:
        for x: int in [1, 2]:
            self.xs.append(x)
        return len(self.xs)
""")
    assert "foldl" in daml
    assert "acc { s =" in daml
    assert "self { xs = self.xs <>" in daml
    assert ".append" not in daml


def test_effectful_lazy_operands_are_lowered_inside_action_branches():
    daml = _compile("""
from forge.builtins.daml_lang import fetch
from forge.models import ContractId, Party, choice, template

@template
class Item:
    owner: Party
    value: int
    signatory: list[Party] = [owner]

@template
class Box:
    owner: Party
    signatory: list[Party] = [owner]

    @choice(controller=owner, consuming=False)
    def Read(self, cids: list[ContractId[Item]]) -> int:
        total: int = 0
        for c: ContractId[Item] in cids:
            total = total + (fetch(c).value if total >= 0 else 0)
        return total
""")
    assert "foldlA" in daml
    assert "<- fetch c" in daml
    assert "if total >= 0 then (fetch c).value" not in daml


def test_effectful_if_expression_branches_are_lowered_as_updates():
    daml = _compile("""
def check(v: int) -> int:
    assert v > 0, "negative"
    return v

def f(condition: bool, value: int) -> int:
    return 5 if condition else check(value)
""")
    assert "if condition" in daml
    assert "pure (if condition then 5 else check value)" not in daml


def test_effectful_boolean_operands_preserve_short_circuiting():
    daml = _compile("""
def check(v: int) -> int:
    assert v > 0, "negative"
    return v

def f(condition: bool, value: int) -> bool:
    return condition and check(value) > 0

def g(condition: bool, value: int) -> bool:
    return condition or check(value) > 0
""")
    assert "if condition" in daml
    assert "pure (condition && (check value > 0))" not in daml
    assert "pure (condition || (check value > 0))" not in daml


def test_effectful_helper_in_range_iterable_is_bound_before_loop():
    daml = _compile("""
def check(v: int) -> int:
    assert v != 4, "four"
    return v * 2

def f(xs: list[int]) -> int:
    t: int = 0
    for i: int in range(check(len(xs))):
        t = t + i
    return t
""")
    assert "<- check (length xs)" in daml
    assert "foldl" in daml


def test_effectful_helper_in_template_ensure_is_rejected_as_pure_context():
    with pytest.raises(
        DamlForgeException,
        match=r"Ledger effect `check` cannot be used in the pure value of `Box.ensure`",
    ):
        _compile("""
from forge.models import Party, template

def check(v: int) -> bool:
    assert v != 4, "four"
    return True

@template
class Box:
    owner: Party
    ensure: bool = check(1)
    signatory: list[Party] = [owner]
""")


def test_loop_scoped_values_are_carried_as_optional_fold_state():
    daml = _compile("""
def f(xs: list[int]) -> int:
    total: int = 0
    for x: int in xs:
        total = total + x
    return total + x
""")
    assert "Some x" in daml
    assert "fromSome" in daml
    assert "import DA.Optional (fromSome)" in daml


def test_loop_scoped_record_access_unwraps_before_attribute_selection() -> None:
    daml = _compile(
        "from forge.builtins.daml_lang import fetch\n"
        "from forge.models import ContractId, Decimal, Party, choice, template\n\n"
        "struct Lot:\n"
        "    amount: Decimal\n\n"
        "@template\n"
        "class Note:\n"
        "    owner: Party\n"
        "    lot: Lot\n"
        "    signatory: list[Party] = [owner]\n\n"
        "    @choice(controller=owner, consuming=False)\n"
        "    def Size(self, cid: ContractId[Note]) -> Decimal:\n"
        "        for _k in range(0, 2):\n"
        "            note = fetch(cid)\n"
        "        return note.lot.amount\n"
    )
    assert "(fromSome _damlforge_" in daml
    assert ").lot).amount" in daml


def test_reported_expression_lowerings_are_buildable_shapes():
    daml = _compile("""
from forge.models import Decimal, TextMap

def f(i: int, x: int, m: TextMap[int]) -> Decimal:
    return Decimal(0.0000005) if x not in [[5]][i] else Decimal(1)

def g(xs: list[int], m: TextMap[int]) -> int:
    total: int = 0
    for x: int in range(5, 0, -2):
        total = total + x
    return total + len(m.keys())
""")
    assert "0.0000005 : Decimal" in daml
    assert "notElem" in daml
    assert "TextMap.toList" in daml
    assert "5, 5 + (-2)" in daml


def test_duplicate_enum_constructors_are_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="constructors must be unique"):
        _compile("""
from enum import Enum, auto

class First(Enum):
    SAME = auto()

class Second(Enum):
    SAME = auto()
""")
