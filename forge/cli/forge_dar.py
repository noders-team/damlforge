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

import re
import shutil
import subprocess
from pathlib import Path

import yaml
from loguru import logger

from forge.cli.pipeline import compile_vy
from forge.codegen import daml_module_name
from forge.codegen.source_map import annotate_daml_output
from forge.daml_versions import DamlCodegenSettings

_PACKAGE_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9]*(-[A-Za-z][A-Za-z0-9]*)*$")
_PACKAGE_VERSION_REGEX = re.compile(r"^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*$")
_REQUIRED_PACKAGE_CONFIG_FIELDS = ("name", "source", "version", "dependencies")


class ProjectStructureError(ValueError):
    pass


def _resolve_dpm_path(dpm_path: str | None) -> str:
    """Return the dpm executable: the given path, else PATH, else ~/.dpm/bin/dpm.

    Raises:
        RuntimeError: When no dpm executable can be found.
    """
    if dpm_path is not None:
        return dpm_path
    found = shutil.which("dpm")
    if found is not None:
        return found
    fallback = Path.home() / ".dpm" / "bin" / "dpm"
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError(
        "Could not find the `dpm` executable on PATH or at ~/.dpm/bin/dpm. "
        "Install DPM (https://docs.digitalasset.com/build/3.4/dpm/dpm.html) or pass --dpm."
    )


def _load_package_config(daml_yaml: Path) -> dict:
    """Load daml.yaml as a mapping.

    Raises:
        ProjectStructureError: When the file is not valid YAML or not a mapping.
    """
    try:
        config = yaml.safe_load(daml_yaml.read_text())
    except yaml.YAMLError as exc:
        raise ProjectStructureError(f"{daml_yaml} is not valid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ProjectStructureError(f"{daml_yaml} must contain a YAML mapping")
    return config


def check_package_config(config: dict, daml_yaml: Path) -> None:
    """Validate required fields, name, version and dependencies of a package config.

    Raises:
        ProjectStructureError: When any of the checks fails.
    """
    missing = [field for field in _REQUIRED_PACKAGE_CONFIG_FIELDS if field not in config]
    if missing:
        raise ProjectStructureError(
            f"{daml_yaml} is missing required field(s): {', '.join(missing)}"
        )

    name = str(config["name"])
    if not _PACKAGE_NAME_REGEX.match(name):
        raise ProjectStructureError(
            f"Invalid package name in {daml_yaml}: {name!r}. Package names should have the "
            f"format {_PACKAGE_NAME_REGEX.pattern}."
        )

    version = str(config["version"])
    if not _PACKAGE_VERSION_REGEX.match(version):
        raise ProjectStructureError(
            f"Invalid package version in {daml_yaml}: {version!r}. Package versions should "
            f"have the format {_PACKAGE_VERSION_REGEX.pattern}."
        )

    deps = config["dependencies"]
    if not isinstance(deps, list) or not deps:
        raise ProjectStructureError(f"{daml_yaml}: `dependencies` must be a non-empty list")


def check_data_dependencies(config: dict, project_dir: Path, daml_yaml: Path) -> None:
    """Check that every declared data-dependency file exists.

    Raises:
        ProjectStructureError: When a declared file is missing.
    """
    deps = config.get("data-dependencies") or []
    for dep in deps:
        dep_path = project_dir / dep
        if not dep_path.is_file():
            raise ProjectStructureError(
                f"{daml_yaml} declares data-dependency {dep!r}, but no such file "
                f"exists at {dep_path}"
            )


def check_project_structure(project_dir: Path) -> dict:
    """Validate the Daml project in `project_dir` and return its parsed daml.yaml.

    Raises:
        ProjectStructureError: When daml.yaml is missing or invalid.
    """
    daml_yaml = project_dir / "daml.yaml"
    if not daml_yaml.is_file():
        raise ProjectStructureError(
            f"{project_dir} is not a Daml project: no daml.yaml found. DamlForge compiles "
            "into an existing Daml project - set one up (e.g. via `daml new`, or copy an "
            "existing one) with its daml.yaml/lib checked in alongside the .vy sources."
        )

    config = _load_package_config(daml_yaml)
    check_package_config(config, daml_yaml)
    check_data_dependencies(config, project_dir, daml_yaml)
    return config


def _check_output_path(output_dar: Path | None) -> None:
    """Check that `output_dar`, when given, is a path to a .dar file.

    Raises:
        ValueError: When it is a directory or does not end in `.dar`.
    """
    if output_dar is None:
        return
    if output_dar.is_dir():
        raise ValueError(
            f"-o must be a path to the .dar FILE to write, not a directory: {output_dar}"
        )
    if output_dar.suffix != ".dar":
        raise ValueError(f"-o must end in `.dar`: {output_dar}")


def _compile_sources(
    source_dir: Path, project_dir: Path, settings: DamlCodegenSettings
) -> dict[str, dict]:
    """Compile every .vy file under `source_dir` into a sibling .daml file.

    Returns:
        Source maps keyed by the .daml path relative to `project_dir`.

    Raises:
        ValueError: When no .vy files are found.
    """
    vy_files = sorted(source_dir.rglob("*.vy"))
    if not vy_files:
        raise ValueError(f"No .vy files found under: {source_dir}")

    source_maps: dict[str, dict] = {}
    for file in vy_files:
        logger.info(f"Compiling: {file}")
        rel = file.relative_to(source_dir)
        stem = daml_module_name(rel.stem)
        name = ".".join((*rel.parent.parts, stem))
        compiled = compile_vy(file, str(rel), name, settings)

        daml_file = source_dir / rel.parent / f"{stem}.daml"
        daml_file.parent.mkdir(parents=True, exist_ok=True)
        daml_file.write_text(compiled.daml)
        source_maps[str(daml_file.relative_to(project_dir))] = compiled.source_map
    return source_maps


def _run_dpm(dpm: str, project_dir: Path, output: Path, source_maps: dict[str, dict]) -> None:
    """Run `dpm build` in `project_dir`, writing the .dar to `output`.

    Raises:
        RuntimeError: When the build fails or produces no .dar.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [dpm, "build", "-o", str(output)], cwd=project_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        report = annotate_daml_output(f"{result.stdout}\n{result.stderr}", source_maps)
        raise RuntimeError(f"dpm build failed (exit {result.returncode}):\n{report}")
    logger.info(result.stdout.strip())

    if not output.is_file():
        raise RuntimeError(f"dpm build reported success but no .dar was found at: {output}")


def build_dar(input_dir: Path, output_dar: Path | None = None, dpm_path: str | None = None) -> Path:
    """Compile the .vy sources of a Daml project and package them into a .dar.

    Args:
        input_dir: Daml project directory containing daml.yaml.
        output_dar: Where to write the .dar. Defaults to <name>-<version>.dar in `input_dir`.
        dpm_path: Path to the dpm executable. Defaults to a lookup.

    Returns:
        The path of the built .dar.

    Raises:
        ValueError: When the input or output path is invalid, or no .vy files exist.
        ProjectStructureError: When the Daml project is invalid.
        RuntimeError: When dpm is missing or the build fails.
    """
    input_dir = input_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input path is not a directory: {input_dir}")
    _check_output_path(output_dar)

    config = check_project_structure(input_dir)
    settings = DamlCodegenSettings.from_config(config)
    source_dir = (input_dir / str(config["source"])).resolve()
    source_dir.mkdir(parents=True, exist_ok=True)
    dpm = _resolve_dpm_path(dpm_path)
    source_maps = _compile_sources(source_dir, input_dir, settings)

    name = config["name"]
    version = config["version"]
    output = output_dar if output_dar is not None else input_dir / f"{name}-{version}.dar"
    logger.info(f"Running dpm build (package: {name}, version: {version})")
    _run_dpm(dpm, input_dir, output, source_maps)
    return output
