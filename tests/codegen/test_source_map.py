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

from forge.codegen.source_map import DamlRange, annotate_daml_output, map_daml_location


def _source_map():
    return {
        "daml/Earnout/Earnout.daml": {
            "sourceFile": "examples/earnout/earnout.vy",
            "mappings": [
                {
                    "generated": {"startLine": 89, "endLine": 121},
                    "source": {"startLine": 94, "startColumn": 0, "endLine": 127, "endColumn": 1},
                    "kind": "template",
                    "name": "QuarterVerdict",
                },
                {
                    "generated": {"startLine": 105, "endLine": 111},
                    "source": {"startLine": 109, "startColumn": 4, "endLine": 121, "endColumn": 5},
                    "kind": "choice",
                    "name": "QuarterVerdict.raise_dispute",
                },
                {
                    "generated": {"startLine": 113, "endLine": 120},
                    "source": {"startLine": 123, "startColumn": 4, "endLine": 127, "endColumn": 1},
                    "kind": "choice",
                    "name": "QuarterVerdict.apply_resolution",
                },
                {
                    "generated": {"startLine": 80, "endLine": 87},
                    "source": {"startLine": 87, "startColumn": 4, "endLine": 91, "endColumn": 16},
                    "kind": "choice",
                    "name": "EarnoutAgreement.submit_quarter",
                },
            ],
        }
    }


def test_map_daml_range_prefers_most_specific_ir_mapping():
    location = map_daml_location(
        "daml/Earnout/Earnout.daml", DamlRange(111, 17, 111, 186), "missing field", _source_map()
    )

    assert location is not None
    assert location.file == "examples/earnout/earnout.vy"
    assert location.start_line == 109


def test_map_daml_desugared_one_one_error_by_choice_name():
    location = map_daml_location(
        "daml/Earnout/Earnout.daml",
        DamlRange(1, 1, 1, 1),
        "Expected type: Submit_quarter -> Update (...) in EarnoutAgreement",
        _source_map(),
    )

    assert location is not None
    assert location.start_line == 87


def test_annotate_daml_output_adds_source_locations():
    output = "\n".join(
        [
            "File:     daml/Earnout/Earnout.daml",
            "Range:    111:17-111:186",
            "Message:",
            "daml/Earnout/Earnout.daml:111:17: error:",
            "    • Constructor ‘Dispute’ does not have the required strict field(s): "
            "verdict_quarter",
            "    • In the first argument of ‘create’:",
        ]
    )

    annotated = annotate_daml_output(output, _source_map())

    assert "DamlForge source locations:" in annotated
    assert "examples/earnout/earnout.vy:109:5-121:6" in annotated
    assert (
        "Daml explanation: Constructor ‘Dispute’ does not have the required "
        "strict field(s): verdict_quarter"
    ) in annotated


def test_annotate_daml_output_normalizes_wrapped_explanation():
    output = "\n".join(
        [
            "File:     daml/Earnout/Earnout.daml",
            "Range:    111:17-111:186",
            "Message:",
            "daml/Earnout/Earnout.daml:111:17: error:",
            "    • Couldn't match type ‘EarnoutAgreement’",
            "                     with ‘(ContractId EarnoutAgreement, ContractId QuarterVerdict)’",
            "    • In the expression:",
        ]
    )

    annotated = annotate_daml_output(output, _source_map())

    assert (
        "Daml explanation: Couldn't match type ‘EarnoutAgreement’ with "
        "‘(ContractId EarnoutAgreement, ContractId QuarterVerdict)’"
    ) in annotated
