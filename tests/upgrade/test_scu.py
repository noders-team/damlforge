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

import os
import shutil
from pathlib import Path

import pytest

from forge.cli.forge_dar import _resolve_dpm_path, build_dar

FIXTURES = Path(__file__).parent / "fixtures"
PACKAGE = "asset-pkg"
SDK_VERSION = "3.5.2"


def _daml_yaml(version: str, upgrades: str | None) -> str:
    lines = [
        f"sdk-version: {SDK_VERSION}",
        f"name: {PACKAGE}",
        f"version: {version}",
        "source: daml",
        "dependencies: [daml-prim, daml-stdlib]",
        "build-options: [--target=2.3]",
    ]
    if upgrades is not None:
        lines.append(f"upgrades: {upgrades}")
    return "\n".join(lines) + "\n"


def _stage(fixture: str, root: Path, version: str, upgrades: str | None) -> Path:
    project = root / fixture
    (project / "daml").mkdir(parents=True)
    shutil.copy(FIXTURES / fixture / "asset.vy", project / "daml" / "asset.vy")
    (project / "daml.yaml").write_text(_daml_yaml(version, upgrades))
    return project


@pytest.fixture(scope="module")
def dpm() -> str:
    try:
        return _resolve_dpm_path(None)
    except RuntimeError as exc:
        if os.environ.get("CI"):
            pytest.fail(str(exc))
        pytest.skip("dpm is required to run the SCU upgrade checks")


@pytest.fixture(scope="module")
def baseline(dpm, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("scu")
    project = _stage("v1", root, "1.0.0", None)
    build_dar(project, root / f"{PACKAGE}-1.0.0.dar", dpm)
    return root


@pytest.mark.parametrize(
    "fixture, accepted, message",
    [
        ("v2_optional_field", True, None),
        ("v2_retyped_field", False, "Field 'amount' changed type from Int64 to Text"),
    ],
)
def test_upgrade_check_against_previous_version(baseline, dpm, fixture, accepted, message):
    previous = baseline / f"{PACKAGE}-1.0.0.dar"
    project = _stage(fixture, baseline, "2.0.0", str(previous))
    output = baseline / f"{fixture}.dar"
    if accepted:
        assert build_dar(project, output, dpm) == output
        return
    with pytest.raises(RuntimeError) as failure:
        build_dar(project, output, dpm)
    assert "has changed the types of some of its original fields" in str(failure.value)
    assert message in str(failure.value)
    assert "asset.vy" in str(failure.value)
