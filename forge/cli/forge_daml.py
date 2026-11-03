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

import json
from pathlib import Path

import yaml
from loguru import logger

from forge.cli.pipeline import compile_vy
from forge.codegen import daml_module_name
from forge.daml_versions import DamlCodegenSettings


def _compile_one(
    file: Path, output_dir: Path, pretty_json: bool, settings: DamlCodegenSettings
) -> None:
    """Compile one .vy file and write its .daml and .map.json files to `output_dir`."""
    logger.info(f"Processing file: {file}")
    name = daml_module_name(file.stem)
    compiled = compile_vy(file, file.name, name, settings)

    daml_file = output_dir / f"{name}.daml"
    logger.info(f"writing DAML output to: {daml_file}")
    daml_file.write_text(compiled.daml)

    map_file = output_dir / f"{name}.map.json"
    logger.info(f"writing source map to: {map_file}")
    indent = 2 if pretty_json else None
    map_file.write_text(json.dumps(compiled.source_map, indent=indent, sort_keys=True, default=str))


def compile_daml(
    input_path: Path,
    output_dir: Path,
    pretty_json: bool = False,
    ignore_errors: bool = True,
    traceback: bool = False,
) -> None:
    """Compile a .vy file, or every .vy file in a directory, into .daml files.

    Args:
        input_path: A .vy file or a directory containing .vy files.
        output_dir: Directory the .daml and .map.json files are written to.
        pretty_json: Indent the source map JSON.
        ignore_errors: Keep compiling the remaining files after one fails.
        traceback: Re-raise errors instead of logging them.

    Raises:
        SystemExit: When a file fails and `ignore_errors` is False.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    project_dir = _find_project_dir(input_path)
    settings = _load_codegen_settings(project_dir)

    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(input_path.glob("*.vy"))

    for file in files:
        try:
            _compile_one(file, output_dir, pretty_json, settings)
        except Exception as exc:
            if traceback:
                raise
            logger.error(f"Error processing file {file}: {exc}")
            if not ignore_errors:
                raise SystemExit(1)


def _find_project_dir(input_path: Path) -> Path:
    """Return the nearest directory above `input_path` holding daml.yaml, else its own folder."""
    start = input_path if input_path.is_dir() else input_path.parent
    candidates = [start, *start.parents]
    for directory in candidates:
        if (directory / "daml.yaml").is_file():
            return directory
    return start


def _load_codegen_settings(project_dir: Path) -> DamlCodegenSettings:
    """Read code generation settings from `project_dir`/daml.yaml, or defaults if absent.

    Raises:
        ValueError: When daml.yaml is not a YAML mapping.
    """
    daml_yaml = project_dir / "daml.yaml"
    if not daml_yaml.is_file():
        return DamlCodegenSettings()
    config = yaml.safe_load(daml_yaml.read_text()) or {}
    if not isinstance(config, dict):
        raise ValueError(f"{daml_yaml} must contain a YAML mapping")
    return DamlCodegenSettings.from_config(config)
