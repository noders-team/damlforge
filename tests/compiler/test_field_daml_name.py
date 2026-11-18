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
from forge.ir import lower_module
from forge.semantics import analyze_module

SOURCE = """
from typing import Annotated
from forge.models import Party, choice, template
from forge.builtins import DamlName


struct Batch:
    num_markers: Annotated[int, DamlName("numMarkers")]
    label: str


@template
class Proxy:
    provider: Party

    signatory: Party = provider

    @DamlName("Proxy_Run")
    @choice(controller=provider, consuming=False, observer=[provider] + collect(batches))
    def proxy_run(
        self,
        batches: list[Batch],
        right_id: Annotated[int, DamlName("rightId")],
    ) -> int:
        total = right_id
        for batch: Batch in batches:
            total = total + batch.num_markers
        return total


def collect(batches: list[Batch]) -> list[Party]:
    return []


def make(count: int) -> Batch:
    return Batch(num_markers=count, label="x")
"""


def _compile(source: str) -> str:
    module = parse_to_ast(source, module_path="renames.vy", resolved_path="renames.vy")
    analyze_module(module)
    return generate_daml(lower_module(module), module_name="Renames")[0]


@pytest.mark.parametrize(
    "expected",
    [
        "    numMarkers : Int",
        "    label : Text",
        "        rightId : Int",
        "batch.numMarkers",
        "let total = rightId",
        "Batch with numMarkers = count",
    ],
)
def test_annotated_daml_name_renames_user_fields_and_choice_parameters(expected):
    assert expected in _compile(SOURCE)


@pytest.mark.parametrize("forbidden", ["num_markers", "right_id"])
def test_annotated_daml_name_leaves_no_python_spelling_behind(forbidden):
    assert forbidden not in _compile(SOURCE)
