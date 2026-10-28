# Working with Time

Many workflows depend on time: an offer expires, a payment can't be made before a
date, a voucher is only redeemable during business hours. This module shows how to
read and compare time in `.vy`, which Daml primitive each approach compiles to,
and how to test it with Daml Script.

All examples are in `main/daml/Voucher.vy`, and each is exercised by a Daml Script
test that passes.

## Time types

Use Python's standard `datetime` types directly in `.vy` contracts; do not
import `Date`, `Timestamp`, or `RelTime` from `forge.models`.

`.vy` uses Python's own `datetime` module. Each type maps to a Daml type:

| `.vy` | Daml | Example |
|---|---|---|
| `datetime` | `Time` | `datetime.now()`, `datetime.combine(day, time())` |
| `date` | `Date` | `date(2030, 1, 1)` |
| `timedelta` | `RelTime` | `timedelta(days=1, hours=2)` |

The compiler translates a fixed set of `datetime` constructs, not the whole
module:

| `.vy` | Daml |
|---|---|
| `datetime.now()` | `getTime` |
| `date(2030, 1, 1)` | `date 2030 Jan 1` |
| `datetime.combine(day, time(9, 30))` | `time day 9 30 0` |
| `datetime.fromtimestamp(seconds, timezone.utc)` | `addRelTime` from the Unix epoch |
| `timedelta(days=1, hours=2)` | `days 1 + hours 2` |
| `moment + delta`, `moment - delta` | `addRelTime` |
| `later - earlier` (two `datetime` values) | `subTime`, a `timedelta` |
| `delta + delta`, `-delta` | `RelTime` arithmetic |
| `<`, `>=`, `==`, and the rest | Daml comparisons, on all three types |

`timedelta` accepts `weeks`, `days`, `hours`, `minutes`, `seconds`,
`milliseconds`, and `microseconds`.

For dates, use the helpers from `DA.Date`:

| Python | `.vy` today | Daml |
|---|---|---|
| `moment.date()` | `to_date_utc(moment)` | `toDateUTC` |
| `day.weekday()` | `day_of_week(day)` | `dayOfWeek`, a `DayOfWeek` such as `Monday` |
| `day.year`, `day.month`, `day.day` | `to_gregorian(day)` | `toGregorian`, a `(year, month, day)` tuple |
| `day + timedelta(days=n)` | `add_days(day, n)` | `addDays` |
| `later - earlier` (two `date` values) | `sub_date(later, earlier)` | `subDate`, an `int` |

> **Note:** these forms are rejected with an error, so they're safe to try:
> `date + timedelta` and `date - date` (`DF1230`), `timedelta * n` (`DF1230`), and
> `min`/`max` on times (`DF1219`). Use the table above instead.

> **Warning: not available yet.** The following compile without a DamlForge
> error but generate Daml that `damlc` rejects, so the build fails with a Daml
> error instead of a `DF` code:
>
> | `.vy` | Generated Daml | Why it fails |
> |---|---|---|
> | `now.weekday()` | `now.weekday` | The call loses its parentheses and becomes a field access |
> | `now.date()`, `day.isoweekday()`, `delta.total_seconds()`, `now.timestamp()` | `now.date`, and so on | Same as above |
> | `now.hour`, `day.year`, `delta.days` | `now.hour`, and so on | Daml's `Time`, `Date`, and `RelTime` have no fields |
> | `now.replace(hour=0)` | `now.replace with hour = 0` | Treated as a record update |
> | `datetime(2030, 1, 1, 9, 30)` | `datetime 2030 1 1 9 30` | Daml has no `datetime` function; use `datetime.combine(date(2030, 1, 1), time(9, 30))` |
> | `date.today()`, `datetime.utcnow()`, `date.fromisoformat(...)` | Copied through unchanged | Not translated |
>
> Support for these is planned for a future release. Until then, use the
> `DA.Date` helpers above and `datetime.now()` for the current ledger time.

## Ledger time

A Daml ledger doesn't have a clock that choices can read freely. Each transaction
carries a *ledger time*, chosen when it's submitted, and the ledger checks that it
stays close to the time the transaction is recorded. There are two ways to use
ledger time in a choice:

- **Ledger-time assertions** such as `assert_within_deadline` and
  `is_ledger_time_lt` only check how ledger time compares to a given value. They
  don't limit how long a transaction can take between preparation and submission,
  so they suit workflows where an external party signs the transaction.
- **`datetime.now()`** compiles to Daml's `getTime` and returns the ledger time
  itself. The ledger then only accepts the transaction if it's submitted within
  about a minute of preparation.

Prefer the assertions when a comparison is all you need.

## The voucher

`Voucher.vy` starts with its imports. The time helpers come from Daml standard
library modules:

```python
from datetime import datetime, time, timedelta

from DA.Assert import assert_deadline_exceeded, assert_within_deadline
from DA.Date import day_of_week, to_date_utc
from DA.Date.Types import Saturday, Sunday
from DA.Time import is_ledger_time_ge, is_ledger_time_lt
from forge.builtins.daml_lang import create
from forge.models import ContractId, Decimal, Party, choice, template
```

A voucher is signed by the shop that issued it and its owner. The owner can offer
it to someone else for a limited time:

```python
@template
class Voucher:
    issuer: Party
    owner: Party
    value: Decimal

    signatory: list[Party] = [issuer, owner]

    @choice(controller=owner, consuming=True)
    def Offer(
        self, new_owner: Party, opens_at: datetime, window: timedelta
    ) -> ContractId[VoucherOffer]:
        closes_at = opens_at + window
        return create(
            VoucherOffer(voucher=self, new_owner=new_owner, opens_at=opens_at, closes_at=closes_at)
        )
```

The offer stores two points in time: when it opens and when it closes. Each of
its choices accepts the offer under a different time rule, and each creates the
voucher for its new owner with this helper:

```python
def transferred(voucher: Voucher, new_owner: Party) -> Voucher:
    return Voucher(issuer=voucher.issuer, owner=new_owner, value=voucher.value)
```

## Accept before a deadline

```python
    @choice(controller=new_owner, consuming=True)
    def AcceptBeforeDeadline(self) -> ContractId[Voucher]:
        assert_within_deadline("the offer has expired", self.closes_at)
        return create(transferred(self.voucher, self.new_owner))
```

`assert_within_deadline` fails the transaction with the given message unless the
ledger time is before `closes_at`.

## Accept after a waiting period

```python
    @choice(controller=new_owner, consuming=True)
    def AcceptAfterOpening(self) -> ContractId[Voucher]:
        assert_deadline_exceeded("the offer is not open yet", self.opens_at)
        return create(transferred(self.voucher, self.new_owner))
```

`assert_deadline_exceeded` is the reverse: it fails unless `opens_at` has passed.

## Accept inside a time window

```python
    @choice(controller=new_owner, consuming=True)
    def AcceptInWindow(self) -> ContractId[Voucher]:
        opened = is_ledger_time_ge(self.opens_at)
        still_open = is_ledger_time_lt(self.closes_at)
        assert opened and still_open
        return create(transferred(self.voucher, self.new_owner))
```

`is_ledger_time_ge` and `is_ledger_time_lt` return a `bool` instead of failing, so
they can be combined. They're ledger operations, so each call becomes its own
step in the generated Daml:

```haskell
    choice AcceptInWindow : ContractId Voucher
      controller new_owner
      do
        let self = this
        opened <- isLedgerTimeGE self.opens_at
        still_open <- isLedgerTimeLT self.closes_at
        assert (opened && still_open)
        create (transferred self.voucher self.new_owner)
```

`is_ledger_time_gt` and `is_ledger_time_le` complete the set.

## Read the time with `datetime.now()`

```python
    @choice(controller=new_owner, consuming=True)
    def AcceptWithGetTime(self) -> ContractId[Voucher]:
        assert datetime.now() < self.closes_at
        return create(transferred(self.voucher, self.new_owner))
```

This behaves like `AcceptBeforeDeadline`, but it reads the ledger time with
`getTime`, so the transaction must be submitted within about a minute of
preparation. Use `datetime.now()` when the value itself is needed, as in the next
example.

Any function that calls `datetime.now()` becomes a Daml `Update`, like a function
that calls `create` or `fetch`.

## Business hours

`Redeem` only works on weekdays between 8:00 and 18:00 UTC:

```python
    @choice(controller=owner, consuming=True)
    def Redeem(self) -> None:
        now = datetime.now()
        today = to_date_utc(now)
        assert day_of_week(today) not in [Saturday(), Sunday()]
        since_midnight = now - datetime.combine(today, time())
        assert since_midnight >= timedelta(hours=8) and since_midnight < timedelta(hours=18)
```

```haskell
    choice Redeem : ()
      controller owner
      do
        let self = this
        now <- getTime
        let today = toDateUTC now
        assert (dayOfWeek today `notElem` [Saturday, Sunday])
        let since_midnight = subTime now (time today 0 0 0)
        assert ((since_midnight >= hours 8) && (since_midnight < hours 18))
        pure ()
```

- `to_date_utc` turns a `datetime` into the `date` it falls on.
- `day_of_week` returns a `DayOfWeek`. Its values are constructors, called with no
  arguments: `Saturday()`.
- `datetime.combine(today, time())` is midnight of that day, so subtracting it
  gives the time of day as a `timedelta`.
- Chained comparisons such as `a <= b < c` aren't supported. Use `and`.

## Testing with time

Daml Script controls ledger time in tests. `setTime` jumps to a given time, and
`passTime` moves forward by a `RelTime`. Both need `DA.Time`, and `DA.Date`
provides `date` and the month names:

```haskell
import DA.Date
import DA.Time
import Daml.Script
import Voucher
```

The window test opens an offer an hour from now, fails to accept it too early,
then moves time forward:

```haskell
  setTime (time (date 2026 Oct 26) 9 0 0)
  now <- getTime
  voucher <- issue shop alice
  offer <- submit alice do
    exerciseCmd voucher Offer with
      new_owner = bob
      opens_at = addRelTime now (hours 1)
      window = days 1
  submitMustFail bob do
    exerciseCmd offer AcceptAfterOpening
  passTime (hours 2)
  submit bob do
    exerciseCmd offer AcceptInWindow
```

Once the window has closed, only the waiting-period choice still succeeds:

```haskell
  passTime (hours 2)
  submitMustFail bob do
    exerciseCmd offer AcceptBeforeDeadline
  submitMustFail bob do
    exerciseCmd offer AcceptWithGetTime
  submitMustFail bob do
    exerciseCmd offer AcceptInWindow
  submit bob do
    exerciseCmd offer AcceptAfterOpening
```

The business-hours test tries a Saturday, then 6:00 on a Monday, then 8:00:

```haskell
  setTime (time (date 2026 Oct 24) 12 0 0)
  submitMustFail alice do
    exerciseCmd voucher Redeem
  setTime (time (date 2026 Oct 26) 6 0 0)
  submitMustFail alice do
    exerciseCmd voucher Redeem
  passTime (hours 2)
  submit alice do
    exerciseCmd voucher Redeem
```

```text
daml/VoucherTest.daml:business_hours_test: ok, 0 active contracts, 7 transactions.
daml/VoucherTest.daml:expired_test: ok, 1 active contracts, 8 transactions.
daml/VoucherTest.daml:deadline_test: ok, 1 active contracts, 6 transactions.
```

Ledger time can't move backwards past a transaction that already happened: a
contract can't be used at a time before it was created.

## Next steps

[Contract Keys](08-contract-keys.md) shows how to find a contract by the values it
holds instead of its contract ID.
