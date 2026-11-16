from forge.models import ContractId, Decimal, Party, choice, template
from forge.builtins import DamlName
from forge.builtins.daml_lang import exercise, fetch, view
import Splice.Api.FeaturedAppRightV1 as FeaturedAppRightV1
import Splice.Api.FeaturedAppRightV2 as FeaturedAppRightV2


struct BatchedMarkersProxy_CreateMarkersResult:
    results: list[list[FeaturedAppRightV1.FeaturedAppRight_CreateActivityMarkerResult]]


struct BatchedMarkersProxy_CreateMarkersV2Result:
    results: list[FeaturedAppRightV2.FeaturedAppRight_CreateActivityMarkerResult]


struct RewardBatch:
    beneficiaries: list[FeaturedAppRightV1.AppRewardBeneficiary]
    num_markers: int


struct RewardBatchV2:
    beneficiaries: list[FeaturedAppRightV2.AppRewardBeneficiary]
    marker_weight: Decimal


def collect_marker_beneficiaries(batches: list[RewardBatch]) -> list[Party]:
    beneficiaries: list[Party] = []
    for batch: RewardBatch in batches:
        for entry: FeaturedAppRightV1.AppRewardBeneficiary in batch.beneficiaries:
            beneficiaries.append(entry.beneficiary)
    return beneficiaries


def collect_marker_beneficiaries_v2(batches: list[RewardBatchV2]) -> list[Party]:
    beneficiaries: list[Party] = []
    for batch: RewardBatchV2 in batches:
        for entry: FeaturedAppRightV2.AppRewardBeneficiary in batch.beneficiaries:
            beneficiaries.append(entry.beneficiary)
    return beneficiaries


@template
class BatchedMarkersProxy:
    provider: Party
    dso: Party

    signatory: Party = provider

    @DamlName("BatchedMarkersProxy_CreateMarkers")
    @choice(
        controller=provider,
        consuming=False,
        observer=[dso] + collect_marker_beneficiaries(batches),
    )
    def batched_markers_proxy_create_markers(
        self,
        featured_app_right_cid: ContractId[FeaturedAppRightV1.FeaturedAppRight],
        batches: list[RewardBatch],
    ) -> BatchedMarkersProxy_CreateMarkersResult:
        for batch: RewardBatch in batches:
            assert batch.num_markers > 0, "numMarkers > 0"

        total_markers = 0
        for batch: RewardBatch in batches:
            total_markers = total_markers + batch.num_markers
        assert total_markers <= 10000, "sum numMarkers <= 10000"

        featured_app_right = fetch(featured_app_right_cid)
        featured_app_right_view: FeaturedAppRightV1.FeaturedAppRightView = view(featured_app_right)
        assert featured_app_right_view.dso == self.dso, "DSO party on featured app right is correct"

        results: list[list[FeaturedAppRightV1.FeaturedAppRight_CreateActivityMarkerResult]] = []
        for batch: RewardBatch in batches:
            batch_results: list[FeaturedAppRightV1.FeaturedAppRight_CreateActivityMarkerResult] = []
            for _index: int in range(0, batch.num_markers):
                marker_result = exercise(
                    featured_app_right_cid,
                    FeaturedAppRightV1.FeaturedAppRight_CreateActivityMarker(beneficiaries=batch.beneficiaries),
                )
                batch_results.append(marker_result)
            results.append(batch_results)

        return BatchedMarkersProxy_CreateMarkersResult(results=results)

    @DamlName("BatchedMarkersProxy_CreateMarkersV2")
    @choice(
        controller=provider,
        consuming=False,
        observer=[dso] + collect_marker_beneficiaries_v2(batches),
    )
    def batched_markers_proxy_create_markers_v2(
        self,
        featured_app_right_cid: ContractId[FeaturedAppRightV2.FeaturedAppRight],
        batches: list[RewardBatchV2],
    ) -> BatchedMarkersProxy_CreateMarkersV2Result:
        for batch: RewardBatchV2 in batches:
            assert batch.marker_weight >= Decimal(1), "markerWeight >= 1.0"

        total_weight = Decimal(0)
        for batch: RewardBatchV2 in batches:
            total_weight = total_weight + batch.marker_weight
        assert total_weight <= Decimal(10000), "sum numMarkers <= 10000.0"

        featured_app_right = fetch(featured_app_right_cid)
        featured_app_right_view: FeaturedAppRightV2.FeaturedAppRightView = view(featured_app_right)
        assert featured_app_right_view.dso == self.dso, "DSO party on featured app right is correct"

        results: list[FeaturedAppRightV2.FeaturedAppRight_CreateActivityMarkerResult] = []
        for batch: RewardBatchV2 in batches:
            marker_result = exercise(
                featured_app_right_cid,
                FeaturedAppRightV2.FeaturedAppRight_CreateActivityMarker(
                    beneficiaries=batch.beneficiaries,
                    weight=batch.marker_weight,
                ),
            )
            results.append(marker_result)

        return BatchedMarkersProxy_CreateMarkersV2Result(results=results)
