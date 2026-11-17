from forge.models import ContractId, Decimal, Party, choice, template, view
from forge.builtins import DamlName
from Splice.Api.Token.HoldingV1 import Holding, HoldingView, InstrumentId
from Splice.Api.Token.MetadataV1 import ChoiceContext, ExtraArgs, Metadata


struct BurnMintOutput:
    owner: Party
    amount: Decimal
    context: ChoiceContext


struct BurnMintFactory_BurnMintResult:
    output_cids: list[ContractId[Holding]]


struct BurnMintFactoryView:
    admin: Party
    meta: Metadata


@template
class ExternalHolding(Holding):
    owner: Party
    instrument_id: InstrumentId
    amount: Decimal

    signatory: list[Party] = [instrument_id.admin, owner]

    @view
    def get_view(self) -> HoldingView:
        return HoldingView(
            owner=self.owner,
            instrument_id=self.instrument_id,
            amount=self.amount,
            lock=None,
            meta=Metadata(values={}),
        )


interface BurnMintFactory(view=BurnMintFactoryView):

    @choice(controller=[expected_admin] + extra_actors, consuming="nonconsuming")
    @DamlName("BurnMintFactory_BurnMint")
    def burn_mint_factory_burn_mint(
        self,
        expected_admin: Party,
        instrument_id: InstrumentId,
        input_holding_cids: list[ContractId[Holding]],
        outputs: list[BurnMintOutput],
        extra_actors: list[Party],
        extra_args: ExtraArgs,
    ) -> BurnMintFactory_BurnMintResult:
        pass

    @choice(controller=actor, consuming="nonconsuming")
    @DamlName("BurnMintFactory_PublicFetch")
    def burn_mint_factory_public_fetch(self, expected_admin: Party, actor: Party) -> BurnMintFactoryView:
        ...
