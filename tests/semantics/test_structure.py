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
from forge.builtins import known_builtin_function_names
from forge.exceptions import ExceptionList
from forge.semantics import analyze_module
from forge.semantics.analysis.imports import ImportsBuilder
from forge.models.error_codes import ErrorCodes
from forge.ir import lower_module
from forge.codegen import generate_daml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "structure"


def _fixture_path(name: str) -> Path:
    return FIXTURES_DIR / name


def _read_fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _analyze(source):
    module = parse_to_ast(source, module_path="test.vy", resolved_path="test.vy")
    analyze_module(module)
    lowered = lower_module(module)
    source, map = generate_daml(lowered)
    assert source is not None
    assert map is not None


@pytest.mark.parametrize(
    "source_path",
    [
        _fixture_path("valid/valid_template.vy"),
        _fixture_path("valid/ledger_effect_marked_is_allowed.vy"),
        _fixture_path("valid/choice_body_ledger_effect_is_allowed.vy"),
        _fixture_path("valid/view_in_interface_is_allowed.vy"),
        _fixture_path("valid/choice_preconsuming_on_template_is_allowed.vy"),
        _fixture_path("valid/choice_postconsuming_on_template_is_allowed.vy"),
        _fixture_path("valid/choice_string_consuming_on_template_is_allowed.vy"),
        _fixture_path("valid/choice_with_observer_is_allowed.vy"),
        _fixture_path("valid/template_key_with_maintainer_is_allowed.vy"),
        _fixture_path("valid/contract_key_builtins_are_allowed.vy"),
        _fixture_path("valid/template_without_key_or_maintainer_is_allowed.vy"),
        _fixture_path("valid/template_ensure_compare_is_allowed.vy"),
        _fixture_path("valid/template_ensure_boolop_is_allowed.vy"),
        _fixture_path("valid/template_ensure_call_is_allowed.vy"),
        _fixture_path("valid/template_without_ensure_is_allowed.vy"),
        _fixture_path("valid/enum_value_access_is_allowed.vy"),
        _fixture_path("valid/self_reference_via_another_template_name_is_allowed.vy"),
        _fixture_path("valid/local_variable_used_as_attribute_base_is_allowed.vy"),
        _fixture_path("valid/known_builtin_and_struct_constructor_call_targets_are_allowed.vy"),
        _fixture_path("valid/daml_name_overrides_choice_name_is_allowed.vy"),
        _fixture_path("valid/genmap_field_is_allowed.vy"),
        _fixture_path("valid/textmap_field_is_allowed.vy"),
        _fixture_path("valid/set_field_is_allowed.vy"),
        _fixture_path("valid/frozenset_field_is_allowed.vy"),
        _fixture_path("valid/valid_template_via_decorator.vy"),
        _fixture_path("valid/compatible_function_call_argument_types_are_allowed.vy"),
        _fixture_path("valid/template_with_ellipsis.vy"),
        _fixture_path("valid/imported_ledger_builtins_are_allowed.vy"),
        _fixture_path("valid/interface_choice_controller_uses_view_is_allowed.vy"),
    ],
    ids=lambda path: path.stem.upper(),
)
def test_valid(source_path):
    _analyze(_read_fixture(source_path))


@pytest.mark.parametrize(
    "source_path,expected_code",
    [
        (_fixture_path("invalid/template_missing_signatory.vy"), ErrorCodes.DF1201.value),
        (_fixture_path("invalid/template_empty_signatory.vy"), ErrorCodes.DF1201.value),
        (_fixture_path("invalid/template_empty_observer.vy"), ErrorCodes.DF1201.value),
        (
            _fixture_path("invalid/template_via_decorator_missing_signatory.vy"),
            ErrorCodes.DF1201.value,
        ),
        (_fixture_path("invalid/choice_missing_controller.vy"), ErrorCodes.DF1203.value),
        (_fixture_path("invalid/choice_missing_consuming.vy"), ErrorCodes.DF1203.value),
        (_fixture_path("invalid/choice_non_literal_consuming.vy"), ErrorCodes.DF1203.value),
        (_fixture_path("invalid/view_with_args.vy"), ErrorCodes.DF1206.value),
        (_fixture_path("invalid/view_in_template.vy"), ErrorCodes.DF1207.value),
        (_fixture_path("invalid/choice_preconsuming_on_interface.vy"), ErrorCodes.DF1208.value),
        (_fixture_path("invalid/choice_postconsuming_on_instance.vy"), ErrorCodes.DF1208.value),
        (_fixture_path("invalid/recursion_single_func.vy"), ErrorCodes.DF1209.value),
        (_fixture_path("invalid/template_key_without_maintainer.vy"), ErrorCodes.DF1210.value),
        (_fixture_path("invalid/template_maintainer_without_key.vy"), ErrorCodes.DF1211.value),
        (
            _fixture_path("invalid/template_maintainer_from_field_not_allowed.vy"),
            ErrorCodes.DF1236.value,
        ),
        (
            _fixture_path("invalid/template_key_without_annotation_not_allowed.vy"),
            ErrorCodes.DF1213.value,
        ),
        (
            _fixture_path("invalid/exercise_by_key_without_template_not_allowed.vy"),
            ErrorCodes.DF1235.value,
        ),
        (
            _fixture_path("invalid/visible_by_key_on_template_without_key_not_allowed.vy"),
            ErrorCodes.DF1235.value,
        ),
        (_fixture_path("invalid/template_ensure_non_boolean_literal.vy"), ErrorCodes.DF1212.value),
        (
            _fixture_path("invalid/template_ensure_arithmetic_expression.vy"),
            ErrorCodes.DF1212.value,
        ),
        (_fixture_path("invalid/template_ensure_without_value.vy"), ErrorCodes.DF1212.value),
        (
            _fixture_path("invalid/template_duplicate_reserved_declarations.vy"),
            ErrorCodes.DF1232.value,
        ),
        (_fixture_path("invalid/template_method_without_choice.vy"), ErrorCodes.DF1233.value),
        (_fixture_path("invalid/template_choice_and_method_same_name.vy"), ErrorCodes.DF1233.value),
        (_fixture_path("invalid/missing_import.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/dict_type_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/unknown_map_type_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/bytes_type_not_allowed.vy"), ErrorCodes.DF1222.value),
        (_fixture_path("invalid/complex_type_not_allowed.vy"), ErrorCodes.DF1222.value),
        (_fixture_path("invalid/wildcard_missing_import_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/as_import_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/double_imports_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/unresolvable_import_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/unresolvable_bare_import_not_allowed.vy"), ErrorCodes.DF1213.value),
        (_fixture_path("invalid/enum_value_access_unknown_member.vy"), ErrorCodes.DF1216.value),
        (_fixture_path("invalid/enum_member_lowercase_not_allowed.vy"), ErrorCodes.DF1217.value),
        (_fixture_path("invalid/enum_member_mixed_case_not_allowed.vy"), ErrorCodes.DF1217.value),
        (_fixture_path("invalid/enum_member_value_not_allowed.vy"), ErrorCodes.DF1231.value),
        (
            _fixture_path("invalid/choice_leading_underscore_name_not_allowed.vy"),
            ErrorCodes.DF1218.value,
        ),
        (
            _fixture_path("invalid/choice_double_leading_underscore_name_not_allowed.vy"),
            ErrorCodes.DF1218.value,
        ),
        (
            _fixture_path("invalid/choice_name_with_no_letter_not_allowed.vy"),
            ErrorCodes.DF1218.value,
        ),
        (
            _fixture_path("invalid/choice_all_underscore_name_not_allowed.vy"),
            ErrorCodes.DF1218.value,
        ),
        (
            _fixture_path("invalid/self_reference_via_template_name_not_allowed.vy"),
            ErrorCodes.DF1219.value,
        ),
        (
            _fixture_path("invalid/unresolved_name_used_as_attribute_base_not_allowed.vy"),
            ErrorCodes.DF1219.value,
        ),
        (
            _fixture_path("invalid/unresolved_name_used_as_call_target_not_allowed.vy"),
            ErrorCodes.DF1219.value,
        ),
        (
            _fixture_path("invalid/decorator_used_without_import_not_allowed.vy"),
            ErrorCodes.DF1219.value,
        ),
        (_fixture_path("invalid/view_used_without_import_not_allowed.vy"), ErrorCodes.DF1219.value),
        (
            _fixture_path("invalid/create_used_without_import_not_allowed.vy"),
            ErrorCodes.DF1219.value,
        ),
        (
            _fixture_path("invalid/to_interface_contract_id_used_without_import_not_allowed.vy"),
            ErrorCodes.DF1219.value,
        ),
        (
            _fixture_path("invalid/multiple_violations.vy"),
            (ErrorCodes.DF1201.value, ErrorCodes.DF1203.value),
        ),
        (_fixture_path("invalid/unknown_decorator_not_allowed.vy"), ErrorCodes.DF1220.value),
        (
            _fixture_path("invalid/daml_name_with_no_arguments_not_allowed.vy"),
            ErrorCodes.DF1221.value,
        ),
        (
            _fixture_path("invalid/daml_name_with_non_literal_argument_not_allowed.vy"),
            ErrorCodes.DF1221.value,
        ),
        (
            _fixture_path("invalid/choice_observer_references_unrelated_choice_arg_not_allowed.vy"),
            ErrorCodes.DF1224.value,
        ),
        (
            _fixture_path(
                "invalid/choice_observer_references_non_field_self_attribute_not_allowed.vy"
            ),
            ErrorCodes.DF1224.value,
        ),
        (
            _fixture_path("invalid/function_call_with_wrong_argument_count_not_allowed.vy"),
            ErrorCodes.DF1225.value,
        ),
        (
            _fixture_path("invalid/function_call_with_incompatible_argument_type_not_allowed.vy"),
            ErrorCodes.DF1226.value,
        ),
        (
            _fixture_path("invalid/function_call_with_mismatched_list_element_type_not_allowed.vy"),
            ErrorCodes.DF1226.value,
        ),
    ],
)
def test_invalid(source_path, expected_code):
    with pytest.raises(Exception) as exc_info:
        _analyze(_read_fixture(source_path))

    expected_codes = (expected_code,) if isinstance(expected_code, str) else expected_code
    assert all(code in str(exc_info.value) for code in expected_codes)


def _builtin_call_source(name: str, imported: bool) -> str:
    header = f"from forge.builtins.daml_lang import {name}\n\n" if imported else ""
    return f"{header}def probe(value: int) -> int:\n    {name}(value)\n    return value\n"


def _undefined_name_errors(source: str) -> str:
    module = parse_to_ast(source, module_path="test.vy", resolved_path="test.vy")
    try:
        analyze_module(module)
    except Exception as exc:
        return str(exc)
    return ""


@pytest.mark.parametrize("name", sorted(known_builtin_function_names()))
@pytest.mark.parametrize(
    "imported,expect_undefined", [(False, True), (True, False)], ids=["missing", "imported"]
)
def test_builtin_function_requires_import(name, imported, expect_undefined):
    errors = _undefined_name_errors(_builtin_call_source(name, imported))

    assert (f"`{name}` is not defined" in errors) is expect_undefined
    assert (ErrorCodes.DF1219.value in errors) is expect_undefined


def test_known_builtin_function_names_cover_ledger_operations():
    names = known_builtin_function_names()

    assert {"create", "fetch", "exercise", "archive", "lookup_by_key", "fetch_by_key"} <= names
    assert "lookup" not in names
    assert {"to_interface_contract_id", "from_interface_contract_id", "to_interface"} <= names
    assert not {"len", "range", "ceil", "floor"} & names


def test_sibling_vy_import_still_resolves_via_load_import(tmp_path):
    sibling_path = tmp_path / "loan_terms.vy"
    sibling_path.write_text(
        "from forge.models import Party, template\n\n"
        "@template\n"
        "class LoanTerms:\n"
        "    owner: Party\n\n"
        "    rate: int\n\n"
        "    signatory: list[Party] = [owner]\n"
    )

    main_path = tmp_path / "main.vy"
    main_path.write_text(
        "from loan_terms import LoanTerms\n\n"
        "from forge.models import template\n\n"
        "@template\n"
        "class Asset:\n"
        "    owner: Party\n\n"
        "    signatory: list[Party] = [owner]\n"
    )

    module = parse_to_ast(
        main_path.read_text(), module_path="main.vy", resolved_path=str(main_path)
    )
    analyze_module(module)


def test_sibling_module_does_not_inherit_importing_module_types(tmp_path):
    sibling_path = tmp_path / "sibling.vy"
    sibling_path.write_text(
        "from forge.models import template\n\n"
        "@template\n"
        "class Sibling:\n"
        "    value: SharedType\n\n"
        "    signatory: list[Party] = []\n"
    )

    main_path = tmp_path / "main.vy"
    main_path.write_text(
        "struct SharedType:\n"
        "    value: int\n\n"
        "from sibling import Sibling\n\n"
        "from forge.models import template\n\n"
        "@template\n"
        "class Asset:\n"
        "    owner: Party\n\n"
        "    signatory: list[Party] = [owner]\n"
    )

    module = parse_to_ast(
        main_path.read_text(), module_path="main.vy", resolved_path=str(main_path)
    )
    with pytest.raises(Exception) as exc_info:
        analyze_module(module)

    assert "DF1213" in str(exc_info.value)


def test_integrity_sum_is_deterministic_for_identical_source():
    module1 = parse_to_ast(
        _read_fixture(_fixture_path("valid/valid_template.vy")),
        module_path="test.vy",
        resolved_path="test.vy",
    )
    module2 = parse_to_ast(
        _read_fixture(_fixture_path("valid/valid_template.vy")),
        module_path="test.vy",
        resolved_path="test.vy",
    )

    assert ImportsBuilder().integrity_sum(module1) == ImportsBuilder().integrity_sum(module2)


def test_integrity_sum_changes_when_source_changes():
    module1 = parse_to_ast(
        _read_fixture(_fixture_path("valid/valid_template.vy")),
        module_path="test.vy",
        resolved_path="test.vy",
    )
    module2 = parse_to_ast(
        _read_fixture(_fixture_path("valid/valid_template.vy")) + "\n",
        module_path="test.vy",
        resolved_path="test.vy",
    )

    assert ImportsBuilder().integrity_sum(module1) != ImportsBuilder().integrity_sum(module2)


def test_integrity_sum_changes_when_sibling_import_changes(tmp_path):
    sibling_path = tmp_path / "sibling.vy"
    sibling_path.write_text("@template\nclass Sibling:\n    owner: Party\n")

    main_path = tmp_path / "main.vy"
    main_path.write_text(
        "from sibling import Sibling\n" + _read_fixture(_fixture_path("valid/valid_template.vy"))
    )

    def _main_integrity_sum():
        module = parse_to_ast(
            main_path.read_text(), module_path="main.vy", resolved_path=str(main_path)
        )
        builder = ImportsBuilder()
        builder.resolve_imports(module, ExceptionList())
        return builder.integrity_sum(module)

    before = _main_integrity_sum()
    sibling_path.write_text("@template\nclass Sibling:\n    owner: Party\n    extra: Text\n")
    after = _main_integrity_sum()

    assert before != after
