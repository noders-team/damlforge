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

from forge.ast import nodes as vy_ast
from forge.builtin_stubs import scope_of, stub_scope
from forge.exceptions import ExceptionList
from forge.semantics.analysis.imports import ImportGraph, ImportsBuilder


def analyze_module(module: vy_ast.Module) -> ImportGraph:
    scope = scope_of(module)
    module._metadata["stub_scope"] = scope
    with stub_scope(scope):
        return _analyze_module(module)


def _analyze_module(module: vy_ast.Module) -> ImportGraph:
    from forge.semantics.analysis.module import ModuleAnalyzer, RecursionAnalyzer

    errors = ExceptionList()
    imports_builder = ImportsBuilder()
    imports_builder.resolve_imports(module, errors)
    errors.raise_if_not_empty()

    ModuleAnalyzer(module, errors).analyze()
    RecursionAnalyzer(module, errors).analyze()
    errors.raise_if_not_empty()

    return imports_builder.graph
