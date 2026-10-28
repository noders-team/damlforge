# Compiler Errors

`forge` checks a `.vy` file in full before generating any Daml. When something is
wrong, it reports a `DF12xx` code, a message, and the exact source location. This
page explains every code: what triggers it, an example that reproduces it, and
how to fix it.

Each example is a complete file that produces the error it's listed under. The
examples assume the usual imports at the top of the file:

```py
from forge.models import ContractId, Decimal, GenMap, Party, choice, template, variant, view
from forge.builtins import DamlName
from forge.builtins.daml_lang import create, fetch
```

## Reading an error

```text
Error processing file Example.vy: DF1225: `add(...)` called with 1 argument, but `add` takes 2 arguments

  contract "Example.vy:9", function "total", line 9:11
       8 def total() -> int:
  ---> 9     return add(1)
  ------------------^
      10
```

| Part | Meaning |
|---|---|
| `DF1225` | The error code. Look it up below |
| `contract "Example.vy:9"` | File and line |
| `function "total"` | The enclosing function or choice, when there is one |
| `line 9:11` | Line and column. The `^` marker points at the column |

`forge` collects every semantic error in a file before stopping, so one run can
report several problems. These appear under `Compilation failed with the
following errors:`, each with its own location. The last block may point at line
1 of the file: that block identifies the module, not an additional error.

`forge daml` keeps going with the remaining files after one fails. Pass `--no-i`
to stop at the first failing file. Add `--traceback` to see the compiler's Python
traceback when you think the error is a compiler bug.

## Error index

| Code | Category | Summary |
|---|---|---|
| [DF1201](#df1201) | Templates | Template has no `signatory` |
| [DF1203](#df1203) | Choices, variants | `@choice` lacks `controller` or a valid `consuming`; a variant has a non-constructor member |
| [DF1205](#df1205) | Decorators | `@template` or `@variant` given arguments |
| [DF1206](#df1206) | Decorators | `@view` given arguments |
| [DF1207](#df1207) | Interfaces | `@view` in a template that implements no interface |
| [DF1208](#df1208) | Interfaces | `preconsuming` or `postconsuming` on an interface choice |
| [DF1209](#df1209) | Functions | A function calls itself |
| [DF1210](#df1210) | Templates | `key` without `maintainer` |
| [DF1211](#df1211) | Templates | `maintainer` without `key` |
| [DF1212](#df1212) | Templates | `ensure` isn't a boolean expression |
| [DF1213](#df1213) | Imports, types | Import can't be resolved or isn't allowed; unknown type name |
| [DF1216](#df1216) | Enums | Unknown enum member |
| [DF1217](#df1217) | Enums | Enum member isn't `SCREAMING_SNAKE_CASE` |
| [DF1218](#df1218) | Choices | Choice name starts with `_` |
| [DF1219](#df1219) | Names | Name, function, or decorator isn't defined or imported |
| [DF1220](#df1220) | Decorators | Unknown decorator |
| [DF1221](#df1221) | Decorators | `@DamlName` without exactly one string literal |
| [DF1222](#df1222) | Types | Type has no Daml equivalent |
| [DF1224](#df1224) | Choices | `controller` or `observer` refers to something out of scope |
| [DF1225](#df1225) | Calls | Wrong number of arguments |
| [DF1226](#df1226) | Calls, types | Argument has the wrong type |
| [DF1227](#df1227) | Interfaces | Template base isn't a known interface |
| [DF1228](#df1228) | Interfaces | Template doesn't implement the whole interface |
| [DF1229](#df1229) | Values, types | Literal out of range, or malformed type arguments |
| [DF1230](#df1230) | Types, control flow | Type mismatch, or a construct Daml can't express |
| [DF1231](#df1231) | Enums | Enum member has an explicit value or is duplicated |
| [DF1232](#df1232) | Declarations | Something is declared twice |
| [DF1233](#df1233) | Templates | Template method that isn't a choice |
| [DF1234](#df1234) | Contract keys | Key declared, but the SDK or LF target can't compile keys |
| [DF1235](#df1235) | Contract keys | `exercise_by_key` or `visible_by_key` not given a template with a key |
| [DF1236](#df1236) | Contract keys | `maintainer` computed from template fields instead of `key` |

## Templates

### DF1201

**Message:** ``Template `Asset` must declare an explicit `signatory` ``

Every template needs at least one signatory: the parties whose authority creates
the contract.

```py
@template
class Asset:
    owner: Party
```

**Fix:** declare `signatory`, either a single `Party` or a `list[Party]`:

```py
    signatory: Party = owner
```

### DF1210

**Message:** ``Template `Asset` declares `key` without a `maintainer` ``

```py
@template
class Asset:
    owner: Party
    tag: str

    signatory: Party = owner
    key: tuple[Party, str] = (owner, tag)
```

**Fix:** a contract key needs maintainers, the parties who are responsible for
the key. Compute them from `key`: `maintainer: Party = key[0]`. See
[DF1236](#df1236).

### DF1211

**Message:** ``Template `Asset` declares `maintainer` without a `key` ``

The reverse of `DF1210`. Either declare the `key` that the maintainers maintain,
or remove `maintainer`.

### DF1212

**Message:** ``Template `Asset` declares `ensure` with a value that isn't a
boolean-shaped expression (comparison, boolean operator, `not`, or a call)``

```py
@template
class Asset:
    owner: Party
    amount: Decimal

    signatory: Party = owner
    ensure: bool = amount + Decimal(1)
```

`ensure` is a precondition checked on every create, so its value must be a
`bool`. A declaration with no value, or an arithmetic expression like the one
above, is rejected. The type checker usually reports `DF1230` (``value has type
`Decimal`, but `Bool` was expected``) alongside it.

**Fix:** use a comparison, a boolean operation, or a call that returns `bool`:

```py
    ensure: bool = amount > Decimal(0)
```

### DF1233

**Message:** ``Template method `Asset.helper` is not supported; decorate it with
`@choice` or move it to module scope``

```py
@template
class Asset:
    owner: Party

    signatory: Party = owner

    def helper(self) -> bool:
        return True
```

A Daml template body holds only fields, clauses, and choices.

**Fix:** if parties should exercise it, make it a `@choice`. Otherwise, move it
to a top-level function that takes what it needs as parameters.

A `key` declared without a type annotation (`key = (owner, tag)`) is reported as
`DF1213`: ``Template `Asset` declares `key` without a type annotation``. Daml needs
the key type, so write `key: tuple[Party, str] = (owner, tag)`.

## Contract keys

These errors apply to the features described in
[Contract Keys](08-contract-keys.md).

### DF1234

**Message:** ``Template `Account` declares a contract key, but they need
`--target=2.3` or later in `build-options`, not the default``

Contract keys compile only with Daml SDK 3.5 or later and Daml-LF target 2.3 or
later. DamlForge reads both from `daml.yaml`. A project on SDK 3.4 gets
``Daml SDK 3.4.11 does not support them; use SDK 3.5 or later`` at the end of the
message instead.

**Fix:** use SDK 3.5 and set the target in `daml.yaml`:

```yaml
sdk-version: 3.5.2
build-options:
  - --target=2.3
```

`forge daml` on a single file outside a project has no `daml.yaml` to read, so it
doesn't check this.

### DF1235

**Message:** ``The first argument of `exercise_by_key` must be a template with a
contract key``

```py
def owner_of(bank: Party, number: str) -> Party:
    return exercise_by_key((bank, number), Account.GetBank())
```

`exercise_by_key` and `visible_by_key` take the template as their first argument,
because Daml can't work out the template from their result. The template must
declare `key` and `maintainer`.

**Fix:** pass the template, then the key:

```py
    return exercise_by_key(Account, (bank, number), Account.GetBank())
```

### DF1236

**Message:** ``Template `Account` must compute `maintainer` from `key` only``

```py
@template
class Account:
    bank: Party
    number: str

    signatory: Party = bank
    key: tuple[Party, str] = (bank, number)
    maintainer: Party = bank
```

Daml evaluates maintainers from the key alone, without the contract, so the
expression can't read template fields.

**Fix:** take the parties out of `key`:

```py
    maintainer: Party = key[0]
```

## Choices

### DF1203

The same code covers three mistakes.

**Message:** ``Choice `Asset.close` must declare an explicit `controller` ``

```py
    @choice(consuming=True)
    def close(self) -> bool:
        return True
```

**Message:** ``Choice `Asset.close` must explicitly declare `consuming` as
True/False or one of ['consuming', 'nonconsuming', 'postconsuming',
'preconsuming']``

```py
    @choice(controller=owner)
    def close(self) -> bool:
        return True
```

**Fix:** always pass both arguments. `consuming` takes a literal `True` or
`False`, or one of the four strings. A variable isn't accepted.

```py
    @choice(controller=owner, consuming=True)
```

**Message:** ``Variant `Result` may contain only constructor declarations``

```py
@variant
class Result:
    def Ok(value: int): pass
    note: int
```

**Fix:** a `@variant` class may contain only constructor methods. Remove the
field, or put the data in a constructor's parameters.

### DF1218

**Message:** ``Choice `Asset._close` starts with `_` - Python's own convention for
a private method, which contradicts `@choice` making it a public,
externally-exercisable Daml choice``

**Fix:** rename the choice so it starts with a letter. Choices are always public
on the ledger.

### DF1224

`controller=` and `observer=` may refer only to the choice's own arguments, the
template's fields, and `self.<field>`. Anything else would make Daml resolve the
name to an unrelated binding, or fail later in `damlc` with "Ambiguous
occurrence".

**Message:** ``observer= of choice `Asset.close` references `self.issuer`, but
`issuer` is not a field of template `Asset` (['owner'])``

```py
@template
class Asset:
    owner: Party

    signatory: Party = owner

    @choice(controller=owner, consuming=True, observer=self.issuer)
    def close(self) -> bool:
        return True
```

**Message:** ``observer= of choice `Asset.close` references `new_owner`, which is
not one of this choice's own arguments ([])``

```py
    @choice(controller=owner, consuming=True)
    def give(self, new_owner: Party) -> bool:
        return True

    @choice(controller=owner, consuming=True, observer=new_owner)
    def close(self) -> bool:
        return True
```

Here `new_owner` belongs to `give`, not `close`. Because the name isn't in scope
at all, `DF1219` is reported with it.

**Fix:** add the party as an argument of the choice that uses it, or refer to a
field that exists. Don't use the template clauses: `self.signatory` isn't a
field.

## Interfaces

### DF1207

**Message:** ``@view on `Asset.get_view` is only valid inside `interface`, or a
`template` that implements one``

```py
struct V:
    owner: Party

@template
class Asset:
    owner: Party

    signatory: Party = owner

    @view
    def get_view(self) -> V:
        return V(owner=self.owner)
```

**Fix:** `@view` supplies an interface's view. List the interface as a base,
`class Asset(SomeInterface):`, or remove the decorator.

### DF1208

**Message:** ``Choice `I.close` declares `consuming="preconsuming"`, which is not
supported on interface choices — only `consuming`/`nonconsuming` are``

```py
struct V:
    owner: Party

interface I(view=V):
    @choice(controller=actor, consuming="preconsuming")
    def close(self, actor: Party) -> bool:
        pass
```

The rule also applies to a template's implementation of an interface choice.

**Fix:** use `consuming=True` or `consuming=False`.

### DF1227

**Message:** ``Template `Asset` declares `Unknown` as a base, but `Unknown` is not
a known `interface` ``

```py
@template
class Asset(Unknown):
    owner: Party

    signatory: Party = owner
```

A template's bases are the interfaces it implements, and each must be a plain
name that resolves to an `interface`. A dotted or computed base gives
``Template `Asset` base must be a plain interface name``.

**Fix:** declare or import the interface, and check its spelling.

### DF1228

**Message:** ``Template `Asset` implements interface `I` but has no `@view` method
returning `V` `` or ``... but does not implement its choice `close` ``

```py
struct V:
    owner: Party

interface I(view=V):
    pass

@template
class Asset(I):
    owner: Party

    signatory: Party = owner
```

**Fix:** a template that implements an interface must provide exactly one `@view`
method that returns the interface's `view=` type, and a method with the same
name as each of the interface's `@choice` declarations.

## Decorators

### DF1205

**Message:** ``@template on `Asset` takes no arguments``

`@template` and `@variant` are bare class markers. In practice, the pre-parser
rejects `@template()` and `@variant()` before this check runs, so you see this
error instead:

```text
The `class` keyword is not allowed.
```

**Fix:** write `@template` or `@variant` with no parentheses, directly above
`class`.

### DF1206

**Message:** ``@view on `Asset.get_view` takes no arguments``

**Fix:** write `@view` with no parentheses.

### DF1220

**Message:** ``Unknown decorator `@Decimal` on `Asset.close` - only ['DamlName',
'checked_fetch', 'choice', 'template', 'variant', 'view', ...] are recognized
decorators``

```py
    @Decimal
    @choice(controller=owner, consuming=True)
    def close(self) -> bool:
        return True
```

DamlForge accepts only its own decorators. A Python decorator such as
`@functools.cache` has no Daml meaning.

**Fix:** remove the decorator. If the name was never imported, `DF1219` is
reported first.

### DF1221

**Message:** ``@DamlName on `Asset.close` takes exactly one string literal
argument``

```py
    @DamlName(1)
    @choice(controller=owner, consuming=True)
    def close(self) -> bool:
        return True
```

**Fix:** pass the Daml name as one string literal: `@DamlName("Asset_Close")`.

## Names and imports

### DF1219

The most common error. A name used in the file isn't bound to anything.

**Message:** ``template is not defined - import the decorator before using it``

```py
from forge.models import Party, choice

@template
class Asset:
    owner: Party

    signatory: Party = owner
```

**Message:** ``create is not defined - no function, import, or struct/template
declaration named `create` exists in scope here``

```py
from forge.models import ContractId, Party, choice, template

def give(owner: Party) -> ContractId[Asset]:
    return create(Asset(owner=owner))
```

**Message:** ``total is not defined - no variable, parameter, import, or
declaration named `total` exists in scope here``

```py
def f(n: int) -> int:
    return total + 1
```

**Message:** ``Asset is a type name, not a variable - no variable named `Asset`
exists in this scope; use `self.owner` to access the current contract's fields``

This appears when you write `Asset.owner` inside a choice of `Asset`.

**Fix:**

| Missing name | Import from |
|---|---|
| `template`, `variant`, `choice`, `view`, `variant_record` | `forge.models` |
| `DamlName`, `no_derive` | `forge.builtins` |
| `create`, `fetch`, `exercise`, `archive`, `lookup_by_key`, `fetch_by_key`, `exercise_by_key`, `visible_by_key`, `lookup_n_by_key`, `lookup_all_by_key`, `to_interface_contract_id`, `from_interface_contract_id`, `to_interface`, `coerce_interface_contract_id` | `forge.builtins.daml_lang` |
| `date`, `datetime`, `time`, `timedelta`, `timezone` | `datetime` |
| `Enum`, `auto` | `enum` |

Only `len`, `range`, `ceil`, and `floor` need no import. For a local variable,
check the spelling and that it's assigned before use. For the current contract,
use `self.<field>`.

### DF1213

Import problems, and unknown type names.

| Message | Cause | Fix |
|---|---|---|
| ``No module named `Missing.Module` - no sibling `.vy` file `Missing/Module.vy` found ...`` | The module isn't a `.vy` file next to this one, a bundled stub, or an importable package | Check the module path. For a Daml package, add its DAR to `data-dependencies` and generate stubs with `forge stubs` |
| ``No name `X` in module `M` `` | The module exists but doesn't export `X` | Check the spelling, or the module the name lives in |
| ``Aliased imports (`Optional as Opt`) are not supported ...`` | `from M import X as Y` | Import `X` directly, or alias the module: `import M as Alias` |
| ``Module alias `alias` must start with an uppercase letter ...`` | `import M as alias` | Use a capitalized alias. Daml module aliases are constructors |
| ``Relative imports are not supported ...`` | `from . import X` | Use the absolute module name |
| ``Import cycle detected: ...`` | Two `.vy` modules import each other | Move the shared declarations into a third module |
| ``M is already imported earlier in this module`` | The same import appears twice | Remove the duplicate |
| ``Imports are only supported at module scope`` | An `import` inside a function | Move it to the top of the file |
| ``No builtin or user-defined type named `dict` ...`` | An annotation names an unknown type, including `dict` | Use `GenMap[K, V]` or `TextMap[V]` for maps, or declare the type |
| ``Variant constructor `Ok` argument `value` requires a type annotation`` | A `@variant` constructor parameter has no type | Annotate it |

Wildcard imports (`from M import *`) are accepted when `M` resolves. An
unresolvable module fails with `DF1213` like any other import.

## Types and values

### DF1222

**Message:** ``bytes has no Daml equivalent and cannot be used as a type (field
`blob` of `Asset`)``

Rejected types are `bytes`, `bytearray`, `memoryview`, `complex`, `type`,
`object`, and `slice`.

**Fix:** use `str` for encoded binary data, such as hex or base64, and the
`Decimal` or `int` types for numbers.

### DF1226

**Message:** ``value has type `Text`, but `Int` was expected``

```py
def double(n: int) -> int:
    return n * 2

def f(label: str) -> int:
    return double(label)
```

An argument passed to one of your functions, or to a choice called through
`cid.choice(...)`, doesn't match the declared parameter type.

**Fix:** pass a value of the declared type, or change the parameter's
annotation. `int` and `Int`, `str` and `Text`, and `float` and `Decimal` are
interchangeable. A plain `T` is accepted where `Optional[T]` is expected.

### DF1229

Values and type shapes that Daml can't represent.

| Message | Cause |
|---|---|
| ``Daml Int literal must be between -9223372036854775808 and 9223372036854775807; got ...`` | An integer literal outside the 64-bit range |
| ``Daml Decimal literal may have at most 10 digits after the decimal point ...`` | A `Decimal` literal with more than 38 digits or more than 10 decimal places |
| ``datetime.timedelta(...) result must be between ... microseconds`` | A `timedelta` too large to fit in 64-bit microseconds |
| ``GenMap expects 2 type argument(s), got 1 ...`` | Wrong number of type arguments, for example `GenMap[int]` or `list[int, str]` |
| ``GenMap keys must be ordered Daml values; `GenMap[int, int]` cannot be used as a key ...`` | A map key or set element type that can't be compared |
| ``datetime.date(...) year 10000 is out of range`` | A literal `date(...)` or `datetime(...)` outside `0001-01-01` to `9999-12-31` |

Constant arithmetic is evaluated at compile time, so `2 ** 64` is caught too. The
full set of bounds is in [Daml value policies](../../forge/semantics/primitive_specs.py).

**Fix:** keep literals within range and give each generic type the right number
of type arguments: `GenMap[K, V]`, `TextMap[V]`, `list[T]`, `Optional[T]`,
`ContractId[T]`.

### DF1230

The type checker's general mismatch error, and the code for constructs Daml
can't express.

**Message:** ``value has type `Int`, but `Text` was expected``

```py
def f(n: int) -> str:
    return n + 1
```

Raised when a value's type doesn't match its context: a return value, an
assignment to an annotated variable, an `if` condition, or the elements of a
list.

**Unsupported constructs:**

| Message | Cause | Fix |
|---|---|---|
| `` `break` is not supported `` / `` `continue` is not supported `` | Daml loops are folds and can't exit early | Use an `if` inside the loop body to skip work |
| `` `raise` is not supported `` | Daml has no exceptions to raise | Use `assert condition, "message"`, which aborts the transaction |
| `Nested function definitions are not supported` | A `def` inside a `def` | Move the inner function to module scope |
| `IR loop with both loop-carried accumulator mutation and an early return is not supported` | A `for` loop that updates a variable and also `return`s from inside the loop | Compute the result in the loop, then return after it |
| ``Ledger effect `create` cannot be used in the pure value of `Asset.field` `` | A template field default or clause that calls `create`, `fetch`, or another ledger operation | Do the ledger operation inside a choice |

```py
def f(xs: list[int]) -> int:
    acc: int = 0
    for x in xs:
        if x < 0:
            return 0
        acc = acc + x
    return acc
```

This loop fails because it both accumulates `acc` and returns early. Track a
flag in the loop and return once, after it.

## Functions and calls

### DF1209

**Message:** ``Function `Example.countdown` is recursive, which is not supported``

```py
def countdown(n: int) -> int:
    return countdown(n - 1)
```

**Fix:** rewrite with a `for` loop over `range(...)` or over a list. The loop
compiles to a fold.

### DF1225

**Message:** ``add(...) called with 1 argument, but `add` takes 2 arguments``

```py
def add(a: int, b: int) -> int:
    return a + b

def f() -> int:
    return add(1)
```

The same code reports invalid calls to the date and time helpers:

| Message | Fix |
|---|---|
| ``datetime.now() does not accept arguments in DamlForge`` | Call `datetime.now()` with no arguments |
| ``datetime.combine(...) requires a date and a time`` | Pass a `date` and a `time(...)` |
| ``datetime.fromtimestamp(...) requires `timezone.utc` `` | Pass `timezone.utc` as the second argument |
| ``datetime.timedelta(...) ...`` | Use only `weeks`, `days`, `hours`, `minutes`, `seconds`, `milliseconds`, and `microseconds` |

## Enums

### DF1216

**Message:** ``CLOSED is not a member of enum `Status` (known members: OPEN)``

**Fix:** use one of the listed members, or add the member to the enum.

### DF1217

**Message:** ``Enum member `Open` of `Status` must be SCREAMING_SNAKE_CASE``

**Fix:** rename the member to `OPEN`.

### DF1231

| Message | Fix |
|---|---|
| ``Enum member `OPEN` must use `auto()`; explicit values are not supported`` | Write `OPEN = auto()` or a bare `OPEN`. Daml enum constructors carry no value |
| ``Enum `Status` members must be bare names or assigned `auto()` `` | Remove methods and other statements from the enum body |
| ``Enum member `OPEN` is declared more than once`` | Remove the duplicate |
| ``Enum member `OPEN` conflicts with enum `Other`; Daml constructors must be unique within a module`` | Rename one member. All constructors in a Daml module share one namespace |

## Duplicate declarations

### DF1232

| Message | Cause |
|---|---|
| ``Function `f` is defined more than once in this module`` | Two top-level `def`s with the same name |
| ``Struct `Bank` declares field `name` more than once`` | A repeated field in a struct or template |
| ``Template `Asset` declares `signatory` more than once`` | A repeated clause: `signatory`, `observer`, `key`, `maintainer`, or `ensure` |

**Fix:** remove or rename the duplicate. Python would let the later definition
win, but DamlForge rejects it.

## Errors without a code

The parser runs before the checks above. A file that doesn't parse produces an
error with no `DF` code:

| Message | Cause | Fix |
|---|---|---|
| ``The `class` keyword is not allowed.`` | A plain `class`, or one under a decorator other than a bare `@template` or `@variant` | Use `struct`, `@template`, `@variant`, `interface`, or `class X(Enum)` |
| ``Invalid syntax (unsupported 'While' Python AST node)`` | A Python statement DamlForge doesn't support, such as `while`, `try`, or `with` | Use `for` loops, `if`, and `assert` |
| ``invalid syntax`` | Code that isn't valid Python | Fix the syntax at the reported line |

## Errors from `damlc`

`forge` checks only what it can see in the `.vy` source. When generated Daml fails
in `dpm build`, `forge dar` prints the `damlc` error and maps it back to the
`.vy` source using the `.map.json` source map:

```text
daml/Fundamentals.daml:86:15: error:
    Variable not in scope: (!!) : TextMap Text -> Text -> Text
ERROR: Creation of DAR file failed.

DamlForge source locations:
  daml/Fundamentals.daml:86:
    DamlForge source daml/Fundamentals.vy:68:1-71:21
```

Read the `damlc` message, then go to the `.vy` lines listed under `DamlForge
source locations`. If valid `.vy` code produces Daml that `damlc` rejects, that's
a compiler bug. Please report it with the `.vy` snippet and the generated `.daml`.

Some `damlc` errors come from the project, not the code:

| `damlc` error | Fix |
|---|---|
| Interface and implementation in the same package cause an upgrade warning-as-error | Add `-Wno-upgrade-interfaces` to `build-options` in `daml.yaml` |
| `Could not find module` for an imported Daml module | Add the package's DAR to `data-dependencies` in `daml.yaml` |
| `Ambiguous occurrence` | Two imported modules export the same name. Use a qualified module import: `import Some.Module as Alias` |

## See also

- [Language Fundamentals](02-language-fundamentals.md) explains the rules these
  errors enforce.
- [Development Environment Setup](01-dev-environment.md) covers `--traceback`,
  `--no-i`, and source maps.
