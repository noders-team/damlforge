# Building and Packaging

A Daml application ships as one or more *packages*. Each package is built into a
DAR file, uploaded to a participant node, and identified on the ledger by its
package ID. DamlForge compiles `.vy` files into the Daml source of a package and
uses DPM to build the DAR, so everything DPM does still applies.

This page uses the project from the previous four modules: a `main` package with
the `.vy` contracts and a `test` package with Daml Script tests.

## Commands

| Command | What it does |
|---|---|
| `forge dar <project> -o <file.dar>` | Compiles every `.vy` file in the project, then runs `dpm build` |
| `forge daml <file-or-dir> -o <dir>` | Compiles `.vy` files to `.daml` without building a DAR |
| `forge stubs <file.dar> -o <dir>` | Writes `.vy` declarations for the templates and types in a DAR |
| `forge json <file.vy>` | Prints the parsed `.vy` file as JSON |
| `dpm build` / `dpm build --all` | Builds one package, or every package in `multi-package.yaml` |
| `dpm test` | Runs the Daml Script tests in the current package |
| `dpm damlc inspect-dar <file.dar>` | Lists the files in a DAR |

`forge daml` writes a JSON source map next to each `.daml` file. Directory input is
intentionally non-recursive; `forge dar` recursively compiles every `.vy` below the
project's configured `source` directory.

## What `forge dar` does

1. **Reads `daml.yaml`.** The project needs `name`, `version`, `source`, and a
   non-empty `dependencies` list. Every `data-dependencies` entry must point to an
   existing file.
2. **Compiles every `.vy` file under `source`**, including subdirectories, and
   writes a `.daml` file next to each one. A file's path gives its module name:
   `daml/Finance/Coin.vy` becomes `module Finance.Coin`.
3. **Runs `dpm build`** and writes the DAR. Without `-o`, the DAR goes to
   `<project>/<name>-<version>.dar`. DPM's own default,
   `.daml/dist/<name>-<version>.dar`, is where other packages in a
   multi-package project look for it, so the examples pass that path to `-o`.
4. **Maps `damlc` errors back to `.vy` lines.** When `damlc` reports an error in a
   generated file, DamlForge adds the `.vy` file and line it came from.

The generated `.daml` files are ordinary Daml, so you can read them to see exactly
what was compiled.

## Package configuration

`main/daml.yaml` for the project used in these modules:

```yaml
sdk-version: 3.5.2
name: guide-main
source: daml
version: 0.0.1
dependencies:
  - daml-prim
  - daml-stdlib
build-options:
  - --target=2.3
  - -Wno-upgrade-interfaces
```

These settings change what DamlForge generates or accepts:

| Setting | Effect |
|---|---|
| `sdk-version` | 3.5 enables contract keys and `Serializable` derivation; 3.4 doesn't support keys |
| `--target=2.3` | Required for contract keys (`DF1234`) |
| `--explicit-serializable=yes` | With SDK 3.5, data types derive `Serializable` |
| `-Wno-upgrade-interfaces` | Lets interfaces and their implementations share a package |

`forge daml` uses the nearest `daml.yaml` in the input's directory or its parents.
Without one, it applies no SDK checks.

## Multi-package projects

`dpm new` creates two packages and a `multi-package.yaml` that lists them:

```yaml
packages:
- ./main
- ./test
```

The test package depends on the DAR of the main package:

```yaml
sdk-version: 3.5.2
name: guide-test
source: daml
version: 0.0.1
dependencies:
  - daml-prim
  - daml-stdlib
  - daml-script
data-dependencies:
  - ../main/.daml/dist/guide-main-0.0.1.dar
build-options:
  - --target=2.3
```

A package that depends on a DAR built for LF 2.3 must target LF 2.3 or later too,
or `damlc` reports `dependencies have incompatible LF versions`.

Build the `.vy` package first, then the rest:

```bash
forge dar main -o main/.daml/dist/guide-main-0.0.1.dar
dpm build --all
cd test && dpm test
```

`dpm build --all` reuses the generated `.daml` files in `main`, so it only needs
`forge dar` to have run after the last `.vy` change.

## `dependencies` and `data-dependencies`

- **`dependencies`** is for the Daml libraries that come with the SDK:
  `daml-prim`, `daml-stdlib`, and `daml-script`.
- **`data-dependencies`** is for DARs: your own packages and third-party ones.
  Daml reads only the compiled Daml-LF from them, so they can come from a ledger
  or an older SDK.

Keep `daml-script` out of the packages you deploy. It's for tests, and it isn't
compatible across SDK versions.

## Using other packages from `.vy`

A `.vy` file can import from three places:

- **Other `.vy` files in the same package**, by module name: `from Coin import Coin`.
- **The Daml standard library**, through stubs that ship with DamlForge:
  `from DA.List import sort`. The compiler adds the matching Daml import.
- **Splice packages**, through bundled stubs: `from Splice.Amulet import Amulet`.
  The project must also list the matching Splice DAR in `data-dependencies`, so
  `damlc` can find the real definitions.

A `.vy` package can't import templates from a DAR you built yourself. Stubs
generated into the `source` directory would be compiled into the package as new
modules instead of referring to the DAR. Keep `.vy` modules that use each other in
one package, and consume its DAR from Daml packages, such as the test package,
with `data-dependencies`.

## Inspecting a DAR

A DAR is a ZIP archive:

```bash
dpm damlc inspect-dar main/.daml/dist/guide-main-0.0.1.dar
```

It contains:

- a `.dalf` file for the package and for each dependency, holding the compiled
  Daml-LF that participants run;
- the `.daml` source of each module, as generated by DamlForge (the `.vy` files
  aren't included);
- `.hi` and `.hie` interface files, used by `dependencies` and IDEs;
- a `META-INF/MANIFEST.MF` that names the main package.

`--json` prints the same information as JSON, including `main_package_id`. The
ledger identifies templates by package ID, module, and name, never by package name
or version. Any change to a package changes its ID and the IDs of the packages
that depend on it.

`forge stubs` turns a DAR back into `.vy` declarations, with choice bodies left as
`pass`. Use it to read the API of a package you depend on:

```bash
forge stubs main/.daml/dist/guide-main-0.0.1.dar -o stubs --module Coin
```

It needs the `stubgen` extra (`pip install 'damlforge[stubgen]'`), and reads DARs for
LF 2.1 to 2.3.

It accepts either a DAR file or a Daml project directory (the latter is built with
DPM first):

```bash
forge stubs path/to/package.dar -o generated/
forge stubs path/to/daml_project -o generated/ --dpm /path/to/dpm
forge stubs path/to/package.dar --module Some.Module --module Other.Module
```

The `--module` option may be repeated and selects modules from the main package or
its dependencies. Unsupported declarations are skipped and reported by the command.

## Deciding what goes in a package

- **Separate tests from contracts.** Tests depend on `daml-script`, which
  shouldn't be uploaded to a production ledger. That's why `dpm new` creates a
  separate `test` package.
- **Separate public APIs from implementations.** Interfaces that other teams
  build on should change rarely, so they belong in their own package, as in the
  Token Standard. With DamlForge, an interface and the templates that implement it
  must currently share a `.vy` package (see above), so this split is only possible
  when the implementations are written in Daml.
- **Separate by business domain.** Stakeholders can review and upload only the
  packages for the workflows they take part in.

## Next steps

[Variants](11-variants.md) goes deeper into sum types: payload styles, recursive
and generic variants, and how to test them. [Compiler Errors](03-compiler-errors.md)
is the reference for every `DF` error code, and the
[examples](../../examples) directory has larger projects, including Splice token
standard contracts.
