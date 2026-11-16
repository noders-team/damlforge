# DamlForge `.vy` Language Reference

`.vy` files are tokenized Python, re-keyworded by a pre-parser, then lowered straight to Daml source (`forge/ir`, `forge/codegen`). There is no runtime: nothing in a `.vy` file is ever executed by Python. Compile with:

```
python -m forge daml <file_or_dir> -o <out_dir>   # .vy -> .daml
python -m forge dar <project_dir> -o <out.dar>    # .vy -> .daml -> real .dar via dpm build
python -m forge json <file>                       # .vy -> JSON AST (or read stdin)
python -m forge stubs <file.dar> -o <out_dir>     # DAR -> supported .vy declarations
# `python -m forge <file_or_dir> -o <out_dir>` (no subcommand) is shorthand for `daml`
```

`forge daml` writes both `<Module>.daml` and `<Module>.map.json`. Directory input
compiles only `.vy` files directly in that directory. `forge dar` requires an existing
Daml project with `daml.yaml` fields `name`, `source`, `version`, and a non-empty
`dependencies` list; it recursively compiles `.vy` files below `source`, writes the
generated `.daml` files into that source tree, and runs `dpm build`. DPM is resolved
from `PATH`, then `~/.dpm/bin/dpm`, or can be supplied with `--dpm PATH`.

Use `forge daml --no-i` to fail on the first file error instead of continuing with
the remaining files. Use `--traceback` on any command for the Python traceback.

Stub generation accepts a DAR or a Daml project directory. It uses `dazl` (installed
with DamlForge) for Daml-LF decoding, and `--module MODULE` may be repeated
to select modules from the main package or any dependency. Unsupported declarations
are skipped and reported.

For Daml tests that need coverage from data-dependency packages, run `daml test --all`;
the package-local test mode does not include those external definitions in its report.

## 1. Container keywords

The pre-parser translates these container forms into Python `class` declarations:

| `.vy` keyword | Compiles to | Real Daml shape |
|---|---|---|
| `struct Foo:` | plain data record | `data Foo = Foo with ...` |
| `@template` then `class Foo:` | contract template | `template Foo where ...` |
| `interface Foo(view=V):` | interface | `interface Foo where viewtype V` |
| `@template` then `class Foo(Bar):` | template implementing interface `Bar` | `template Foo with ... where ... interface instance Bar for Foo where ...` |
| `flag Foo:` / `enum Foo:` | sum type of nullary constructors | `data Foo = A \| B \| ...` |
| `@variant` then `class Foo:` | sum type with payload constructors | `data Foo = A Type \| B with ...` |

`@template\nclass Foo:` is the only template spelling; bare `template Foo:` is not supported. Likewise, `@variant\nclass Foo:` is the only spelling for a payload variant. Neither class marker needs an import because the pre-parser consumes it before decorator-name analysis. No other decorator combination with bare `class` is legal - plain `class Foo:` and `@anything_else\nclass Foo:` both raise a hard `SyntaxException` at tokenize time.

`struct`/`interface`/`event`/`enum` (but not `template`/`flag`) are also reserved identifiers - you cannot name a variable or field `struct`, etc. There is no `instance` keyword - a template declares which interface(s) it implements directly via base classes, like real Python subclassing/ABCs (see §11).

`event`/`error` (`EventDef`/`ErrorDef`) parse but are not wired into codegen; the `exception` decorator explicitly raises `NotImplementedError` - none of these three are usable yet.

## 2. Type system

**Native Python types - always available, no import:** `str`, `int`, `float`, `bool`, `None`, `list[T]`, `tuple[T, ...]`, `set[T]`, `frozenset[T]`. `Optional[T]` needs `from typing import Optional` for the file to remain valid-looking Python, but the compiler itself doesn't require the import.

**DamlForge builtin types - recognized unconditionally by the type checker, but conventionally still imported from `forge.models` for readability:** `Party`, `Decimal`, `Int` (a distinct `Int` for real Daml `Int`, vs. Python `int`), `ContractId[T]`, `GenMap[K, V]`, `TextMap[V]`.

```python
from datetime import datetime

from forge.models import ContractId, Decimal, Optional, Party, TextMap, choice
```

**Banned field types:** `dict[...]` and any unrecognized generic like `Map[...]` (`DF1213` - use `GenMap`/`TextMap`), and `bytes`/`bytearray`/`memoryview`/`complex`/`type`/`object`/`slice` (`DF1222` - no Daml equivalent).

**Type synonyms the checker treats as equal** when checking call-argument compatibility: `int`↔`Int`, `str`↔`Text`, `float`↔`Decimal`. A bare `X` is compatible where `Optional[X]` is expected (auto-wrapped, see §6). Generic args are checked recursively - `list[A]` is never compatible with `list[B]` unless `A`/`B` match.

**Your own types**: any `struct`/`template`/`interface`/`flag` declared in a reachable module is automatically a valid type name - no import needed within the same module, real `from X import Y` needed across `.vy` files.

Python's `datetime`, `date`, and `timedelta` map to Daml `Time`, `Date`, and
`RelTime`:

```python
from datetime import date, datetime, time, timedelta, timezone
def current_time() -> datetime:
    return datetime.now()

def epoch() -> date:
    return date(1970, 1, 1)

def start_of_epoch() -> datetime:
    return datetime.combine(epoch(), time())

def cooldown(months: int) -> timedelta:
    return timedelta(days=30 * months, hours=12)

def deadline(start: datetime) -> datetime:
    return start + timedelta(days=7)

def elapsed(later: datetime, earlier: datetime) -> timedelta:
    return later - earlier

def from_seconds(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)
```

`datetime.now()` lowers to the Daml update operation `getTime`; DamlForge
automatically infers the enclosing function as an update. `date(...)` accepts
Python's integer month numbering (`1` through `12`) and automatically imports
`DA.Date`. `datetime.combine(date_value, time(...))` lowers to DAML's pure
`DA.Time.time`; a standalone `time(...)` has no DAML equivalent.

Adding a `timedelta` to a `datetime` uses normal Python arithmetic and lowers to
DAML `addRelTime`; subtracting two datetimes lowers to `subTime`.
`datetime.fromtimestamp(...)` requires an explicit `timezone.utc` and lowers
seconds to DAML's microsecond-based `fromUnixMicroseconds`.

The compiler supports Python's fixed units: `weeks`, `days`, `hours`, `minutes`,
`seconds`, `milliseconds`, and `microseconds`. Weeks lower to seven days. Python
does not support `years=` because a calendar year is not a fixed duration; use
explicit date/calendar logic when that distinction matters.

**Qualified module imports** - use when two real Daml packages export a same-named type (Daml itself would raise "Ambiguous occurrence" on an unqualified double-import):

```python
import Splice.Api.FeaturedAppRightV1 as FeaturedAppRightV1
import Splice.Api.FeaturedAppRightV2 as FeaturedAppRightV2
...
def f(x: FeaturedAppRightV1.FeaturedAppRight): ...
```

`from X import Y as Z` (aliased *from*-import) is banned outright, as is `from X import *`, importing the same name twice, and importing a module/name that doesn't actually resolve - all raise `DF1213`.

## 3. Decorators

Function and method decorators are ordinary Python objects and must be imported before use. An unimported decorator fails like any other unresolved name (`DF1219`, "not defined"). This includes the `@template` and `@variant` class markers, which are imported from `forge.models`.

```python
from forge.models import choice, variant_record, view
from forge.builtins import DamlName
```

| Decorator | Where legal | Rules |
|---|---|---|
| `@choice(controller=..., consuming=..., observer=...)` | method inside `template`/`interface` | `controller` required (`DF1203`); `consuming` required, must be a literal `True`/`False` or one of `"preconsuming"`/`"consuming"`/`"nonconsuming"`/`"postconsuming"` (`DF1203`); name can't start with `_` (`DF1218`); `"preconsuming"`/`"postconsuming"` banned on interface choice declarations, and on a template's implementation of an interface choice (`DF1208`) |
| `@view` | method inside `interface`, or a `template` that implements one | no args (`DF1206`); banned inside a `template` with no interface bases (`DF1207`) |
| `@variant_record` | constructor method inside an `@variant` class | bare only; emits a named-field Daml constructor using `with` instead of a positional payload |
| `@DamlName("RealName")` | any function/choice | exactly one string-literal positional arg (`DF1221`); overrides the auto-derived Daml-side name |
| `@template` | immediately before `class Foo:`, nothing else | class marker imported from `forge.models`; see §1 |
| `@variant` | immediately before `class Foo:`, nothing else | class marker imported from `forge.models`; see §1 and §9 |

### `controller=`/`observer=` name rules (`DF1224`)

Every bare name inside a choice's `controller=`/`observer=` expression must be one of: that choice's own `with`-arguments, one of the template's own declared fields, or `self`. `self.<attr>` is further checked to be a real field (not `self.signatory` - `signatory` is a Daml typeclass function, not a stored field). This exists because Daml's own name resolution would otherwise silently pick an unrelated same-named field elsewhere in the module, or fail with a confusing `damlc`-time "Ambiguous occurrence" error. This check runs for `template` choices, including a template's implementation of an interface choice (since it lives directly in the template body and has real field access) - a bare `interface`'s own abstract choice declarations are skipped (they have no fields, no `self`, only their own with-arguments).

```python
@choice(controller=owner, consuming=True)                 # owner: template field - OK
def transfer(self, new_owner: Party) -> bool: ...

@choice(controller=owner, consuming=True, observer=new_owner)   # new_owner is `transfer`'s
def rename(self, label: str) -> bool: ...                       # arg, not `rename`'s - DF1224
```

## 4. Ledger primitives - must be imported

`fetch`, `exercise`, `create`, `archive`, `lookup_by_key`, `fetch_by_key`, `exercise_by_key`, `visible_by_key`, `lookup_n_by_key`, `lookup_all_by_key`, `view` (interface view extraction, distinct from the `@view` decorator), `from_interface_contract_id`, `to_interface_contract_id`, `to_interface`, `coerce_interface_contract_id`, `stakeholder`, `error`, `reverse` (every `@DamlName` function in `forge/builtins/daml_lang.py`) must be imported from `forge.builtins.daml_lang`. Calling one without importing it fails with `DF1219` ("not defined"). Only `len`/`range`/`ceil`/`floor` are import-free.

```python
from forge.builtins.daml_lang import fetch

def f(cid: ContractId[Asset]) -> Asset:
    return fetch(cid)
```

Inside an `interface` body, a bare `view` in `controller=`/`observer=` (for example `controller=view.provider`) is the interface's own view value, bound by the compiler like `self`. It needs no import.

### Ledger-effect inference

DamlForge automatically infers a plain function as an update when its body
calls an update operation such as `fetch`, `exercise`, `create`, `archive`, or
`fetch_by_key`. The inference is transitive, so a function that calls another
inferred update function is also emitted with an update return type:

```python
def replace(new_owner: Party) -> bool:
    archive(self)
```

```python
def move(new_owner: Party) -> bool:
    replace(new_owner)     # also inferred as an update
```

Choice bodies are already update contexts and may call ledger operations
directly. Imported Daml update helpers and declaration-only stubs carry
compiler metadata so inference can recognize them even though their `.vy`
declarations have no body to inspect.

## 5. Template fields

```python
@template
class Asset:
    owner: Party
    asset_id: Text

    signatory: list[Party] = [owner]        # required (DF1201 if missing)
    observer: list[Party] = [...]           # optional
    key: tuple[Party, Text] = (owner, asset_id)   # optional, but...
    maintainer: list[Party] = [owner]             # ...requires key, and vice versa (DF1210/DF1211)
    ensure: bool = amount > Decimal(0)      # optional; must be bool-typed comparison/boolop/call
                                             # (not bare arithmetic, not missing a value - DF1212)
```

`signatory`/`observer`/`key`/`maintainer`/`ensure` are special clause names, not ordinary fields - don't reference them via `self.signatory` etc. in choice logic (the compiler flags this as `DF1224`).

## 6. `Optional`, `Some`, auto-unwrapping

Never write `Some(...)` or `from_some(...)` by hand. Based on a field/return's declared `Optional[X]` type, the compiler automatically wraps a plain `X` value into `Some(X)` on assignment/return, and unwraps `Optional[X]` into `X` where a plain `X` is required. Just write the plain values:

```python
lock: Optional[Lock] = None
...
return HoldingView(..., lock=None, ...)     # compiler emits `None`
return HoldingView(..., lock=existing_lock, ...)   # if existing_lock: Lock, compiler emits `Some existing_lock`
```

## 7. Maps

```python
from forge.models import GenMap, TextMap

balances: GenMap[Party, Decimal] = {alice: Decimal(0), bob: Decimal(0)}
values: TextMap[str] = {}

balances[alice]                 # lookup
balances[alice] = Decimal(5)    # insert/update
alice in balances               # membership
```

`GenMap`/`TextMap` also expose `.insert(k, v)`, `.lookup(k)`, `.member(k)`, `.delete(k)`, `.keys()`, `.values()` if you prefer explicit method calls over subscript/`in` sugar.

## 8. Flags (sum types)

```python
flag PeriodPaymentStatus:
    PERIOD_PENDING
    PERIOD_PAID
```

Members **must** be `SCREAMING_CASE` (`DF1217` on lowercase or mixed case). Access via `PeriodPaymentStatus.PERIOD_PAID`, compare with `==`/`!=`. Flags have no payload and no methods.

## 9. Variants (payload sum types)

Use `@variant` when constructors carry values. Each method declares a
constructor and its parameters declare the payload:

```python
from datetime import datetime

from forge.models import Party, variant_record

@variant
class Result:
    def Accepted(value: Party): pass
    def Cancelled(): pass

    @variant_record
    def Deferred(reason: str, retry_at: datetime): pass
```

This generates:

```daml
data Result
  = Accepted Party
  | Cancelled
  | Deferred with
      reason : Text
      retry_at : Time
  deriving (Eq, Show, Serializable)
```

An ordinary constructor method is positional: its parameter names are not part
of the generated Daml API. Mark a constructor with `@variant_record` when the
original Daml shape uses named `with` fields and those field names must be
preserved. This distinction applies even to a single-field constructor. A
constructor with no parameters needs no `@variant_record`.

Constructor methods are declarations only and should have `pass` bodies.
`@variant` and `@variant_record` must both be imported from
`forge.models`.

## 10. Structs

Plain data records - fields only:

```python
struct InstrumentId:
    admin: Party
    id: str
```

**No methods.** A `def` inside a `struct` body is currently silently dropped (no error raised) - this mirrors a real gap relative to Vyper, which explicitly rejects struct methods with `StructureException`. Treat "structs can't have methods" as a hard rule and use a free top-level `def` instead:

```python
def update_milestone_if_due(paid_amount: Decimal, milestone: RepaymentMilestone) -> RepaymentMilestone:
    if milestone.paid or paid_amount < milestone.amount:
        return milestone
    return RepaymentMilestone(due_date=milestone.due_date, amount=milestone.amount,
                               paid=True, paid_date=milestone.paid_date)
```

## 11. Interfaces - a template implements one via real subclassing

There is no `instance` keyword. A `template` declares it implements an `interface` the same way Python declares a class implements an ABC - by listing it as a base:

```python
struct HoldingView:
    owner: Party

interface Holding(view=HoldingView):
    @choice(controller=actors, consuming="nonconsuming")
    def withdraw(self, actors: list[Party], extra_args: ExtraArgs) -> bool:
        pass                      # interface choices are declared once, with `pass` bodies

@template
class SimpleHolding(Holding):
    owner: Party
    issuer: Party
    signatory: list[Party] = [owner, issuer]

    @view
    def get_view(self) -> HoldingView:
        return HoldingView(owner=self.owner)

    @choice(controller=actors, consuming="nonconsuming")
    def withdraw(self, actors: list[Party], extra_args: ExtraArgs) -> bool:
        ...                       # real implementation - same field/self access as any other choice
```

Any method inside the template whose name matches one of the interface's own `@choice`-declared names is treated as that interface's implementation (not a separate template choice); everything else stays a normal template choice. A template can implement more than one interface (`@template` followed by `class Foo(A, B):`) - a `@view` method is matched to the right interface by its return type, so implementing two interfaces needs two `@view` methods, one per interface's declared `view=` type.

**Compile-time completeness check (`DF1228`)**: a template that lists an interface as a base must implement every one of that interface's `@choice`-declared members by name, and have exactly one `@view` method whose return type matches the interface's `view=` type - both are checked before codegen, not left to fail later inside `damlc`. The base itself must resolve to a real `interface` declaration (`DF1227` otherwise).

Under the hood, this compiles to Daml's real interface-choice-delegation mechanism: the `interface` emits the choice once plus an abstract "impl" method signature, and each implementing `template`'s `interface instance ... for ... where` block provides just that method's body - matching how real Daml (and the Splice token standard) actually structures this, not a per-template redeclared choice.

If you build interface+implementation in the same `.vy` module (the common case for a self-contained example), add `-Wno-upgrade-interfaces` to your project's `daml.yaml` `build-options` - real Daml warns-as-error about interfaces and implementations sharing a package, since it affects upgradability; this is a project-level Daml build setting, not something `.vy` can express.

## 12. Functions, loops, calls

Free top-level functions are plain `def`s; call-argument arity (`DF1225`) and type-compatibility (`DF1226`, only for `Name` arguments with a known declared type) are checked for any call to a known top-level `.vy` function.

Direct self-recursion is banned (`DF1209`) - a function/choice cannot call itself.

For-loops may include a type annotation on the loop variable before `in` (this is `.vy`-specific sugar the pre-parser slurps out, since bare PEP-526 annotations aren't legal there in real Python). The annotation is optional when the iterable type is known:

```python
for milestone: RepaymentMilestone in milestones:
    ...
for _index: int in range(0, count):
    ...
```

`len`, `range`, `ceil`, `floor` are always-available builtins (no import) and rename on the Daml side (`len`→`length`, `ceil`→`ceiling`; `floor`/`range` pass through).

## 13. Error code index

| Code | Meaning |
|---|---|
| DF1201 | template missing `signatory` |
| DF1203 | `@choice` missing `controller` or missing/invalid `consuming` |
| DF1206 | `@view` given arguments |
| DF1207 | `@view` used inside a `template` with no interface base |
| DF1208 | `"preconsuming"`/`"postconsuming"` on an interface choice declaration or implementation |
| DF1209 | direct self-recursion |
| DF1210 | `key` without `maintainer` |
| DF1211 | `maintainer` without `key` |
| DF1212 | `ensure` missing a value, or not a bool-typed comparison/boolop/call |
| DF1213 | import errors (wildcard, aliased from-import, duplicate, unresolvable) or a banned field type (`dict`, unknown map) |
| DF1216 | unknown flag member accessed |
| DF1217 | flag member not `SCREAMING_CASE` |
| DF1218 | choice name starts with `_` |
| DF1219 | name not defined / not in scope |
| DF1220 | unknown/unrecognized decorator |
| DF1221 | `@DamlName` given the wrong argument shape |
| DF1222 | field type with no Daml equivalent (`bytes`, `complex`, ...) |
| DF1224 | `controller=`/`observer=` references a name that isn't a with-arg/field/`self` |
| DF1225 | wrong argument count on a known function call |
| DF1226 | argument type incompatible with a known function's declared parameter type |
| DF1227 | a `template`'s interface base isn't a plain name, or doesn't resolve to a known `interface` |
| DF1228 | a `template` implementing an interface is missing a required choice or `@view` implementation |

## 14. Worked example

```python
from datetime import datetime

from forge.models import ContractId, Decimal, Optional, Party, TextMap, choice, view
from forge.builtins.daml_lang import archive, create

struct InstrumentId:
    admin: Party
    id: str

struct Metadata:
    values: TextMap[str]

struct HoldingView:
    owner: Party
    instrument_id: InstrumentId
    amount: Decimal
    meta: Metadata

interface Holding(view=HoldingView):
    pass

@template
class SimpleHolding(Holding):
    owner: Party
    issuer: Party
    amount: Decimal
    instrument_id: InstrumentId

    signatory: list[Party] = [owner, issuer]

    @view
    def get_view(self) -> HoldingView:
        return HoldingView(
            owner=self.owner,
            instrument_id=self.instrument_id,
            amount=self.amount,
            meta=Metadata(values={}),
        )

@template
class PendingTransfer:
    sender: Party
    receiver: Party
    amount: Decimal
    instrument_id: InstrumentId

    signatory: list[Party] = [sender]
    observer: list[Party] = [receiver]

    @choice(controller=receiver, consuming=True)
    def accept(self) -> ContractId[SimpleHolding]:
        assert self.amount > Decimal(0), "transfer amount must be positive"
        return create(SimpleHolding(
            owner=self.receiver,
            issuer=self.sender,
            amount=self.amount,
            instrument_id=self.instrument_id,
        ))
```

Full source: `examples/token_standard/token_standard.vy` (verified against real `dpm build`). `examples/crowd_loans/` is the largest real example, exercising `flag`, `GenMap`, nested `for` loops, and cross-package Splice imports.
