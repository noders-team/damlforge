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

from forge.ast.nodes import Dict
from forge.ast.parse import parse_to_ast
from forge.codegen import generate_daml
from forge.ir import lower_module
from forge.semantics import analyze_module

_HEADER = "def g(m: TextMap[int]) -> int:\n    return 0\n\n"


def _parse(source):
    return parse_to_ast(source, module_path="test.vy", resolved_path="test.vy")


def _compile_to_daml(source):
    module = _parse(source)
    import_graph = analyze_module(module)
    ir = lower_module(module, import_graph=import_graph)
    daml_source, _ = generate_daml(ir, module_name="Test")
    return daml_source


@pytest.mark.parametrize(
    "call, expected",
    [
        (f"g({{'a': {1}}})", 'g (TextMap.fromList [("a", 1)])'),
        (f"g({{'a': {1}, 'b': {2}}})", 'g (TextMap.fromList [("a", 1), ("b", 2)])'),
        ("g({})", "g (TextMap.fromList [])"),
    ],
)
def test_map_literal_call_argument(call, expected):
    source = _HEADER + f"def f() -> int:\n    return {call}\n"

    call_node = _parse(source).body[1].body[0].value

    assert isinstance(call_node.args[0], Dict)
    assert call_node.keywords == []
    assert expected in _compile_to_daml(source)
