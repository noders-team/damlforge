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

import pytest

from forge.ast.parse import parse_to_ast
from forge.codegen.emit import generate_daml
from forge.exceptions import CompilerPanic, DamlForgeException, SyntaxException
from forge.ir import lower_module
from forge.semantics import analyze_module


def _compile(source: str) -> tuple[str, dict[str, object]]:
    module = parse_to_ast(source, module_path="fuzz.vy", resolved_path="fuzz.vy")
    analyze_module(module)
    ir = lower_module(module)
    return generate_daml(ir, "Fuzz")


def _compile_imported(tmp_path: Path, source: str, filename: str = "main.vy") -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "models.vy").write_text(
        "from enum import Enum, auto\n"
        "from forge.models import Decimal, Party, template\n\n"
        "class Kind(Enum):\n"
        "    CASH = auto()\n\n"
        "@template\n"
        "class Note:\n"
        "    owner: Party\n"
        "    signatory: list[Party] = [owner]\n\n"
        "def double(x: Decimal) -> Decimal:\n"
        "    return x + x\n"
    )
    path = tmp_path / filename
    path.write_text(source)
    module = parse_to_ast(source, module_path=filename, resolved_path=str(path))
    import_graph = analyze_module(module)
    ir = lower_module(module, import_graph=import_graph)
    return generate_daml(ir, "Main")[0]


def test_malformed_type_subscript_is_rejected_instead_of_crashing():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    signatory: 9223372036854775807[Party] = [issuer]\n"
        )

    assert not isinstance(exc_info.value, CompilerPanic)
    assert "No builtin or user-defined type" in str(exc_info.value)


def test_literal_type_annotation_is_rejected_instead_of_crashing():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    issuer: False\n"
        )

    assert not isinstance(exc_info.value, CompilerPanic)
    assert "No builtin or user-defined type" in str(exc_info.value)


def test_nested_attribute_type_annotation_is_rejected_instead_of_crashing():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import ContractId, Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    signatory: list[Party] = [issuer]\n"
            "    def Sum(self, cids: list[ContractId[Iou]]) -> x.y.z:\n"
            "        return verify(cids, self.issuer)\n"
        )

    assert not isinstance(exc_info.value, CompilerPanic)
    assert "No builtin or user-defined type" in str(exc_info.value)


def test_invalid_local_generic_type_is_rejected_before_lowering():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import ContractId, Decimal, Party, choice, template\n"
            "@template\n"
            "class Iou:\n"
            "    owner: Party\n"
            "    signatory: list[Party] = [issuer]\n"
            "    @choice(controller=owner, consuming=False)\n"
            "    def Many(self, owners: list[Party]) -> list[ContractId[Iou]]:\n"
            "        cids: list[ContractId[1]] = []\n"
        )

    assert not isinstance(exc_info.value, CompilerPanic)
    assert "No builtin or user-defined type" in str(exc_info.value)


def test_bad_indentation_is_reported_as_syntax_error():
    with pytest.raises(SyntaxException):
        parse_to_ast("            total = total + x\n    issuer: Party\n")


def test_loop_accumulator_with_early_return_is_reported_as_unsupported():
    with pytest.raises(DamlForgeException, match="loop-carried accumulator"):
        _compile(
            "from forge.models import ContractId, Party, choice, template\n"
            "@template\n"
            "class Iou:\n"
            "    owner: Party\n"
            "    signatory: list[Party] = [issuer]\n"
            "    @choice(controller=owner, consuming=False)\n"
            "    def Count(self, cids: list[ContractId[Iou]]) -> int:\n"
            "        n = 0\n"
            "        for c in cids:\n"
            "            if c == c:\n"
            "                return n\n"
            "            n = n + 1\n"
        )


def test_import_inside_function_is_rejected_before_lowering():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import ContractId, Party, choice, template\n"
            "@template\n"
            "class Iou:\n"
            "    owner: Party\n"
            "    signatory: list[Party] = [issuer]\n"
            "    @choice(controller=owner, consuming=False)\n"
            "    def Many(self, owners: list[Party]) -> list[ContractId[Iou]]:\n"
            "        import os\n"
        )

    assert not isinstance(exc_info.value, CompilerPanic)
    assert "only supported at module scope" in str(exc_info.value)


def test_continue_is_rejected_before_lowering():
    with pytest.raises(DamlForgeException, match="`continue` is not supported"):
        _compile(
            "from forge.models import Decimal, Party, template\n"
            ""
            "def verify(cids: list[Party], p: Party) -> Decimal:\n"
            "    continue\n"
            "@template\n"
            "class Iou:\n"
            "    signatory: list[Party] = [issuer]\n"
        )


def test_raise_is_rejected_before_lowering():
    with pytest.raises(DamlForgeException, match="`raise` is not supported"):
        _compile(
            "from forge.models import ContractId, Decimal, Party, choice, template\n"
            "@template\n"
            "class Iou:\n"
            "    owner: Party\n"
            "    signatory: list[Party] = [issuer]\n"
            "    @choice(controller=owner, consuming=False)\n"
            "    def Many(self, owners: list[Party]) -> list[ContractId[Iou]]:\n"
            "        raise\n"
        )


def test_nested_function_is_rejected_before_lowering():
    with pytest.raises(DamlForgeException, match="Nested function definitions"):
        _compile(
            "from forge.models import Decimal, Party, template\n"
            ""
            "def verify(cids: list[Party], p: Party) -> Decimal:\n"
            "    def Count(self, cids: list[Party]) -> int:\n"
            "        return 0\n"
            "@template\n"
            "class Iou:\n"
            "    signatory: list[Party] = [issuer]\n"
        )


def test_non_name_template_field_target_is_rejected_without_attribute_error():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    signatory: list[Party] = [issuer]\n"
            "    tuple[int, str]: Party\n"
        )

    assert not isinstance(exc_info.value, AttributeError)


def test_non_name_struct_field_target_is_rejected_without_attribute_error():
    with pytest.raises(Exception) as exc_info:
        _compile(
            "from forge.models import Party, struct\n"
            "@struct\n"
            "class Lot:\n"
            "    tuple[int, str]: Party\n"
        )

    assert not isinstance(exc_info.value, AttributeError)


def test_missing_annotation_only_module_is_reported_as_damlforge_error():
    with pytest.raises(DamlForgeException, match="No module named `forge.Iou`"):
        _compile(
            "from forge.Iou import Decimal, Party, choice, template\n"
            "@template\n"
            "class Iou:\n"
            "    issuer: Party\n"
        )


def test_relative_import_is_reported_as_damlforge_error():
    with pytest.raises(DamlForgeException, match="Relative imports are not supported"):
        _compile(
            "from . import models\n"
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    issuer: Party\n"
        )


def test_missing_annotation_only_import_is_reported_as_damlforge_error():
    with pytest.raises(DamlForgeException, match="No module named `forge.DA.Map`"):
        _compile(
            "import forge.DA.Map as M\n"
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    issuer: Party\n"
        )


def test_lowercase_module_alias_is_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="Module alias `m` must start with an uppercase"):
        _compile(
            "import DA.Map as m\n"
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    issuer: Party\n"
            "    signatory: list[Party] = [issuer]\n"
        )


def test_missing_builtin_stub_is_reported_before_daml_generation():
    with pytest.raises(DamlForgeException, match="No module named `DA.int`"):
        _compile("import DA.int as M\n")


def test_malformed_for_syntax_is_reported_as_syntax_error():
    with pytest.raises(SyntaxException, match="Invalid for-loop syntax"):
        parse_to_ast("    for row for rows:\n        for x in row:\n")


def test_unsupported_bitwise_operator_is_rejected_before_lowering():
    with pytest.raises(DamlForgeException, match="operator `&` is not supported"):
        _compile(
            "from forge.models import Decimal\n"
            "def total_of(total: Decimal) -> Decimal:\n"
            "    return total & total\n"
        )


def test_unsupported_unary_operator_is_rejected_before_lowering():
    with pytest.raises(DamlForgeException, match="operator `~` is not supported"):
        _compile(
            "from forge.models import ContractId, Party, choice, template\n"
            "@template\n"
            "class Iou:\n"
            "    owner: Party\n"
            "    signatory: list[Party] = [owner]\n"
            "    @choice(controller=owner, consuming=False)\n"
            "    def Many(self, owners: list[Party]) -> list[ContractId[Iou]]:\n"
            "        return ~owners\n"
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from forge.models import GenMap, Party\n"
            "\n"
            "def mark(votes: GenMap[Party, bool], voter: Party) -> GenMap[Party, bool]:\n"
            "    votes[voter] = True\n"
            "    return votes\n",
            "Map.insert voter True votes",
        ),
        (
            "from forge.models import GenMap, Party\n"
            "\n"
            "def has_voted(votes: GenMap[Party, bool], voter: Party) -> bool:\n"
            "    return voter in votes\n",
            "Map.member voter votes",
        ),
        (
            "from forge.models import GenMap, Party\n"
            "\n"
            "def is_new(votes: GenMap[Party, bool], voter: Party) -> bool:\n"
            "    return voter not in votes\n",
            "not (Map.member voter votes)",
        ),
        (
            "def add_fee(a: int) -> int:\n" "    FEE = 10\n" "    return a + FEE\n",
            "a + _damlforge_fee",
        ),
    ],
    ids=("map-assignment", "map-membership", "map-nonmembership", "uppercase-local"),
)
def test_reported_lowerings_are_valid_daml(source: str, expected: str) -> None:
    daml = _compile(source)[0]
    assert expected in daml


def test_enum_member_cannot_collide_with_template_name() -> None:
    with pytest.raises(DamlForgeException, match="conflicts with template `CASH`"):
        _compile(
            "from enum import Enum, auto\n"
            "from forge.models import Party, template\n\n"
            "class Kind(Enum):\n"
            "    CASH = auto()\n\n"
            "@template\n"
            "class CASH:\n"
            "    owner: Party\n"
            "    signatory: list[Party] = [owner]\n"
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import models as M\n\n" "def cash() -> M.Kind:\n" "    return M.Kind.CASH\n",
            ("import qualified Models as M", "M.CASH"),
        ),
        (
            "from forge.models import ContractId\n"
            "import models\n\n"
            "def same(c: ContractId[models.Note]) -> ContractId[models.Note]:\n"
            "    return c\n",
            ("import Models", "ContractId Models.Note"),
        ),
        (
            "from forge.models import Decimal\n"
            "import models\n\n"
            "def twice(x: Decimal) -> Decimal:\n"
            "    return models.double(x)\n",
            ("import Models", "Models.double x"),
        ),
    ],
    ids=("qualified-enum", "plain-type", "plain-call"),
)
def test_module_imports_use_daml_qualifiers(
    tmp_path: Path, source: str, expected: tuple[str, ...]
) -> None:
    daml = _compile_imported(tmp_path, source)
    for fragment in expected:
        assert fragment in daml


def test_uppercase_member_on_expression_is_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="Uppercase member `CASH`"):
        _compile("from forge.models import Party\n" "def f() -> str:\n" '    return "".CASH\n')


def test_qualified_access_on_primitive_type_is_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="`Party` is a type name, not a value"):
        _compile("from forge.models import Party\n" "def f() -> str:\n" "    return Party.amount\n")


def test_qualified_method_call_on_contract_id_type_is_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="`ContractId` is a type name, not a value"):
        _compile(
            "from forge.models import ContractId\n"
            "def f() -> int:\n"
            "    return ContractId.now()\n"
        )


def test_uppercase_method_on_expression_is_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="Uppercase member `Note`"):
        _compile("def f() -> int:\n" "    return [].Note()\n")


def test_ledger_effect_in_template_field_value_is_rejected():
    with pytest.raises(Exception, match="Ledger effect `fetch` cannot be used"):
        _compile(
            "from forge.models import ContractId, Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    owner: Party\n"
            "    source: ContractId[Iou]\n"
            "    signatory: list[Party] = [fetch(source)]\n"
        )


def test_enum_member_is_wrapped_when_optional_value_is_expected():
    daml, _ = _compile(
        "from enum import Enum, auto\n"
        "from forge.models import Optional\n"
        "class E(Enum):\n"
        "    A = auto()\n"
        "    B = auto()\n"
        "def accept(value: Optional[E]) -> Optional[E]:\n"
        "    return value\n"
        "def direct() -> Optional[E]:\n"
        "    return E.A\n"
        "def f() -> Optional[E]:\n"
        "    value: Optional[E] = E.A\n"
        "    return accept(E.B)\n"
    )

    assert "Some A" in daml
    assert "Some B" in daml


def test_enum_member_list_literal_uses_list_indexing():
    daml, _ = _compile(
        "from enum import Enum, auto\n"
        "class E(Enum):\n"
        "    A = auto()\n"
        "    B = auto()\n"
        "def f() -> E:\n"
        "    return [E.A, E.B][0]\n"
    )

    assert "!!" in daml
    assert "._1" not in daml


def test_bare_undefined_name_is_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="`missing` is not defined"):
        _compile("def f() -> int:\n" "    return missing\n")


def test_duplicate_top_level_functions_are_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="Function `f` is defined more than once"):
        _compile("def f() -> int:\n" "    return 1\n" "def f() -> int:\n" "    return 2\n")


def test_duplicate_struct_fields_are_rejected_before_daml_generation():
    with pytest.raises(DamlForgeException, match="declares field `kind` more than once"):
        _compile("struct Lot:\n" "    kind: str\n" "    kind: str\n")


def test_comprehension_like_for_syntax_is_reported_as_syntax_error():
    with pytest.raises(SyntaxException, match="Invalid for-loop syntax"):
        parse_to_ast(
            "from forge.models import Party, template\n"
            "@template\n"
            "class Iou:\n"
            "    if i.amount > [x for x in xs](1):\n"
            "        n = n + 1\n"
        )


def test_annotation_only_local_does_not_reach_codegen_as_none():
    daml, _ = _compile(
        "from forge.models import Decimal, Party, template\n"
        ""
        "def verify(cids: list[Party], p: Party) -> Decimal:\n"
        "    issuer: Party\n"
    )

    assert "let issuer =" not in daml
