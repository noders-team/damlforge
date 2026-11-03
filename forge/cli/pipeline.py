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

from dataclasses import dataclass
from pathlib import Path

from forge.ast.parse import parse_to_ast
from forge.codegen import generate_daml
from forge.codegen.source_map import with_source_file
from forge.daml_versions import DamlCodegenSettings
from forge.exceptions import tag_exceptions
from forge.ir import lower_module
from forge.semantics import analyze_module


@dataclass(frozen=True)
class Compiled:
    daml: str
    source_map: dict


def compile_vy(
    file: Path, module_path: str, module_name: str, settings: DamlCodegenSettings
) -> Compiled:
    """Compile one .vy file into Daml source plus its source map.

    Args:
        file: The .vy file to compile.
        module_path: Path shown in diagnostics for this module.
        module_name: Name of the generated Daml module.
        settings: Daml code generation settings.

    Returns:
        The generated Daml source and the source map tagged with `file`.
    """
    source = file.read_text()
    module = parse_to_ast(source, module_path=module_path, resolved_path=str(file.resolve()))
    with tag_exceptions(module):
        graph = analyze_module(module)
        ir = lower_module(module, import_graph=graph)
        daml, source_map = generate_daml(ir, module_name=module_name, settings=settings)
    return Compiled(daml, with_source_file(source_map, file))
