# Development Environment Setup

DamlForge lets you write Canton smart contracts in `.vy` files. These use Python
syntax with a few contract-specific keywords. The `forge` compiler turns each
`.vy` file into ordinary Daml source, and DPM, the Daml package manager, builds,
tests, and ships that source. Nothing in a `.vy` file runs as Python: the file is
parsed, checked, and lowered straight to Daml.

This page sets up the toolchain, creates a first project, and runs a test against
it. The modules that follow build on this setup.

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Python | 3.11 or newer | Running the `forge` compiler |
| [DPM](https://docs.canton.network/sdks-tools/cli-tools/dpm) | Daml SDK 3.4 or 3.5 | Building DARs, running Daml Script tests, starting a sandbox |

Install DPM by following its installation page, then confirm that it can see at
least one SDK:

```bash
dpm version
```

```text
   3.4.8
 * 3.5.2
```

The SDK marked with `*` is the default. Each project can pin its own SDK with
`sdk-version` in `daml.yaml`.

## Installing DamlForge

### From PyPI

DamlForge is published on PyPI as `damlforge`. The command it installs is still
`forge`, and the Python package is still imported as `forge`:

```bash
python3 -m venv .venv
source .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install damlforge
```

### As a dependency of your own project

Pin DamlForge in your project like any other Python tool. With a
`requirements.txt`:

```text
damlforge==<version>
```

With Poetry, add it to a dev group, since it's only needed at build time:

```bash
poetry add --group dev damlforge@<version>
```

which records:

```toml
[tool.poetry.group.dev.dependencies]
damlforge = "<version>"
```

The stubs for the Daml standard library (`DA.*`, `GHC.*`), Splice (`Splice.*`),
and the `forge.models` and `forge.builtins` modules all ship inside the package.
After installing it, your `.vy` files can import them directly, with no extra
path setup:

```python
from DA.List import sort
from forge.builtins.daml_lang import create
from forge.models import Party, choice, template
```

Run the compiler from the same environment, for example `forge dar .` or
`poetry run forge dar .`. Splice stubs still need the matching Splice DAR in
`data-dependencies`; see
[Building and Packaging](10-building-packaging.md#using-other-packages-from-vy).

### From a release wheel

Every `v*.*.*` tag also publishes a wheel named
`damlforge-<version>-py3-none-any.whl` on the project's
[GitHub Releases](https://github.com/noders-team/damlforge/releases) page:

```bash
pip install damlforge-<version>-py3-none-any.whl
```

The wheel contains everything `forge` needs. You don't need a source checkout.

### From source

If you're working on the compiler or want unreleased changes, install the
repository in editable mode:

```bash
git clone https://github.com/noders-team/damlforge.git
cd damlforge
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

An editable install picks up changes under `forge/` immediately, with no
reinstall. The test and lint tool groups need `pip>=25.1`:

```bash
pip install --group test
pip install --group lint
```

### Optional: stub generation

`forge stubs` rebuilds `.vy` declarations from an existing DAR so your contracts
can import types from packages written in plain Daml. It uses `dazl`, which
is installed together with DamlForge.

## Verifying the installation

```bash
forge --version
```

```text
forge, version 0.0.12
```

`forge --help` lists the subcommands:

| Command | What it does |
|---|---|
| `forge daml <file-or-dir> -o <dir>` | Compiles `.vy` to `.daml` and writes a `.map.json` source map for each file |
| `forge dar <project-dir> -o <file.dar>` | Compiles every `.vy` file in a Daml project, then runs `dpm build` |
| `forge json <file>` | Prints the parsed JSON AST, for debugging the compiler |
| `forge stubs <file.dar> -o <dir>` | Generates `.vy` stubs from a DAR |

Running `forge <path>` with no subcommand is the same as `forge daml <path>`.

## Creating your first project

DamlForge compiles into a normal Daml project and doesn't scaffold its own. Start
from the DPM skeleton:

```bash
dpm new hello-forge
cd hello-forge
```

The skeleton contains two packages:

```text
hello-forge/
├── multi-package.yaml
├── main/
│   ├── daml.yaml
│   └── daml/Main.daml     <- contract code
└── test/
    ├── daml.yaml
    └── daml/Test.daml     <- Daml Script tests
```

You'll write the contracts in `.vy` and keep the tests in Daml Script.

### Replace the contract with a `.vy` file

Delete `main/daml/Main.daml` and create `main/daml/Main.vy`:

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

This is the same `Asset` contract the skeleton shipped in Daml. The module name
comes from the file name, so `Main.vy` becomes `module Main`. The choice `give`
becomes `Give`.

### Build the DAR

```bash
forge dar main -o main/.daml/dist/hello-forge-main-0.0.1.dar
```

`forge dar` writes `main/daml/Main.daml` next to your `.vy` file, then calls
`dpm build`. Open the generated file to see what DamlForge produced:

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

The `-o` path matches the `data-dependencies` entry in `test/daml.yaml`, so the
test package can find the DAR. Without `-o`, the DAR goes to
`main/<name>-<version>.dar`.

### Run the test

The skeleton test refers to two names that changed: the `AssetId` type synonym,
which the `.vy` file doesn't declare, and the Daml-style `newOwner` field. Update
`test/daml/Test.daml`:

```haskell
setup : Script (ContractId Asset)
```

```haskell
  bobTV <- submit alice do
    exerciseCmd aliceTV Give with new_owner = bob

  submit bob do
    exerciseCmd bobTV Give with new_owner = alice
```

Then run it:

```bash
cd test
dpm test
```

```text
Test Summary

daml/Test.daml:setup: ok, 1 active contracts, 3 transactions.
```

Your toolchain is ready: `.vy` source compiles to Daml, builds into a DAR, and
passes a Daml Script test on a real ledger engine.

## Project requirements

`forge dar` works on any Daml project whose `daml.yaml` has these fields:

| Field | Requirement |
|---|---|
| `name` | Required |
| `version` | Required |
| `source` | Required. DamlForge compiles every `.vy` file below this directory, recursively |
| `dependencies` | Required and non-empty, for example `daml-prim` and `daml-stdlib` |
| `data-dependencies` | Optional. Each entry is checked to exist before DPM runs |
| `sdk-version` | Optional. Controls version-specific code generation (see below) |

Generated `.daml` files land next to their `.vy` sources. Treat them as build
output: edit the `.vy` file and rebuild.

### SDK versions

DamlForge reads `sdk-version` and `build-options` from `daml.yaml`:

- **Daml 3.4** projects get inferred serializability, which needs no extra
  deriving clause.
- **Daml 3.5** projects can add `--explicit-serializable=yes` to
  `build-options`. Generated records and variants then derive `Serializable`.

### Interfaces in the same package

If a package declares an interface and implements it in the same package,
`damlc` raises an upgradability warning as an error. Add the following to
`build-options`:

```yaml
build-options:
  - -Wno-upgrade-interfaces
```

### Locating DPM

`forge dar` looks for DPM on `PATH` first, then at `~/.dpm/bin/dpm`. Pass
`--dpm /path/to/dpm` if it's installed somewhere else.

## Editor setup

`.vy` files are valid-looking Python, so a Python editor mode gives you syntax
highlighting. In VS Code, add this to your settings:

```json
"files.associations": {
  "*.vy": "python"
}
```

Expect the Python language server to flag contract-specific keywords such as
`struct` and `interface`. `forge` is the source of truth for errors in `.vy`
code.

To browse the generated Daml with type information and hover docs, open the
project in Daml Studio:

```bash
dpm studio
```

## Day-to-day commands

```bash
forge daml main/daml/Main.vy -o /tmp/out   # Inspect generated Daml without building
forge dar main -o <file.dar>               # Full build to a DAR
forge dar main --traceback                 # Show a Python traceback on a compiler failure
forge daml main/daml --no-i                # Stop at the first failing file
dpm test                                   # Run Daml Script tests
dpm test --all                             # Include templates from data-dependency DARs in coverage
```

When `dpm build` fails on generated code, DamlForge prints the matching `.vy`
location under the `damlc` error, using the `.map.json` source map:

```text
DamlForge source locations:
  daml/Fundamentals.daml:86:
    DamlForge source daml/Fundamentals.vy:68:1-71:21
```

## Example projects

The repository's `examples/` directory has complete projects to compile and
study:

| Example | Shows |
|---|---|
| `examples/basic/` | Templates, interfaces, cross-module imports |
| `examples/token_standard/` | A Splice token-standard holding and transfer flow |
| `examples/crowd_loans/` | Enums, `GenMap`, nested loops, Splice data dependencies |
| `examples/earnout/daml_forge/` | A multi-template workflow with `fetch`, `exercise`, and tuple returns |

Build one to check your setup against a larger codebase:

```bash
forge dar examples/earnout/daml_forge
```

## Next steps

Continue to [Language Fundamentals](02-language-fundamentals.md) to learn the
`.vy` type system, data structures, functions, and control flow, and how each one
maps to Daml.
