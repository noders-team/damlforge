---
name: damlforge-vy-lang
description: Use when writing, reading, or reviewing .vy source files in the DamlForge compiler (this repo) - a Python/Vyper-syntax DSL that compiles to real Daml/Canton smart contracts. Applies whenever asked to author a Daml template/choice/interface in .vy, translate a .daml file to .vy, or explain DF12xx compiler errors.
---

# DamlForge .vy Language

## Overview

DamlForge compiles `.vy` files (Python syntax, Vyper-flavored keywords) into real `.daml` source. It is NOT Vyper (no EVM/gas/storage concepts survive) and NOT Python (nothing executes - it's an AST that lowers straight to Daml). Full detail: [reference.md](reference.md).

## Current project workflow

- Use `python -m forge daml <file-or-directory> -o <directory>` for source generation. A file input produces one `.daml` file and a `.map.json` source map; a directory input covers only `.vy` files directly in that directory.
- Use `python -m forge dar <project-directory> -o <file.dar>` for a real package build. The project must already have a valid `daml.yaml`; DamlForge recursively compiles `.vy` files below its `source` directory and then invokes DPM.
- Use `python -m forge stubs <file.dar> -o <directory>` to reconstruct supported declarations as `.vy`. Stub generation uses `dazl`, which is installed with DamlForge; a project directory can be supplied instead of a DAR and will be built with DPM.
- Use `daml test --all` when test coverage must include templates and choices imported from data-dependency DARs. Plain `daml test` can report only the package-local definitions.
- DPM is resolved from `PATH`, then `~/.dpm/bin/dpm`; pass `--dpm PATH` when necessary. Add `--traceback` while diagnosing compiler failures. `forge daml --no-i` makes the default error-tolerant multi-file compile fail on the first error.

## Before writing any .vy file

1. Every field/type name in the file must resolve to one of: a Python-native type (including `date`, `datetime`, and `timedelta` imported from Python's `datetime` module), a DamlForge builtin type (`Party`, `Decimal`, `Int`, `ContractId[T]`, `GenMap[K,V]`, `TextMap[T]`), or a `struct`/`template`/`interface`/`flag`/`variant` declared/imported in scope. See reference.md § Type System.
2. Function and method decorators (`@choice`, `@view`, `@variant_record`, `@DamlName`) must be imported from `forge.models` or `forge.builtins`. The `@template` and `@variant` class markers must be imported from `forge.models` too. Ledger operations (`create`, `fetch`, `exercise`, `archive`, the by-key functions, interface conversions) must be imported from `forge.builtins.daml_lang`. See reference.md § Decorators and § Ledger primitives.
3. Every `template` needs `signatory`. `key` requires `maintainer` and vice versa; `key` needs a type annotation, `maintainer` is computed from `key` only (`DF1236`), and keys need SDK 3.5 with `--target=2.3` (`DF1234`). Annotate the result of `fetch_by_key`/`lookup_by_key`/`lookup_all_by_key`/`lookup_n_by_key` so Daml can infer the template; `exercise_by_key(T, key, T.Choice(...))` and `visible_by_key(T, key)` take the template first (`DF1235`). `ensure` (if present) must be a `bool`-typed comparison/boolop/call, never bare arithmetic. See reference.md § Template Fields.
4. Daml update effects are inferred automatically from calls to `fetch`/`exercise`/`create`/`archive`/`*_by_key` and from transitive calls to functions containing those operations. Choices are already update contexts. See reference.md § Ledger Effects.
5. Never put a `def` inside a `struct` - it is silently dropped, no error. Use a free top-level function instead.

Generated stubs may also contain `@no_derive` from `forge.builtins` when the DAR
does not expose the equality/show instances needed for normal DamlForge derivation.
Keep that marker when editing a generated stub unless you intentionally want to
change the generated Daml derivations.

## Quick Reference

| Concept | `.vy` syntax |
|---|---|
| Record type | `struct Foo:` |
| Contract type | `@template\nclass Foo:` |
| Interface | `interface Foo(view=FooView):` |
| Interface impl | `@template\nclass Bar(Foo):` (subclass the interface directly - no `instance` keyword) |
| Payload variant | `@variant\nclass Result:\n    def Ok(value: int): pass` |
| Named-field variant constructor | `@variant_record` on a constructor method; emits Daml `Constructor with ...` |
| Payload-free enum | `flag Status:\n    PENDING\n    PAID` (members MUST be `SCREAMING_CASE`) |
| Choice | `@choice(controller=..., consuming=True)` |
| Fetch/exercise/create/archive | `from forge.builtins.daml_lang import create, fetch, ...` - required, `DF1219` otherwise |
| Optional field | `Optional[X]` - never write `Some(...)`/`from_some(...)` yourself, the compiler inserts it |
| Current ledger time | `datetime.now()` from Python's `datetime`; DamlForge infers the enclosing function as an update |
| Calendar date | `date(year, month, day)` from Python's `datetime`; integer months are `1..12` |
| Date and time | `datetime.combine(date_value, time(hour, minute, second))`; lowers to `DA.Time.time` |
| Add relative time | `timestamp + timedelta(...)`; lowers to `DA.Time.addRelTime` |
| Time difference | `later - earlier`; typed datetimes lower to `DA.Time.subTime` |
| Unix timestamp | `datetime.fromtimestamp(seconds, timezone.utc)`; lowers to `DA.Time.fromUnixMicroseconds` |
| Relative time | `timedelta(days=..., hours=...)` from Python's `datetime`; lowers to `DA.Time` |
| Map | `GenMap[K, V]` / `TextMap[V]`, dict literal `{...}`, `m[k]`, `k in m` |
| Typed for-loop | `for x: T in xs:` (annotation is optional when the iterable type is known) |

For date/time values, import Python's standard types directly:

```python
from datetime import date, datetime, time, timedelta, timezone
```

Never import `Date`, `Timestamp`, or `RelTime` from `forge.models`. They are
represented by `date`, `datetime`, and `timedelta`. Use `datetime.now()` only
in Daml update contexts. See reference.md § Type System for constructors,
arithmetic, and Unix timestamp conversion.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Using `@template`/`@variant`/`@choice`/`@view`/`@variant_record` without importing it | Import it from `forge.models` (`DF1219` otherwise) |
| Calling `create`/`fetch`/`exercise`/`to_interface_contract_id`/... without importing it | Import it from `forge.builtins.daml_lang` (`DF1219` otherwise); only `len`/`range`/`ceil`/`floor` are import-free |
| Using `@variant_record` for every constructor | Use it only for Daml named-field (`with`) constructors; omit it for positional constructors |
| Calling a custom `days(...)` helper | Import `timedelta` from `datetime` and use `timedelta(days=...)`; `years=` is not a Python or Daml fixed-duration unit |
| Writing `def helper(): ...` inside a `struct` | Move it to a top-level `def`; structs are plain data records |
| `controller=`/`observer=` referencing a name that isn't a with-arg, a template field, or `self.<field>` | Only reference the choice's own arguments/fields - anything else either resolves to an unrelated same-named field or fails `damlc` with "Ambiguous occurrence" (`DF1224`) |
| `key` without `maintainer` (or vice versa) | Always declare both together (`DF1210`/`DF1211`) |
| A bare `def` calling `archive(self)`/`fetch(...)` etc. | No marker is needed; DamlForge infers the function as an update |
| `from X import Y as Z` | Aliased from-imports are banned; use `import X as Y` (module alias) instead if disambiguation is needed |
| `dict[K, V]` or a hand-rolled `Map[...]` field | Use `GenMap[K, V]` / `TextMap[V]` |

Full syntax, decorator/builtin tables, type system, error-code index, and a worked example: [reference.md](reference.md).
