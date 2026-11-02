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

from forge.ast.parse import parse_to_ast
from forge.exceptions import tag_exceptions
from forge.semantics import analyze_module


def compile_json(input_vy: str) -> dict:
    """Parse and analyze .vy source, returning its AST as a JSON-compatible dict."""
    ast = parse_to_ast(input_vy)
    with tag_exceptions(ast):
        analyze_module(ast)
    return ast.to_dict()
