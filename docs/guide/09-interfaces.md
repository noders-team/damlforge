# Interfaces

An *interface* is a shared contract type. It declares a view and a set of choices,
and any template can implement it. Code written against the interface works with
every template that implements it, including templates written later or by
another team.

Interfaces are how applications interoperate on Canton Network. The Token Standard
(CIP-0056) defines interfaces such as `Holding` and `TransferInstruction`. A token that
implements them works with any wallet that uses them.

The examples on this page are in `main/daml/Holding.vy` and
`test/daml/HoldingTest.daml`, and the test passes.

## Declaring an interface

```python
from datetime import date

from forge.builtins.daml_lang import (
    create,
    exercise,
    fetch,
    from_interface_contract_id,
    to_interface_contract_id,
)
from forge.models import ContractId, Decimal, Party, choice, template, view


struct HoldingView:
    owner: Party
    amount: Decimal
    description: str


interface Holding(view=HoldingView):
    @choice(controller=view.owner, consuming=True)
    def Holding_Transfer(self, new_owner: Party) -> ContractId[Holding]:
        pass
```

- **`view=HoldingView`** is the interface's *view type*: a struct that every
  implementation must be able to produce. Clients read contracts through it
  without knowing which template they are.
- **Interface choices** are declared with `@choice` and a `pass` body. The
  implementing templates provide the bodies.
- **`controller=view.owner`** refers to the view of the contract the choice is
  exercised on. A controller can also be a choice argument.

```haskell
interface Holding where
  viewtype HoldingView

  holding_TransferImpl : ContractId Holding -> Party -> Update (ContractId Holding)

  choice Holding_Transfer : ContractId Holding
    with
      new_owner : Party
    controller (view this).owner
    do holding_TransferImpl this self new_owner
```

The choice is declared once, on the interface, and calls a method that each
template implements.

## Implementing an interface

A template implements an interface by listing it as a base class, the same way a
Python class subclasses an abstract base class:

```python
@template
class Cash(Holding):
    bank: Party
    owner: Party
    amount: Decimal

    signatory: Party = bank
    observer: Party = owner

    @view
    def holding_view(self) -> HoldingView:
        return HoldingView(owner=self.owner, amount=self.amount, description="cash")

    @choice(controller=view.owner, consuming=True)
    def Holding_Transfer(self, new_owner: Party) -> ContractId[Holding]:
        moved = create(Cash(bank=self.bank, owner=new_owner, amount=self.amount))
        return to_interface_contract_id(moved)
```

- **`@view`** marks the method that builds the view. It's matched to the interface
  by its return type, so a template that implements two interfaces has two `@view`
  methods.
- **A method with the same name as an interface choice** is that choice's
  implementation. Every other choice stays a normal template choice.
- **`to_interface_contract_id`** turns the new `ContractId[Cash]` into a
  `ContractId[Holding]`, the type the interface choice returns.

The template must implement every interface choice and have exactly one matching
`@view` (`DF1228`), and the base must be an interface (`DF1227`).

A second template implements the same interface with different fields:

```python
@template
class Bond(Holding):
    issuer: Party
    owner: Party
    face_value: Decimal
    maturity: date

    signatory: Party = issuer
    observer: Party = owner

    @view
    def holding_view(self) -> HoldingView:
        return HoldingView(owner=self.owner, amount=self.face_value, description="bond")
```

Its `Holding_Transfer` creates a `Bond` the same way `Cash` creates a `Cash`.

## Using an interface

Code that takes `ContractId[Holding]` works with cash, bonds, and any future
holding:

```python
@template
class Wallet:
    owner: Party

    signatory: Party = owner

    @choice(controller=owner, consuming=False)
    def SendAll(
        self, holdings: list[ContractId[Holding]], receiver: Party
    ) -> list[ContractId[Holding]]:
        sent: list[ContractId[Holding]] = []
        for holding in holdings:
            sent.append(exercise(holding, holding.Holding_Transfer(new_owner=receiver)))
        return sent
```

`exercise` runs the interface choice, and Daml dispatches to the implementation
of whichever template the contract is.

To go back from an interface to a template, convert the contract ID with
`from_interface_contract_id`. Annotate the result so Daml knows which template
you expect:

```python
    @choice(controller=owner, consuming=False)
    def CashBank(self, holding: ContractId[Holding]) -> Party:
        cash_id: ContractId[Cash] = from_interface_contract_id(holding)
        cash = fetch(cash_id)
        return cash.bank
```

`fetch` fails if the contract isn't a `Cash`, so `CashBank` fails for a bond.

| `.vy` | Daml | Converts |
|---|---|---|
| `to_interface_contract_id(cid)` | `toInterfaceContractId` | `ContractId[Template]` to `ContractId[Interface]` |
| `from_interface_contract_id(cid)` | `fromInterfaceContractId` | `ContractId[Interface]` to `ContractId[Template]` |
| `to_interface(value)` | `toInterface` | a template value to an interface value |

All three come from `forge.builtins.daml_lang`.

## Testing interfaces

Daml Script converts IDs with the same functions, and reads a contract's view with
`queryInterfaceContractId`:

```haskell
  wallet <- submit alice do
    createCmd Wallet with owner = alice
  [cash_holding, bond_holding] <- submit alice do
    exerciseCmd wallet SendAll with
      holdings = [toInterfaceContractId cash, toInterfaceContractId bond]
      receiver = bob

  Some cash_view <- queryInterfaceContractId bob cash_holding
  assert (cash_view.owner == bob && cash_view.description == "cash")
  Some bond_view <- queryInterfaceContractId bob bond_holding
  assert (bond_view.amount == 1000.0)

  submitMustFail alice do
    exerciseCmd cash_holding Holding_Transfer with new_owner = alice
```

The last step fails because Alice no longer owns the cash: the controller is the
view's owner, now Bob.

```text
daml/HoldingTest.daml:holding_test: ok, 4 active contracts, 8 transactions.
```

## Implementing an interface from an external DAR

The repository also contains a concrete template implementing the Splice Token
Standard `Holding` interface imported from the external
`splice-api-token-holding-v1` DAR:

```text
examples/splice-api-token-burn-mint-v1/daml/Splice/Api/Token/burn_mint_v1.vy
```

`ExternalHolding(Holding)` uses the imported `HoldingView` and is compiled by
`make build-examples` against the pinned Splice DARs. The generated Daml must
contain `interface instance Holding for ExternalHolding`, which is also covered
by `tests/examples/test_token_standard.py`.

## Interfaces and templates in one package

`damlc` warns when an interface and its implementations are in the same package,
because it limits how the package can be upgraded, and the warning fails the
build. For a single package like this one, add the flag to `daml.yaml`:

```yaml
build-options:
  - -Wno-upgrade-interfaces
```

For production code, put the interfaces in their own package so implementations
can be upgraded independently. [Building and Packaging](10-building-packaging.md)
covers splitting a project into packages.

## Next steps

[Building and Packaging](10-building-packaging.md) explains how DamlForge projects
are built into DARs and how packages depend on each other.
