<div align="center">

# DamlForge

**Write Daml smart contracts for Canton in Python syntax.**

[![PyPI](https://img.shields.io/pypi/v/damlforge)](https://pypi.org/project/damlforge/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/damlforge/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

[User guide](https://docs.damlforge.noders.services) · [Examples](examples) · [Releases](https://github.com/noders-team/damlforge/releases)

</div>

DamlForge lets you write Canton smart contracts in `.vy` files. These use Python
syntax with a few contract-specific (Vyper-flavored) keywords. The `forge` compiler turns each
`.vy` file into ordinary Daml source, and DPM, the Daml package manager, builds,
tests, and ships that source. Nothing in a `.vy` file runs as Python: the file is
parsed, checked, and lowered straight to Daml.

## How it works

**1. Install DamlForge** (Python 3.11 or newer):

```bash
pip install damlforge
```

**2. Write a contract** in `Main.vy`:

```python
from forge.builtins.daml_lang import create
from forge.models import ContractId, Party, choice, template


@template
class Asset:
    issuer: Party
    owner: Party
    name: str

    signatory: Party = issuer
    observer: Party = owner
    ensure: bool = name != ""

    @choice(controller=owner, consuming=True)
    def give(self, new_owner: Party) -> ContractId[Asset]:
        return create(Asset(issuer=self.issuer, owner=new_owner, name=self.name))
```

**3. Compile it to Daml:**

```bash
forge daml Main.vy -o out/
```

`forge` generates `out/Main.daml`:

```haskell
module Main where

template Asset
  with
    issuer : Party
    owner : Party
    name : Text
  where
    signatory issuer
    observer owner
    ensure name /= ""

    choice Give : ContractId Asset
      with
        new_owner : Party
      controller owner
      do
        let self = this
        create (Asset with issuer = self.issuer, owner = new_owner, name = self.name)
```

**More:** see the [full documentation](#documentation), from setup to packaging.

## Documentation

The guide at [docs.damlforge.noders.services](https://docs.damlforge.noders.services)
walks through DamlForge from setup to packaging. Every example in it compiles
with `forge dar` and is covered by a passing Daml Script test.

| # | Page | Covers |
|---|---|---|
| 1 | [Development Environment Setup](https://docs.damlforge.noders.services/guide/dev-environment/) | Installing DPM and DamlForge, creating a project, editor setup, day-to-day commands |
| 2 | [Language Fundamentals](https://docs.damlforge.noders.services/guide/language-fundamentals/) | Types, structs, enums, variants, `Optional`, functions, control flow, dates, imports |
| 3 | [Compiler Errors](https://docs.damlforge.noders.services/guide/compiler-errors/) | Every `DF` error code, with the message, cause, and fix |
| 4 | [Contract Templates](https://docs.damlforge.noders.services/guide/contract-templates/) | Templates, signatories, observers, `ensure`, testing with Daml Script |
| 5 | [Choices](https://docs.damlforge.noders.services/guide/choices/) | Choices as methods and as delegation, updating a contract in place, consuming and non-consuming choices, a simple cash model |
| 6 | [Design Patterns](https://docs.damlforge.noders.services/guide/design-patterns/) | Propose-accept, delegation, authorization, locking, multi-party agreements |
| 7 | [Working with Time](https://docs.damlforge.noders.services/guide/working-with-time/) | `datetime` support, ledger time, deadlines and time windows, `setTime`/`passTime` |
| 8 | [Contract Keys](https://docs.damlforge.noders.services/guide/contract-keys/) | `key` and `maintainer`, by-key lookups, Daml 3.5 requirements |
| 9 | [Interfaces](https://docs.damlforge.noders.services/guide/interfaces/) | Declaring and implementing interfaces, views, converting contract IDs |
| 10 | [Building and Packaging](https://docs.damlforge.noders.services/guide/building-packaging/) | `forge dar`, `daml.yaml` settings, multi-package projects, DAR contents |
| 11 | [Variants](https://docs.damlforge.noders.services/guide/variants/) | Constructor styles, recursive and generic variants, current limitations |

## Claude Code skill

See [`.claude/skills/damlforge-vy-lang/`](.claude/skills/damlforge-vy-lang/SKILL.md)
for the full language reference. It is also a Claude Code skill: Claude Code uses
it to write, read, and review `.vy` files, translate `.daml` files to `.vy`, and
explain `DF` compiler errors in this repository.

## Installing

DamlForge is published on PyPI as `damlforge`. It installs the `forge` command and the `forge` Python package:

```bash
python -m venv .venv
source .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install damlforge
```

<details>
<summary><b>Using DamlForge in another project</b> (pip, Poetry)</summary>

Add DamlForge as a dependency of the project that holds your `.vy` contracts.

With pip, pin it in `requirements.txt`:

```text
damlforge==<version>
```

Use `damlforge[stubgen]==<version>` instead if you also run `forge stubs`. Then install and build:

```bash
pip install -r requirements.txt
forge dar path/to/daml_project -o out.dar
```

With Poetry, add it to a dev group, since it's only needed at build time:

```bash
poetry add --group dev damlforge@<version>
poetry add --group dev 'damlforge[stubgen]@<version>'   # if you also run forge stubs
```

This records it in your `pyproject.toml`:

```toml
[tool.poetry.group.dev.dependencies]
damlforge = { version = "<version>", extras = ["stubgen"] }
```

Leave out `extras` if you don't need `forge stubs`. Run the compiler through Poetry's environment:

```bash
poetry install
poetry run forge dar path/to/daml_project -o out.dar
```

The stubs for the Daml standard library (`DA.*`, `GHC.*`), Splice (`Splice.*`), `forge.models`, and `forge.builtins` ship inside the package. Once it's installed, `.vy` files import them directly, with no extra path setup:

```python
from DA.List import sort
from forge.builtins.daml_lang import create
from forge.models import Party, choice, template
```

Splice stubs also need the matching Splice DAR in `data-dependencies` in `daml.yaml`. See [Development Environment Setup](https://docs.damlforge.noders.services/guide/dev-environment/#as-a-dependency-of-your-own-project) and [Building and Packaging](https://docs.damlforge.noders.services/guide/building-packaging/#using-other-packages-from-vy).

</details>

<details>
<summary><b>Installing from GitHub Releases</b></summary>

Releases are also built by CI on every `v*.*.*` tag and attached to the corresponding [GitHub Release](https://github.com/noders-team/damlforge/releases) as `damlforge-<version>-py3-none-any.whl`, which installs with `pip install damlforge-<version>-py3-none-any.whl`.

</details>

<details>
<summary><b>Upgrading</b></summary>

Upgrade in place:

```bash
pip install -U damlforge==<new-version>
```

If you installed the old `forge` wheel from GitHub Releases, run `pip uninstall forge` first, since the distribution was renamed to `damlforge`.

</details>

### Publishing to PyPI

`.github/workflows/publish-pypi.yml` is started by hand from the Actions tab (**Run workflow**) with the version to publish. It builds the sdist and wheel with that version and uploads them through PyPI trusted publishing, so no API token is stored in the repository.

## Commands

Installing the wheel puts a `forge` command on your `PATH`. It's a [Click](https://click.palletsprojects.com/) CLI with a subcommand per compile target:

```bash
forge --help          # top-level usage and subcommand list
forge --version       # installed forge version
forge daml --help     # per-subcommand usage (also: forge dar, forge json, forge stubs)
```

```bash
forge daml path/to/file.vy -o out/          # .vy -> out/<Module>.daml + out/<Module>.map.json
forge daml path/to/vy_source_dir -o out/    # compiles top-level .vy files in the directory
forge dar path/to/vy_project -o out.dar     # compile the project and build a real .dar
forge json path/to/file.vy --pretty-json    # .vy -> JSON AST, printed to stdout
forge stubs input.dar -o generated/         # .dar -> generated .vy stubs
```

`forge <path> [options]` (no subcommand) is shorthand for `forge daml <path> [options]`.
`forge stubs` needs `pip install 'damlforge[stubgen]'`.

See [Building and Packaging](https://docs.damlforge.noders.services/guide/building-packaging/) for `daml.yaml`
requirements, DPM options, and stub generation.

## Examples

The [`examples/`](examples) directory has complete projects to compile and study:

| Example | Shows |
|---|---|
| [`examples/basic/`](examples/basic) | Templates, interfaces, cross-module imports |
| [`examples/token_standard/`](examples/token_standard) | A Splice token-standard holding and transfer flow |
| [`examples/crowd_loans/`](examples/crowd_loans) | Enums, `GenMap`, nested loops, Splice data dependencies |
| [`examples/splice-util-batched-markers/`](examples/splice-util-batched-markers) | A Splice featured-app template with structs, choices, and Splice API data dependencies |

## Building the wheel yourself

If you have the source checked out:

```bash
pip install build
python -m build          # or: make build
```

produces `dist/damlforge-<version>-py3-none-any.whl`, versioned from the nearest git tag (see `Makefile`'s `build`/`release` targets and `.github/workflows/build-wheel.yml`).

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .              # installs forge + runtime deps (click, etc.) in editable mode
pip install --group test      # pytest + friends
pip install --group lint      # black/flake8/isort/mypy
```

`--group` needs `pip>=25.1` (`pip install -U pip` first if needed); otherwise install the pinned packages listed under `[dependency-groups]` in `pyproject.toml` directly.

With an editable install, `forge`/`python -m forge` picks up changes under `forge/` immediately - no reinstall needed:

```bash
forge --help
forge daml examples/basic/simple.vy -o /tmp/out && cat /tmp/out/Simple*.daml
```

Run the test suite and linters:

```bash
pytest -q      # or: make test
make lint      # mypy, black, flake8, isort in sequence
```

## License

DamlForge is licensed under the [Apache License, Version 2.0](LICENSE).

Copyright 2026 Noders Team. Author: Artem Bogomaz <artembogomaz@gmail.com>.
