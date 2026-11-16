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

from pathlib import Path

from forge.ast.parse import parse_to_ast
from forge.codegen import generate_daml
from forge.ir import lower_module
from forge.semantics import analyze_module

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "token_standard" / "token_standard.vy"
)


def _parse_simple():
    source_path = Path(__file__).resolve().parents[2] / "examples" / "basic" / "simple.vy"
    source = source_path.read_text()
    return parse_to_ast(source, module_path="simple.vy", resolved_path=str(source_path))


def test_simple():
    module = _parse_simple()
    analyze_module(module)
    lowered = lower_module(module)
    source, map = generate_daml(lowered)
    assert source is not None
    assert map is not None
    assert "template SimpleTemplate" in source


def test_burn_mint_interface():
    source_path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "splice-api-token-burn-mint-v1"
        / "daml"
        / "Splice"
        / "Api"
        / "Token"
        / "burn_mint_v1.vy"
    )
    source = source_path.read_text()
    assert source is not None
    assert source != ""

    ast_tree = parse_to_ast(source, module_path="burn_mint_v1.vy", resolved_path=str(source_path))
    analyze_module(ast_tree)
    lowered = lower_module(ast_tree)
    source, map = generate_daml(lowered)
    assert source is not None
    assert map is not None
    assert "burnMintFactory_BurnMintImpl" in source
    assert "burnMintFactory_PublicFetchImpl" in source


def test_token_standard_example_declares_expected_containers():
    source = EXAMPLE_PATH.read_text()
    assert source is not None

    module = parse_to_ast(source, module_path="token_standard.vy", resolved_path=str(EXAMPLE_PATH))
    assert module is not None

    graph = analyze_module(module)
    assert graph is not None

    names_by_type = {}
    for node in module.body:
        if not hasattr(node, "name"):
            continue
        names_by_type.setdefault(type(node).__name__, []).append(node.name)

    assert names_by_type["InterfaceDef"] == ["Holding", "TransferInstruction"]
    assert names_by_type["TemplateDef"] == ["SimpleHolding", "PendingTransfer"]

    templates = [node for node in module.body if type(node).__name__ == "TemplateDef"]
    bases_by_name = {t.name: [b.id for b in t.bases] for t in templates}
    assert bases_by_name == {
        "SimpleHolding": ["Holding"],
        "PendingTransfer": ["TransferInstruction"],
    }
