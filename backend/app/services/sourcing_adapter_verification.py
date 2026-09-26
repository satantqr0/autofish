import time

from adapters.base import AdapterStatus
from sqlalchemy.orm import Session

from app.models import AdapterCall, SourcingCandidate
from app.services.adapter_factory import get_supplier_detail_adapter
from app.services.audit import write_audit
from app.services.integrations import (
    enrich_product_find_candidate,
    finish_sync_run,
    save_snapshot,
    serialize_candidate,
    start_sync_run,
    upsert_sourcing_candidate,
)


class CandidateAdapterVerificationError(RuntimeError):
    def __init__(
        self,
        *,
        status: AdapterStatus,
        code: str | None,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.safe_message = message
        self.retryable = retryable


async def verify_candidate_from_authorized_adapter(
    db: Session,
    *,
    candidate: SourcingCandidate,
    actor_user_id: int | None,
    actor_type: str,
    correlation_id: str,
) -> dict:
    """Enrich one Product Find candidate using the configured read-only detail adapter.

    The caller owns the transaction. Freight is only populated by the downstream
    evidence parser when the signed supplier title explicitly states free shipping.
    """
    adapter = get_supplier_detail_adapter()
    started = time.monotonic()
    result = await adapter.get_product(candidate.external_product_id)
    duration_ms = int((time.monotonic() - started) * 1000)
    db.add(
        AdapterCall(
            correlation_id=correlation_id,
            adapter=result.source,
            adapter_version=result.source_version,
            operation="VERIFY_PRODUCT_DETAIL",
            status=result.status.value,
            duration_ms=duration_ms,
            error_code=result.error_code,
            safe_summary={"candidate_id": candidate.id},
        )
    )
    if result.status != AdapterStatus.OK or result.data is None:
        raise CandidateAdapterVerificationError(
            status=result.status,
            code=result.error_code,
            message=result.safe_message or "Adapter 调用失败",
            retryable=result.status
            in {AdapterStatus.RATE_LIMITED, AdapterStatus.TRANSIENT_ERROR},
        )

    normalized = enrich_product_find_candidate(
        candidate.normalized_data or {},
        result.data.raw_content,
    )
    run = start_sync_run(
        db,
        platform="1688",
        adapter_name="1688-hybrid-verification",
        adapter_version="1.0",
        operation="VERIFY_PRODUCT_DETAIL",
        requested_by_user_id=actor_user_id,
        correlation_id=correlation_id,
    )
    snapshot, created = save_snapshot(
        db,
        run=run,
        platform="1688",
        object_type="SUPPLIER_PRODUCT_VERIFICATION",
        external_id=candidate.external_product_id,
        adapter_name="1688-hybrid-verification",
        adapter_version="1.0",
        normalized_data=normalized,
        raw_payload={
            "candidate_id": candidate.id,
            "detail_snapshot_hash": normalized["stats"]["detail_snapshot_hash"],
            "adapter_verification": normalized["adapter_verification"],
        },
    )
    candidate = upsert_sourcing_candidate(
        db,
        snapshot=snapshot,
        adapter_name=candidate.adapter_name,
        adapter_version=candidate.adapter_version,
        data=normalized,
    )
    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=1,
        items_written=int(created),
    )
    serialized = serialize_candidate(candidate)
    write_audit(
        db,
        action="SOURCING_CANDIDATE_ADAPTER_VERIFIED",
        entity_type="SOURCING_CANDIDATE",
        entity_id=candidate.id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        after_data={
            "snapshot_id": snapshot.id,
            "import_ready": serialized["import_ready"],
            "conditional_shipping": normalized["adapter_verification"][
                "conditional_shipping"
            ],
        },
        correlation_id=correlation_id,
    )
    return {
        "sync_run_id": run.id,
        "snapshot_id": snapshot.id,
        "snapshot_created": created,
        "candidate": serialized,
    }
