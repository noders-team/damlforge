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
from forge.builtin_stubs import StubMetadata
from forge.codegen import generate_daml
from forge.ir import lower_module
from forge.ir.fields import FieldScope
from forge.semantics import analyze_module


def _compile(source: str) -> str:
    module = parse_to_ast(source, module_path="tuples.vy", resolved_path="tuples.vy")
    analyze_module(module)
    return generate_daml(lower_module(module), module_name="Tuples")[0]


@pytest.mark.parametrize(
    "tuple_type, index, expected, forbidden",
    [
        ("tuple[int, int]", 1, "t._2", "case"),
        ("tuple[int, int, int, int, int]", 4, "t._5", "case"),
        ("tuple[int, int, int, int, int, int]", 0, "case t of (tuple_item, _, _, _, _, _)", "t._1"),
        ("tuple[int, int, int, int, int, int]", 5, "case t of (_, _, _, _, _, tuple_item)", "t._6"),
    ],
)
def test_tuple_index_uses_accessor_only_when_daml_defines_it(
    tuple_type, index, expected, forbidden
):
    daml = _compile(f"""
def pick(t: {tuple_type}) -> int:
    return t[{index}]
""")
    assert expected in daml
    assert forbidden not in daml
    assert "!!" not in daml


@pytest.mark.parametrize(
    "owner, field, expected",
    [
        ("TransferFactory_Transfer", "expected_admin", "expectedAdmin"),
        ("TransferInstructionV1.TransferFactory_Transfer", "expected_admin", "expectedAdmin"),
        ("Missing.TransferFactory_Transfer", "expected_admin", "expectedAdmin"),
        ("Unknown.Type", "expected_admin", "expected_admin"),
        (None, "expected_admin", "expected_admin"),
    ],
)
def test_field_rename_resolves_module_qualified_owner(owner, field, expected):
    scope = FieldScope(
        {"TransferFactory_Transfer": {"expected_admin": "expectedAdmin"}}, set(), set()
    )
    assert scope.keyword(owner, field) == expected


def test_stub_merge_keeps_field_renames_of_same_named_choices():
    first = StubMetadata()
    first.type_field_renames["TransferFactory_Transfer"] = {"expected_admin": "expectedAdmin"}
    second = StubMetadata()
    second.type_field_renames["TransferFactory_Transfer"] = {"extra_args": "extraArgs"}
    first.merge(second, None)
    assert first.type_field_renames["TransferFactory_Transfer"] == {
        "expected_admin": "expectedAdmin",
        "extra_args": "extraArgs",
    }


@pytest.mark.parametrize(
    "returned, expected",
    [
        (
            "(result.purchased_traffic, result.sender_change_amulet)",
            "pure (result.purchasedTraffic, result.senderChangeAmulet)",
        ),
        ("(result.purchased_traffic, None)", "pure (result.purchasedTraffic, None)"),
    ],
)
def test_tuple_with_optional_element_from_untyped_exercise_is_not_wrapped_in_some(
    returned, expected
):
    daml = _compile(f"""
from typing import Optional
from forge.models import Party
from forge.builtins.daml_lang import exercise
from Splice.Amulet import Amulet
from Splice.DecentralizedSynchronizer import MemberTraffic
from Splice.AmuletRules import (
    AmuletRules,
    AmuletRules_BuyMemberTraffic,
    TransferContext,
    TransferInput,
)

def buy(
    rules: ContractId[AmuletRules],
    inputs: list[TransferInput],
    context: TransferContext,
    provider: Party,
    dso: Party,
) -> tuple[ContractId[MemberTraffic], Optional[ContractId[Amulet]]]:
    result = exercise(
        rules,
        AmuletRules_BuyMemberTraffic(
            inputs=inputs,
            context=context,
            provider=provider,
            member_id="m",
            synchronizer_id="s",
            migration_id=1,
            traffic_amount=2,
            expected_dso=dso,
        ),
    )
    return {returned}
""")
    assert expected in daml
    assert "Some (" not in daml
