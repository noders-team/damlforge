from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional

from forge.builtins import DamlName
from forge.builtins.daml_lang import (
    create,
    error,
    exercise,
    reverse,
    view,
)
from forge.models import (
    ContractId,
    Party,
    choice,
    template,
    variant,
    variant_record,
)
import Splice.Api.Token.AllocationInstructionV1 as AllocationInstructionV1
import Splice.Api.Token.AllocationInstructionV2 as AllocationInstructionV2
import Splice.Api.Token.AllocationRequestV2 as AllocationRequestV2
import Splice.Api.Token.AllocationV1 as AllocationV1
import Splice.Api.Token.AllocationV2 as AllocationV2
import Splice.Api.Token.TransferInstructionV1 as TransferInstructionV1
import Splice.Api.Token.TransferInstructionV2 as TransferInstructionV2

from Splice.Amulet import Amulet, FeaturedAppRight, FeaturedAppRight_Cancel
from Splice.Amulet.TokenApiUtils import (
    amulet_instrument_id,
    amulet_instrument_id_name,
    amulet_instrument_id_v2,
)
from Splice.AmuletRules import (
    AmuletRules,
    AmuletRules_BuyMemberTraffic,
    AmuletRules_CreateExternalPartySetupProposal,
    AmuletRules_DevNet_FeatureApp,
    AmuletRules_DevNet_Tap,
    AmuletRules_Fetch,
    ExternalPartySetupProposal,
    InputAmulet,
    InvalidTransferReason,
    PaymentTransferContext,
    Transfer,
    TransferContext,
    TransferInput,
    TransferPreapproval,
    TransferPreapproval_Renew,
    TransferPreapproval_Send,
    TransferResult,
    compute_synchronizer_fees,
    exercise_payment_transfer,
)
from Splice.DecentralizedSynchronizer import ForMemberTraffic, MemberTraffic
from Splice.Api.Token.MetadataV1 import (
    AnyContractId,
    AnyValue,
    av_contract_id,
    ChoiceContext,
    ExtraArgs,
    Metadata,
)
from Splice.TokenStandard.Utils import (
    basic_account,
    check_actors,
    require_match_expected,
)
from Splice.Types import ForDso
from Splice.Util import (
    fetch_and_archive,
    fetch_checked_interface,
    fetch_public_reference_data,
    fetch_unchecked_but_archive_later,
)
from Splice.Wallet.Install import (
    co_accept_transfer_preapproval_proposal,
    co_app_payment,
    co_buy_member_traffic,
    co_complete_accepted_transfer,
    co_complete_buy_traffic_request,
    co_create_external_party_setup_proposal,
    co_merge_transfer_inputs,
    co_renew_transfer_preapproval,
    co_subscription_accept_and_make_initial_payment,
    co_subscription_make_payment,
    co_tap,
    co_transfer_preapproval_send,
    ext_amulet_operation,
)
from Splice.Wallet.Payment import (
    AcceptedAppPayment,
    AppPaymentRequest,
    AppPaymentRequest_Accept,
    AppPaymentRequest_Expire,
    AppPaymentRequest_Reject,
    PaymentAmount,
    TerminatedAppPayment,
)
from Splice.Wallet.Subscriptions import (
    SubscriptionIdleState,
    SubscriptionIdleState_CancelSubscription,
    SubscriptionIdleState_MakePayment,
    SubscriptionInitialPayment,
    SubscriptionPayment,
    SubscriptionRequest,
    SubscriptionRequest_AcceptAndMakePayment,
    SubscriptionRequest_Reject,
    TerminatedSubscription,
)
from buy_traffic_request import (
    BuyTrafficRequest,
    BuyTrafficRequest_Cancel,
    BuyTrafficRequest_Complete,
    BuyTrafficRequest_CompleteResult,
    BuyTrafficRequest_Expire,
    BuyTrafficRequestTrackingInfo,
)
from top_up_state import ValidatorTopUpState
from transfer_offer import (
    AcceptedTransferOffer,
    AcceptedTransferOffer_Abort,
    AcceptedTransferOffer_Complete,
    AcceptedTransferOffer_CompleteResult,
    AcceptedTransferOffer_Expire,
    AcceptedTransferOffer_Withdraw,
    TransferOffer,
    TransferOffer_Accept,
    TransferOffer_Expire,
    TransferOffer_Reject,
    TransferOffer_Withdraw,
    TransferOfferTrackingInfo,
)
from transfer_preapproval import (
    TransferPreapprovalProposal,
    TransferPreapprovalProposal_Accept,
)


struct ExecutionContext:
    dso: Party
    endUser: Party
    validator: Party
    paymentContext: PaymentTransferContext


@variant
class AmuletOperation:
    def CO_AppPayment(cid: ContractId[AppPaymentRequest]): pass
    def CO_CompleteAcceptedTransfer(cid: ContractId[AcceptedTransferOffer]): pass
    def CO_SubscriptionAcceptAndMakeInitialPayment(cid: ContractId[SubscriptionRequest]): pass
    def CO_SubscriptionMakePayment(cid: ContractId[SubscriptionIdleState]): pass
    def CO_MergeTransferInputs(): pass

    @variant_record
    def CO_BuyMemberTraffic(
        trafficAmount: int,
        memberId: str,
        synchronizerId: str,
        migrationId: int,
        minTopupInterval: timedelta,
        topupStateCid: Optional[ContractId[ValidatorTopUpState]],
    ): pass

    @variant_record
    def CO_CompleteBuyTrafficRequest(trafficRequestCid: ContractId[BuyTrafficRequest]): pass

    @variant_record
    def CO_Tap(tapAmount: Decimal): pass

    @variant_record
    def ExtAmuletOperation(dummyUnitField: None): pass

    @variant_record
    def CO_CreateExternalPartySetupProposal(
        externalParty: Party, preapprovalExpiresAt: datetime
    ): pass

    @variant_record
    def CO_AcceptTransferPreapprovalProposal(
        preapprovalProposalCid: ContractId[TransferPreapprovalProposal],
        expiresAt: datetime,
    ): pass

    @variant_record
    def CO_RenewTransferPreapproval(
        previousApprovalCid: ContractId[TransferPreapproval],
        newExpiresAt: datetime,
    ): pass

    @variant_record
    def CO_TransferPreapprovalSend(
        transferPreapprovalCid: ContractId[TransferPreapproval],
        providerFeaturedAppRightCid: Optional[ContractId[FeaturedAppRight]],
        amount: Decimal,
        description: Optional[str],
    ): pass


@variant
class AmuletOperationOutcome:
    def COO_AcceptedAppPayment(value: ContractId[AcceptedAppPayment]): pass
    def COO_CompleteAcceptedTransfer(value: tuple[TransferResult, TransferOfferTrackingInfo]): pass
    def COO_SubscriptionInitialPayment(value: ContractId[SubscriptionInitialPayment]): pass
    def COO_SubscriptionPayment(value: ContractId[SubscriptionPayment]): pass
    def COO_MergeTransferInputs(value: Optional[ContractId[Amulet]]): pass
    def COO_BuyMemberTraffic(value: ContractId[MemberTraffic]): pass
    def COO_CompleteBuyTrafficRequest(value: tuple[ContractId[MemberTraffic], BuyTrafficRequestTrackingInfo]): pass
    def COO_Tap(value: ContractId[Amulet]): pass
    def COO_Error(value: InvalidTransferReason): pass

    @variant_record
    def ExtAmuletOperationOutcome(dummyUnitField: None): pass

    def COO_CreateExternalPartySetupProposal(value: ContractId[ExternalPartySetupProposal]): pass
    def COO_AcceptTransferPreapprovalProposal(value: ContractId[TransferPreapproval]): pass
    def COO_RenewTransferPreapproval(value: ContractId[TransferPreapproval]): pass
    def COO_TransferPreapprovalSend(value: Optional[ContractId[Amulet]]): pass


def optional_amulet_inputs(
    amulet: Optional[ContractId[Amulet]],
) -> list[TransferInput]:
    if amulet is None:
        return []
    cid: ContractId[Amulet] = amulet
    return [InputAmulet(cid)]


def payment_context_with_featured_app_right(
    context: PaymentTransferContext,
    featuredAppRight: Optional[ContractId[FeaturedAppRight]],
) -> PaymentTransferContext:
    return PaymentTransferContext(
        amulet_rules=context.amulet_rules,
        context=TransferContext(
            open_mining_round=context.context.open_mining_round,
            issuing_mining_rounds=context.context.issuing_mining_rounds,
            validator_rights=context.context.validator_rights,
            featured_app_right=featuredAppRight,
        ),
    )


def mkMergeAmuletAndRewardsTransfer(
    endUserP: Party, inputs: list[TransferInput]
) -> Transfer:
    return Transfer(
        sender=endUserP,
        provider=endUserP,
        inputs=inputs,
        outputs=[],
        beneficiaries=None,
    )


def buy_member_traffic(
    executionContext: ExecutionContext,
    inputs: list[TransferInput],
    trafficAmount: int,
    memberId: str,
    synchronizerId: str,
    migrationId: int,
    minTopupInterval: timedelta,
    topupStateCid: Optional[ContractId[ValidatorTopUpState]],
) -> tuple[ContractId[MemberTraffic], Optional[ContractId[Amulet]]]:
    now = datetime.now()
    if topupStateCid is not None:
        state_cid: ContractId[ValidatorTopUpState] = topupStateCid
        state: ValidatorTopUpState = fetch_and_archive(
            ForMemberTraffic(
                dso=executionContext.dso,
                member_id=memberId,
                synchronizer_id=synchronizerId,
                migration_id=migrationId,
            ),
            state_cid,
        )
        assert executionContext.validator == state.validator, "validator party and ValidatorTopUpState match"
        assert state.lastPurchasedAt + minTopupInterval <= now, "enough time has elapsed since the last topup"
        create(
            ValidatorTopUpState(
                dso=state.dso,
                validator=state.validator,
                memberId=state.memberId,
                synchronizerId=state.synchronizerId,
                migrationId=state.migrationId,
                lastPurchasedAt=now,
            )
        )

    context = executionContext.paymentContext
    rules: AmuletRules = fetch_public_reference_data(
        ForDso(dso=executionContext.dso),
        context.amulet_rules,
        AmuletRules_Fetch(p=executionContext.validator),
    )
    purchase_inputs: list[TransferInput] = inputs
    if rules.is_dev_net and topupStateCid is not None:
        fees = compute_synchronizer_fees(
            executionContext.dso,
            executionContext.validator,
            trafficAmount,
            rules,
            context.context,
        )
        tap_result = exercise(
            context.amulet_rules,
            AmuletRules_DevNet_Tap(
                receiver=executionContext.validator,
                amount=fees[0],
                open_round=context.context.open_mining_round,
            ),
        )
        purchase_inputs = [InputAmulet(tap_result.amulet_sum.amulet)]

    result = exercise(
        context.amulet_rules,
        AmuletRules_BuyMemberTraffic(
            inputs=purchase_inputs,
            context=context.context,
            provider=executionContext.validator,
            member_id=memberId,
            synchronizer_id=synchronizerId,
            migration_id=migrationId,
            traffic_amount=trafficAmount,
            expected_dso=executionContext.dso,
        ),
    )
    return (result.purchased_traffic, result.sender_change_amulet)


def executeAmuletOperationRec(
    executionContext: ExecutionContext,
    inputs: list[TransferInput],
    prevResults: list[AmuletOperationOutcome],
    operations: list[AmuletOperation],
) -> list[AmuletOperationOutcome]:
    current_inputs: list[TransferInput] = inputs
    outcomes: list[AmuletOperationOutcome] = prevResults

    for operation: AmuletOperation in operations:
        app_payment = co_app_payment(operation)
        if app_payment is not None:
            cid: ContractId[AppPaymentRequest] = app_payment
            result = exercise(
                cid,
                AppPaymentRequest_Accept(
                    inputs=current_inputs,
                    context=executionContext.paymentContext,
                    wallet_provider=executionContext.validator,
                ),
            )
            current_inputs = optional_amulet_inputs(result.sender_change_amulet)
            outcomes = [COO_AcceptedAppPayment(result.accepted_payment)] + outcomes
        else:
            accepted_transfer = co_complete_accepted_transfer(operation)
            if accepted_transfer is not None:
                cid: ContractId[AcceptedTransferOffer] = accepted_transfer
                result = exercise(
                    cid,
                    AcceptedTransferOffer_Complete(
                        inputs=current_inputs,
                        transfer_context=executionContext.paymentContext,
                        wallet_provider=executionContext.validator,
                    ),
                )
                current_inputs = optional_amulet_inputs(result.senderChangeAmulet)
                outcomes = [
                    COO_CompleteAcceptedTransfer(
                        (result.transferResult, result.trackingInfo)
                    )
                ] + outcomes
            else:
                subscription_request = co_subscription_accept_and_make_initial_payment(operation)
                if subscription_request is not None:
                    cid: ContractId[SubscriptionRequest] = subscription_request
                    result = exercise(
                        cid,
                        SubscriptionRequest_AcceptAndMakePayment(
                            inputs=current_inputs,
                            context=executionContext.paymentContext,
                            wallet_provider=executionContext.validator,
                        ),
                    )
                    current_inputs = optional_amulet_inputs(result.sender_change)
                    outcomes = [
                        COO_SubscriptionInitialPayment(result.subscription_payment)
                    ] + outcomes
                else:
                    subscription_state = co_subscription_make_payment(operation)
                    if subscription_state is not None:
                        cid: ContractId[SubscriptionIdleState] = subscription_state
                        result = exercise(
                            cid,
                            SubscriptionIdleState_MakePayment(
                                inputs=current_inputs,
                                context=executionContext.paymentContext,
                                wallet_provider=executionContext.validator,
                            ),
                        )
                        current_inputs = optional_amulet_inputs(result.sender_change)
                        outcomes = [
                            COO_SubscriptionPayment(result.subscription_payment)
                        ] + outcomes
                    else:
                        merge = co_merge_transfer_inputs(operation)
                        if merge is not None:
                            result = exercise_payment_transfer(
                                executionContext.dso,
                                executionContext.paymentContext,
                                mkMergeAmuletAndRewardsTransfer(
                                    executionContext.endUser, current_inputs
                                ),
                            )
                            current_inputs = optional_amulet_inputs(
                                result.sender_change_amulet
                            )
                            outcomes = [
                                COO_MergeTransferInputs(result.sender_change_amulet)
                            ] + outcomes
                        else:
                            buy = co_buy_member_traffic(operation)
                            if buy is not None:
                                args: tuple[int, str, str, int, timedelta, Optional[ContractId[ValidatorTopUpState]]] = buy
                                result = buy_member_traffic(
                                    executionContext,
                                    current_inputs,
                                    args[0],
                                    args[1],
                                    args[2],
                                    args[3],
                                    args[4],
                                    args[5],
                                )
                                current_inputs = optional_amulet_inputs(result[1])
                                outcomes = [COO_BuyMemberTraffic(result[0])] + outcomes
                            else:
                                buy_request = co_complete_buy_traffic_request(operation)
                                if buy_request is not None:
                                    cid: ContractId[BuyTrafficRequest] = buy_request
                                    result = exercise(
                                        cid,
                                        BuyTrafficRequest_Complete(
                                            inputs=current_inputs,
                                            context=executionContext.paymentContext,
                                            wallet_provider=executionContext.validator,
                                        ),
                                    )
                                    current_inputs = optional_amulet_inputs(
                                        result.senderChangeAmulet
                                    )
                                    outcomes = [
                                        COO_CompleteBuyTrafficRequest(
                                            (result.purchasedTraffic, result.trackingInfo)
                                        )
                                    ] + outcomes
                                else:
                                    tap = co_tap(operation)
                                    if tap is not None:
                                        tapAmount: Decimal = tap
                                        result = exercise(
                                            executionContext.paymentContext.amulet_rules,
                                            AmuletRules_DevNet_Tap(
                                                receiver=executionContext.endUser,
                                                amount=tapAmount,
                                                open_round=executionContext.paymentContext.context.open_mining_round,
                                            ),
                                        )
                                        tapped = result.amulet_sum.amulet
                                        current_inputs = [InputAmulet(tapped)] + current_inputs
                                        outcomes = [COO_Tap(tapped)] + outcomes
                                    else:
                                        extension = ext_amulet_operation(operation)
                                        if extension is not None:
                                            error("ExtAmuletOperation: bare extension constructors are not allowed at runtime")
                                        else:
                                            create_external = co_create_external_party_setup_proposal(operation)
                                            if create_external is not None:
                                                args: tuple[Party, datetime] = create_external
                                                assert executionContext.endUser == executionContext.validator, "Only a validator can create a proposal to host an external party"
                                                result = exercise(
                                                    executionContext.paymentContext.amulet_rules,
                                                    AmuletRules_CreateExternalPartySetupProposal(
                                                        context=executionContext.paymentContext,
                                                        inputs=current_inputs,
                                                        user=args[0],
                                                        validator=executionContext.validator,
                                                        preapproval_expires_at=args[1],
                                                        expected_dso=executionContext.dso,
                                                        opt_created_at=None,
                                                    ),
                                                )
                                                if result.amulet_paid > Decimal(0):
                                                    current_inputs = optional_amulet_inputs(result.transfer_result.sender_change_amulet)
                                                outcomes = [
                                                    COO_CreateExternalPartySetupProposal(result.proposal_cid)
                                                ] + outcomes
                                            else:
                                                accept_preapproval = co_accept_transfer_preapproval_proposal(operation)
                                                if accept_preapproval is not None:
                                                    args: tuple[ContractId[TransferPreapprovalProposal], datetime] = accept_preapproval
                                                    assert executionContext.endUser == executionContext.validator, "Only a validator can accept a proposal to create a transfer preapproval"
                                                    result = exercise(
                                                        args[0],
                                                        TransferPreapprovalProposal_Accept(
                                                            context=executionContext.paymentContext,
                                                            inputs=current_inputs,
                                                            expires_at=args[1],
                                                        ),
                                                    )
                                                    if result.amuletPaid > Decimal(0):
                                                        current_inputs = optional_amulet_inputs(result.transferResult.sender_change_amulet)
                                                    outcomes = [
                                                        COO_AcceptTransferPreapprovalProposal(
                                                            result.transferPreapprovalCid
                                                        )
                                                    ] + outcomes
                                                else:
                                                    renew = co_renew_transfer_preapproval(operation)
                                                    if renew is not None:
                                                        args: tuple[ContractId[TransferPreapproval], datetime] = renew
                                                        result = exercise(
                                                            args[0],
                                                            TransferPreapproval_Renew(
                                                                context=executionContext.paymentContext,
                                                                inputs=current_inputs,
                                                                new_expires_at=args[1],
                                                            ),
                                                        )
                                                        if result.amulet_paid > Decimal(0):
                                                            current_inputs = optional_amulet_inputs(result.transfer_result.sender_change_amulet)
                                                        outcomes = [
                                                            COO_RenewTransferPreapproval(
                                                                result.transfer_preapproval_cid
                                                            )
                                                        ] + outcomes
                                                    else:
                                                        send = co_transfer_preapproval_send(operation)
                                                        if send is not None:
                                                            args: tuple[ContractId[TransferPreapproval], Optional[ContractId[FeaturedAppRight]], Decimal, Optional[str]] = send
                                                            send_context = payment_context_with_featured_app_right(
                                                                executionContext.paymentContext,
                                                                args[1],
                                                            )
                                                            result = exercise(
                                                                args[0],
                                                                TransferPreapproval_Send(
                                                                    sender=executionContext.endUser,
                                                                    context=send_context,
                                                                    inputs=current_inputs,
                                                                    amount=args[2],
                                                                    description=args[3],
                                                                ),
                                                            )
                                                            current_inputs = optional_amulet_inputs(result.result.sender_change_amulet)
                                                            outcomes = [
                                                                COO_TransferPreapprovalSend(
                                                                    result.result.sender_change_amulet
                                                                )
                                                            ] + outcomes

    return outcomes


struct WalletAppInstall_ExecuteBatchResult:
    endUserName: str
    outcomes: list[AmuletOperationOutcome]
    optEndUserParty: Optional[Party]


struct WalletAppInstall_AppPaymentRequest_RejectResult:
    terminatedAppPayment: ContractId[TerminatedAppPayment]


struct WalletAppInstall_AppPaymentRequest_ExpireResult:
    terminatedAppPayment: ContractId[TerminatedAppPayment]


struct WalletAppInstall_SubscriptionRequest_RejectResult:
    terminatedSubscription: ContractId[TerminatedSubscription]


struct WalletAppInstall_SubscriptionIdleState_CancelSubscriptionResult:
    terminatedSubscription: ContractId[TerminatedSubscription]


struct WalletAppInstall_CreateTransferOfferResult:
    transferOffer: ContractId[TransferOffer]


struct WalletAppInstall_TransferOffer_AcceptResult:
    acceptedTransferOffer: ContractId[AcceptedTransferOffer]


struct WalletAppInstall_TransferOffer_RejectResult:
    trackingInfo: TransferOfferTrackingInfo


struct WalletAppInstall_TransferOffer_WithdrawResult:
    trackingInfo: TransferOfferTrackingInfo


struct WalletAppInstall_TransferOffer_ExpireResult:
    trackingInfo: TransferOfferTrackingInfo


struct WalletAppInstall_AcceptedTransferOffer_AbortResult:
    trackingInfo: TransferOfferTrackingInfo


struct WalletAppInstall_AcceptedTransferOffer_WithdrawResult:
    trackingInfo: TransferOfferTrackingInfo


struct WalletAppInstall_AcceptedTransferOffer_ExpireResult:
    trackingInfo: TransferOfferTrackingInfo


struct WalletAppInstall_CreateBuyTrafficRequestResult:
    buyTrafficRequest: ContractId[BuyTrafficRequest]


struct WalletAppInstall_BuyTrafficRequest_CancelResult:
    trackingInfo: BuyTrafficRequestTrackingInfo


struct WalletAppInstall_BuyTrafficRequest_ExpireResult:
    trackingInfo: BuyTrafficRequestTrackingInfo


struct WalletAppInstall_FeaturedAppRights_CancelResult:
    pass


struct WalletAppInstall_FeaturedAppRights_SelfGrantResult:
    featuredAppRight: ContractId[FeaturedAppRight]


struct WalletAppInstall_TransferPreapprovalProposal_CreateResult:
    preapprovalProposalCid: ContractId[TransferPreapprovalProposal]


def checkTransferInstructionV2(
    dsoParty: Party,
    transferInstructionCid: ContractId[TransferInstructionV2.TransferInstruction],
    actors: list[Party],
    endUserParty: Party,
) -> None:
    check_actors(actors, [[endUserParty]])
    instruction = fetch_checked_interface(
        ForDso(dso=dsoParty), transferInstructionCid
    )
    instruction_view: TransferInstructionV2.TransferInstructionView = view(instruction)
    require_match_expected(
        (
            "instruction.transfer.instrumentId",
            instruction_view.transfer.instrument_id,
        ),
        amulet_instrument_id_v2(dsoParty),
    )


def checkAllocationSpecificationV2(
    allocation: AllocationV2.AllocationSpecification,
    endUserParty: Party,
) -> None:
    require_match_expected(
        ("allocation.authorizer", allocation.authorizer),
        basic_account(endUserParty),
    )
    for leg: AllocationV2.TransferLegSide in allocation.transfer_leg_sides:
        require_match_expected(
            (leg.transfer_leg_id + ".instrumentId", leg.instrument_id),
            amulet_instrument_id_name(),
    )


def unpackCid(value: AnyValue) -> AnyContractId:
    cid = av_contract_id(value)
    if cid is None:
        error("Wrong type")
    result: AnyContractId = cid
    return result


@template
class WalletAppInstall:
    dsoParty: Party
    validatorParty: Party
    endUserName: str
    endUserParty: Party

    signatory: list[Party] = [endUserParty, validatorParty]

    @DamlName("WalletAppInstall_ExecuteBatch")
    @choice(controller=validatorParty, consuming=False)
    def execute_batch(
        self,
        context: PaymentTransferContext,
        inputs: list[TransferInput],
        operations: list[AmuletOperation],
    ) -> WalletAppInstall_ExecuteBatchResult:
        outcomes = executeAmuletOperationRec(
            ExecutionContext(
                dso=self.dsoParty,
                endUser=self.endUserParty,
                validator=self.validatorParty,
                paymentContext=context,
            ),
            inputs,
            [],
            operations,
        )
        return WalletAppInstall_ExecuteBatchResult(
            endUserName=self.endUserName,
            outcomes=reverse(outcomes),
            optEndUserParty=self.endUserParty,
        )

    @DamlName("WalletAppInstall_AppPaymentRequest_Reject")
    @choice(controller=validatorParty, consuming=False)
    def app_payment_request_reject(
        self, cid: ContractId[AppPaymentRequest]
    ) -> WalletAppInstall_AppPaymentRequest_RejectResult:
        result = exercise(cid, AppPaymentRequest_Reject())
        return WalletAppInstall_AppPaymentRequest_RejectResult(
            terminatedAppPayment=result.terminated_app_payment
        )

    @DamlName("WalletAppInstall_AppPaymentRequest_Expire")
    @choice(controller=validatorParty, consuming=False)
    def app_payment_request_expire(
        self, cid: ContractId[AppPaymentRequest]
    ) -> WalletAppInstall_AppPaymentRequest_ExpireResult:
        result = exercise(cid, AppPaymentRequest_Expire(actor=self.endUserParty))
        return WalletAppInstall_AppPaymentRequest_ExpireResult(
            terminatedAppPayment=result.terminated_app_payment
        )

    @DamlName("WalletAppInstall_SubscriptionRequest_Reject")
    @choice(controller=validatorParty, consuming=False)
    def subscription_request_reject(
        self, cid: ContractId[SubscriptionRequest]
    ) -> WalletAppInstall_SubscriptionRequest_RejectResult:
        result = exercise(cid, SubscriptionRequest_Reject())
        return WalletAppInstall_SubscriptionRequest_RejectResult(
            terminatedSubscription=result.terminated_subscription
        )

    @DamlName("WalletAppInstall_SubscriptionIdleState_CancelSubscription")
    @choice(controller=validatorParty, consuming=False)
    def subscription_idle_state_cancel(
        self, cid: ContractId[SubscriptionIdleState]
    ) -> WalletAppInstall_SubscriptionIdleState_CancelSubscriptionResult:
        result = exercise(cid, SubscriptionIdleState_CancelSubscription())
        return WalletAppInstall_SubscriptionIdleState_CancelSubscriptionResult(
            terminatedSubscription=result.terminated_subscription
        )

    @DamlName("WalletAppInstall_CreateTransferOffer")
    @choice(controller=validatorParty, consuming=False)
    def create_transfer_offer(
        self,
        receiver: Party,
        amount: PaymentAmount,
        description: str,
        expiresAt: datetime,
        trackingId: str,
    ) -> WalletAppInstall_CreateTransferOfferResult:
        offer = create(
            TransferOffer(
                sender=self.endUserParty,
                receiver=receiver,
                dso=self.dsoParty,
                amount=amount,
                description=description,
                expiresAt=expiresAt,
                trackingId=trackingId,
            )
        )
        return WalletAppInstall_CreateTransferOfferResult(transferOffer=offer)

    @DamlName("WalletAppInstall_TransferOffer_Accept")
    @choice(controller=validatorParty, consuming=False)
    def transfer_offer_accept(
        self, cid: ContractId[TransferOffer]
    ) -> WalletAppInstall_TransferOffer_AcceptResult:
        result = exercise(cid, TransferOffer_Accept())
        return WalletAppInstall_TransferOffer_AcceptResult(
            acceptedTransferOffer=result.acceptedTransferOffer
        )

    @DamlName("WalletAppInstall_TransferOffer_Reject")
    @choice(controller=validatorParty, consuming=False)
    def transfer_offer_reject(
        self, cid: ContractId[TransferOffer]
    ) -> WalletAppInstall_TransferOffer_RejectResult:
        result = exercise(cid, TransferOffer_Reject())
        return WalletAppInstall_TransferOffer_RejectResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_TransferOffer_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def transfer_offer_withdraw(
        self, cid: ContractId[TransferOffer], reason: str
    ) -> WalletAppInstall_TransferOffer_WithdrawResult:
        result = exercise(cid, TransferOffer_Withdraw(reason=reason))
        return WalletAppInstall_TransferOffer_WithdrawResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_TransferOffer_Expire")
    @choice(controller=validatorParty, consuming=False)
    def transfer_offer_expire(
        self, cid: ContractId[TransferOffer]
    ) -> WalletAppInstall_TransferOffer_ExpireResult:
        result = exercise(cid, TransferOffer_Expire(actor=self.endUserParty))
        return WalletAppInstall_TransferOffer_ExpireResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_AcceptedTransferOffer_Abort")
    @choice(controller=validatorParty, consuming=False)
    def accepted_transfer_offer_abort(
        self, cid: ContractId[AcceptedTransferOffer], reason: str
    ) -> WalletAppInstall_AcceptedTransferOffer_AbortResult:
        result = exercise(cid, AcceptedTransferOffer_Abort(reason=reason))
        return WalletAppInstall_AcceptedTransferOffer_AbortResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_AcceptedTransferOffer_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def accepted_transfer_offer_withdraw(
        self, cid: ContractId[AcceptedTransferOffer], reason: str
    ) -> WalletAppInstall_AcceptedTransferOffer_WithdrawResult:
        result = exercise(cid, AcceptedTransferOffer_Withdraw(reason=reason))
        return WalletAppInstall_AcceptedTransferOffer_WithdrawResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_AcceptedTransferOffer_Expire")
    @choice(controller=validatorParty, consuming=False)
    def accepted_transfer_offer_expire(
        self, cid: ContractId[AcceptedTransferOffer]
    ) -> WalletAppInstall_AcceptedTransferOffer_ExpireResult:
        result = exercise(cid, AcceptedTransferOffer_Expire(actor=self.endUserParty))
        return WalletAppInstall_AcceptedTransferOffer_ExpireResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_CreateBuyTrafficRequest")
    @choice(controller=validatorParty, consuming=False)
    def create_buy_traffic_request(
        self,
        memberId: str,
        synchronizerId: str,
        migrationId: int,
        trafficAmount: int,
        expiresAt: datetime,
        trackingId: str,
    ) -> WalletAppInstall_CreateBuyTrafficRequestResult:
        request = create(
            BuyTrafficRequest(
                dso=self.dsoParty,
                endUserParty=self.endUserParty,
                expiresAt=expiresAt,
                trackingId=trackingId,
                trafficAmount=trafficAmount,
                memberId=memberId,
                synchronizerId=synchronizerId,
                migrationId=migrationId,
            )
        )
        return WalletAppInstall_CreateBuyTrafficRequestResult(
            buyTrafficRequest=request
        )

    @DamlName("WalletAppInstall_BuyTrafficRequest_Cancel")
    @choice(controller=validatorParty, consuming=False)
    def buy_traffic_request_cancel(
        self, requestCid: ContractId[BuyTrafficRequest], reason: str
    ) -> WalletAppInstall_BuyTrafficRequest_CancelResult:
        result = exercise(requestCid, BuyTrafficRequest_Cancel(reason=reason))
        return WalletAppInstall_BuyTrafficRequest_CancelResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_BuyTrafficRequest_Expire")
    @choice(controller=validatorParty, consuming=False)
    def buy_traffic_request_expire(
        self, requestCid: ContractId[BuyTrafficRequest]
    ) -> WalletAppInstall_BuyTrafficRequest_ExpireResult:
        result = exercise(requestCid, BuyTrafficRequest_Expire())
        return WalletAppInstall_BuyTrafficRequest_ExpireResult(
            trackingInfo=result.trackingInfo
        )

    @DamlName("WalletAppInstall_FeaturedAppRights_Cancel")
    @choice(controller=validatorParty, consuming=False)
    def featured_app_rights_cancel(
        self, cid: ContractId[FeaturedAppRight]
    ) -> WalletAppInstall_FeaturedAppRights_CancelResult:
        exercise(cid, FeaturedAppRight_Cancel())
        return WalletAppInstall_FeaturedAppRights_CancelResult()

    @DamlName("WalletAppInstall_FeaturedAppRights_SelfGrant")
    @choice(controller=validatorParty, consuming=False)
    def featured_app_rights_self_grant(
        self, amuletRulesCid: ContractId[AmuletRules]
    ) -> WalletAppInstall_FeaturedAppRights_SelfGrantResult:
        result = exercise(
            amuletRulesCid,
            AmuletRules_DevNet_FeatureApp(
                provider=self.endUserParty, activity_weight=None
            ),
        )
        return WalletAppInstall_FeaturedAppRights_SelfGrantResult(
            featuredAppRight=result.featured_app_right_cid
        )

    @DamlName("WalletAppInstall_TransferFactory_Transfer")
    @choice(controller=validatorParty, consuming=False)
    def transfer_factory_transfer(
        self,
        transferFactoryCid: ContractId[TransferInstructionV1.TransferFactory],
        transferArg: TransferInstructionV1.TransferFactory_Transfer,
    ) -> TransferInstructionV1.TransferInstructionResult:
        assert transferArg.transfer.sender == self.endUserParty, "sender is endUserParty"
        assert transferArg.transfer.instrument_id == amulet_instrument_id(self.dsoParty), "instrumentId is amuletInstrumentId"
        assert transferArg.expected_admin == self.dsoParty, "expected admin is dso"
        return exercise(transferFactoryCid, transferArg)

    @DamlName("WalletAppInstall_TransferInstruction_Accept")
    @choice(controller=validatorParty, consuming=False)
    def transfer_instruction_accept(
        self,
        transferInstructionCid: ContractId[TransferInstructionV1.TransferInstruction],
        acceptArg: TransferInstructionV1.TransferInstruction_Accept,
    ) -> TransferInstructionV1.TransferInstructionResult:
        instruction = fetch_checked_interface(
            ForDso(dso=self.dsoParty), transferInstructionCid
        )
        instruction_view: TransferInstructionV1.TransferInstructionView = view(instruction)
        assert instruction_view.transfer.receiver == self.endUserParty, "receiver must match endUserParty"
        return exercise(transferInstructionCid, acceptArg)

    @DamlName("WalletAppInstall_TransferInstruction_Reject")
    @choice(controller=validatorParty, consuming=False)
    def transfer_instruction_reject(
        self,
        transferInstructionCid: ContractId[TransferInstructionV1.TransferInstruction],
        rejectArg: TransferInstructionV1.TransferInstruction_Reject,
    ) -> TransferInstructionV1.TransferInstructionResult:
        instruction = fetch_checked_interface(
            ForDso(dso=self.dsoParty), transferInstructionCid
        )
        instruction_view: TransferInstructionV1.TransferInstructionView = view(instruction)
        assert instruction_view.transfer.receiver == self.endUserParty, "receiver must match endUserParty"
        return exercise(transferInstructionCid, rejectArg)

    @DamlName("WalletAppInstall_TransferInstruction_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def transfer_instruction_withdraw(
        self,
        transferInstructionCid: ContractId[TransferInstructionV1.TransferInstruction],
        withdrawArg: TransferInstructionV1.TransferInstruction_Withdraw,
    ) -> TransferInstructionV1.TransferInstructionResult:
        instruction = fetch_checked_interface(
            ForDso(dso=self.dsoParty), transferInstructionCid
        )
        instruction_view: TransferInstructionV1.TransferInstructionView = view(instruction)
        assert instruction_view.transfer.sender == self.endUserParty, "sender must match endUserParty"
        return exercise(transferInstructionCid, withdrawArg)

    @DamlName("WalletAppInstall_AllocationFactory_Allocate")
    @choice(controller=validatorParty, consuming=False)
    def allocation_factory_allocate(
        self,
        allocationFactory: ContractId[AllocationInstructionV1.AllocationFactory],
        allocateArg: AllocationInstructionV1.AllocationFactory_Allocate,
    ) -> AllocationInstructionV1.AllocationInstructionResult:
        assert allocateArg.expected_admin == self.dsoParty, "expected admin is dso"
        assert allocateArg.allocation.transfer_leg.sender == self.endUserParty, "sender is endUserParty"
        assert allocateArg.allocation.transfer_leg.instrument_id == amulet_instrument_id(self.dsoParty), "instrumentId is amuletInstrumentId"
        return exercise(allocationFactory, allocateArg)

    @DamlName("WalletAppInstall_Allocation_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def allocation_withdraw(
        self,
        allocationCid: ContractId[AllocationV1.Allocation],
        withdrawArg: AllocationV1.Allocation_Withdraw,
    ) -> AllocationV1.Allocation_WithdrawResult:
        allocation = fetch_checked_interface(
            ForDso(dso=self.dsoParty), allocationCid
        )
        allocation_view: AllocationV1.AllocationView = view(allocation)
        assert allocation_view.allocation.transfer_leg.sender == self.endUserParty, "sender must match endUserParty"
        return exercise(allocationCid, withdrawArg)

    @DamlName("WalletAppInstall_TransferFactoryV2_Transfer")
    @choice(controller=validatorParty, consuming=False)
    def transfer_factory_v2_transfer(
        self,
        transferFactoryCid: ContractId[TransferInstructionV2.TransferFactory],
        transferArg: TransferInstructionV2.TransferFactory_Transfer,
    ) -> TransferInstructionV2.TransferInstructionResult:
        require_match_expected(
            ("transfer.instrumentId", transferArg.transfer.instrument_id),
            amulet_instrument_id_v2(self.dsoParty),
        )
        return exercise(transferFactoryCid, transferArg)

    @DamlName("WalletAppInstall_TransferInstructionV2_Accept")
    @choice(controller=validatorParty, consuming=False)
    def transfer_instruction_v2_accept(
        self,
        transferInstructionCid: ContractId[TransferInstructionV2.TransferInstruction],
        acceptArg: TransferInstructionV2.TransferInstruction_Accept,
    ) -> TransferInstructionV2.TransferInstructionResult:
        checkTransferInstructionV2(
            self.dsoParty,
            transferInstructionCid,
            acceptArg.actors,
            self.endUserParty,
        )
        return exercise(transferInstructionCid, acceptArg)

    @DamlName("WalletAppInstall_TransferInstructionV2_Reject")
    @choice(controller=validatorParty, consuming=False)
    def transfer_instruction_v2_reject(
        self,
        transferInstructionCid: ContractId[TransferInstructionV2.TransferInstruction],
        rejectArg: TransferInstructionV2.TransferInstruction_Reject,
    ) -> TransferInstructionV2.TransferInstructionResult:
        checkTransferInstructionV2(
            self.dsoParty,
            transferInstructionCid,
            rejectArg.actors,
            self.endUserParty,
        )
        return exercise(transferInstructionCid, rejectArg)

    @DamlName("WalletAppInstall_TransferInstructionV2_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def transfer_instruction_v2_withdraw(
        self,
        transferInstructionCid: ContractId[TransferInstructionV2.TransferInstruction],
        withdrawArg: TransferInstructionV2.TransferInstruction_Withdraw,
    ) -> TransferInstructionV2.TransferInstructionResult:
        checkTransferInstructionV2(
            self.dsoParty,
            transferInstructionCid,
            withdrawArg.actors,
            self.endUserParty,
        )
        return exercise(transferInstructionCid, withdrawArg)

    @DamlName("WalletAppInstall_AllocationFactoryV2_Allocate")
    @choice(controller=validatorParty, consuming=False)
    def allocation_factory_v2_allocate(
        self,
        allocationFactory: ContractId[AllocationInstructionV2.AllocationFactory],
        allocateArg: AllocationInstructionV2.AllocationFactory_Allocate,
        allocationReqCid: Optional[ContractId[AllocationRequestV2.AllocationRequest]],
    ) -> AllocationInstructionV2.AllocationInstructionResult:
        check_actors(allocateArg.actors, [[self.endUserParty]])
        checkAllocationSpecificationV2(
            allocateArg.allocation, self.endUserParty
        )
        if allocationReqCid is not None:
            request_cid: ContractId[AllocationRequestV2.AllocationRequest] = allocationReqCid
            request = fetch_unchecked_but_archive_later(request_cid)
            request_view: AllocationRequestV2.AllocationRequestView = view(request)
            require_match_expected(
                ("allocationReq.settlement", request_view.settlement),
                allocateArg.settlement,
            )
            exercise(
                request_cid,
                AllocationRequestV2.AllocationRequest_Accept(
                    actors=[self.endUserParty],
                    extra_args=ExtraArgs(
                        context=ChoiceContext(values={}),
                        meta=Metadata(values={}),
                    ),
                ),
            )
        return exercise(allocationFactory, allocateArg)

    @DamlName("WalletAppInstall_AllocationV2_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def allocation_v2_withdraw(
        self,
        allocationCid: ContractId[AllocationV2.Allocation],
        withdrawArg: AllocationV2.Allocation_Withdraw,
    ) -> AllocationV2.AllocationResult:
        check_actors(withdrawArg.actors, [[self.endUserParty]])
        allocation = fetch_checked_interface(
            ForDso(dso=self.dsoParty), allocationCid
        )
        allocation_view: AllocationV2.AllocationView = view(allocation)
        checkAllocationSpecificationV2(
            allocation_view.allocation, self.endUserParty
        )
        return exercise(allocationCid, withdrawArg)

    @DamlName("WalletAppInstall_AllocationInstructionV2_Accept")
    @choice(controller=validatorParty, consuming=False)
    def allocation_instruction_v2_accept(
        self,
        allocationInstructionCid: ContractId[AllocationInstructionV2.AllocationInstruction],
        acceptArg: AllocationInstructionV2.AllocationInstruction_Accept,
    ) -> AllocationInstructionV2.AllocationInstructionResult:
        check_actors(acceptArg.actors, [[self.endUserParty]])
        instruction = fetch_checked_interface(
            ForDso(dso=self.dsoParty), allocationInstructionCid
        )
        instruction_view: AllocationInstructionV2.AllocationInstructionView = view(instruction)
        checkAllocationSpecificationV2(
            instruction_view.allocation, self.endUserParty
        )
        return exercise(allocationInstructionCid, acceptArg)

    @DamlName("WalletAppInstall_AllocationInstructionV2_Withdraw")
    @choice(controller=validatorParty, consuming=False)
    def allocation_instruction_v2_withdraw(
        self,
        allocationInstructionCid: ContractId[AllocationInstructionV2.AllocationInstruction],
        withdrawArg: AllocationInstructionV2.AllocationInstruction_Withdraw,
    ) -> AllocationInstructionV2.AllocationInstructionResult:
        check_actors(withdrawArg.actors, [[self.endUserParty]])
        instruction = fetch_checked_interface(
            ForDso(dso=self.dsoParty), allocationInstructionCid
        )
        instruction_view: AllocationInstructionV2.AllocationInstructionView = view(instruction)
        checkAllocationSpecificationV2(
            instruction_view.allocation, self.endUserParty
        )
        return exercise(allocationInstructionCid, withdrawArg)
