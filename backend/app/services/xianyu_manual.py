from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (
    Account,
    AutomationControl,
    AutomationScope,
    AutonomousLaunch,
    Lifecycle,
    PlatformActionRecord,
    Product,
    ProductSKU,
    XianyuDraft,
    XianyuProduct,
    XianyuStatus,
)
from app.services.audit import write_audit
from app.services.catalog import product_query_options
from app.services.integrations import (
    finish_sync_run,
    sanitize_payload,
    save_snapshot,
    snapshot_hash,
    start_sync_run,
)
from app.services.publication_guard import assert_product_publishable

ADAPTER_NAME = "manual-browser-snapshot"
ADAPTER_VERSION = "1.0"
PUBLICATION_ADAPTER_NAME = "manual-browser-publication"
BRIDGE_PROVIDER = "seller-browser-bridge"
PUBLISH_ACTION = "BROWSER_PUBLISH_PRODUCT"


def _reconcile_verified_browser_publication(
    db,
    *,
    draft,
    payload,
    user,
    correlation_id,
):
    """Close a pending browser handoff after its live listing was independently verified."""

    result = {
        "browser_action_id": None,
        "browser_task_reconciled": False,
        "launch_id": None,
        "launch_reconciled": False,
    }
    if draft is None or payload.status != "ACTIVE":
        return result

    action = db.scalar(
        select(PlatformActionRecord)
        .where(
            PlatformActionRecord.provider == BRIDGE_PROVIDER,
            PlatformActionRecord.action_type == PUBLISH_ACTION,
            PlatformActionRecord.target_type == "XIANYU_DRAFT",
            PlatformActionRecord.target_id == draft.id,
        )
        .order_by(PlatformActionRecord.id.desc())
    )
    if action is not None:
        result["browser_action_id"] = action.id
        if action.status != "SUCCEEDED":
            verified_at = payload.captured_at.astimezone(UTC)
            action.status = "SUCCEEDED"
            action.execution_result = {
                **(action.execution_result or {}),
                "status": "SUCCEEDED",
                "phase": "SUBMITTED",
                "submission_attempted": True,
                "success_evidence": "闲鱼发布成功页及公开商品详情已核验",
                "external_product_id": payload.external_product_id,
                "current_url": payload.source_url,
                "received_at": verified_at.isoformat(),
                "result_source": "MANUAL_BROWSER_RECONCILIATION",
            }
            action.executed_at = verified_at
            action.lease_owner = None
            action.lease_expires_at = None
            action.error_code = None
            action.error_message = None
            result["browser_task_reconciled"] = True
            write_audit(
                db,
                action="XIANYU_BROWSER_TASK_MANUALLY_RECONCILED",
                entity_type="PLATFORM_ACTION",
                entity_id=action.id,
                actor_user_id=user.id,
                actor_type="USER",
                after_data={
                    "draft_id": draft.id,
                    "external_product_id": payload.external_product_id,
                    "source_url": payload.source_url,
                    "success_evidence": "闲鱼发布成功页及公开商品详情已核验",
                },
                result="SUCCEEDED",
                correlation_id=correlation_id,
            )

    launch = db.scalar(
        select(AutonomousLaunch)
        .where(AutonomousLaunch.draft_id == draft.id)
        .order_by(AutonomousLaunch.id.desc())
    )
    if launch is not None:
        result["launch_id"] = launch.id
        if launch.status != "PUBLISHED" or launch.current_stage != "XIANYU_PUBLISHED":
            launch.status = "PUBLISHED"
            launch.current_stage = "XIANYU_PUBLISHED"
            launch.finished_at = payload.captured_at.astimezone(UTC)
            launch.error_code = None
            launch.error_message = None
            result["launch_reconciled"] = True

    if result["browser_task_reconciled"] or result["launch_reconciled"]:
        now = datetime.now(UTC)
        controls = db.scalars(
            select(AutomationControl).where(
                AutomationControl.scope.in_([
                    AutomationScope.GLOBAL,
                    AutomationScope.PUBLISH,
                ])
            )
        ).all()
        for control in controls:
            control.last_executed_at = now
            control.consecutive_failures = 0
    return result


def ingest_manual_xianyu_snapshot(db, *, payload, user, correlation_id):
    captured_at = payload.captured_at.isoformat()
    run = start_sync_run(
        db,
        platform="XIANYU",
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        operation="INGEST_MANUAL_BROWSER_SNAPSHOT",
        requested_by_user_id=user.id,
        correlation_id=correlation_id,
    )
    account_ref = f"manual-browser:{user.id}"
    account_payload = {
        "external_account_id": account_ref,
        "nickname": payload.nickname,
        "status": "MANUAL_READ_ONLY",
        "source_url": payload.source_url,
        "captured_at": captured_at,
        "declared_active_count": payload.declared_active_count,
        "declared_sold_count": payload.declared_sold_count,
        "snapshot_complete": payload.snapshot_complete,
        "ingest_method": ADAPTER_NAME,
    }
    account_snapshot, account_snapshot_created = save_snapshot(
        db,
        run=run,
        platform="XIANYU",
        object_type="ACCOUNT",
        external_id=account_ref,
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        normalized_data=account_payload,
        raw_payload=account_payload,
    )
    account = db.scalar(
        select(Account).where(
            Account.platform == "XIANYU",
            Account.external_account_id == account_ref,
            Account.owner_user_id == user.id,
        )
    )
    if account is None:
        account = Account(
            platform="XIANYU",
            external_account_id=account_ref,
            nickname=payload.nickname,
            owner_user_id=user.id,
        )
        db.add(account)
        db.flush()
    account.nickname = payload.nickname
    account.status = "MANUAL_READ_ONLY"
    account.is_enabled = True
    account.raw_snapshot = sanitize_payload(account_payload)
    account.snapshot_hash = account_snapshot.payload_hash
    account.last_synced_at = payload.captured_at

    products_created = 0
    products_updated = 0
    products_removed = 0
    snapshots_written = int(account_snapshot_created)
    seen_product_ids = {item.external_product_id for item in payload.products}
    for item in payload.products:
        normalized = {
            **item.model_dump(mode="json"),
            "captured_at": captured_at,
            "ingest_method": ADAPTER_NAME,
        }
        product_snapshot, snapshot_created = save_snapshot(
            db,
            run=run,
            platform="XIANYU",
            object_type="PRODUCT",
            external_id=item.external_product_id,
            adapter_name=ADAPTER_NAME,
            adapter_version=ADAPTER_VERSION,
            normalized_data=normalized,
            raw_payload=normalized,
        )
        snapshots_written += int(snapshot_created)
        row = db.scalar(
            select(XianyuProduct).where(
                XianyuProduct.account_id == account.id,
                XianyuProduct.external_product_id == item.external_product_id,
            )
        )
        if row is None:
            row = XianyuProduct(
                account_id=account.id,
                product_id=None,
                external_product_id=item.external_product_id,
            )
            db.add(row)
            products_created += 1
        elif snapshot_created:
            products_updated += 1
        row.status = item.status
        row.published_title = item.title
        row.published_price = item.price
        row.raw_snapshot = sanitize_payload(normalized)
        row.snapshot_hash = product_snapshot.payload_hash
        row.last_synced_at = payload.captured_at

    if payload.snapshot_complete:
        missing_active_products = db.scalars(
            select(XianyuProduct).where(
                XianyuProduct.account_id == account.id,
                XianyuProduct.status == "ACTIVE",
                XianyuProduct.external_product_id.not_in(seen_product_ids),
            )
        ).all()
        for row in missing_active_products:
            normalized = {
                "external_product_id": row.external_product_id,
                "title": row.published_title,
                "price": str(row.published_price) if row.published_price is not None else None,
                "status": "REMOVED",
                "source_url": (row.raw_snapshot or {}).get("source_url"),
                "captured_at": captured_at,
                "ingest_method": ADAPTER_NAME,
                "reason": "absent-from-complete-snapshot",
            }
            product_snapshot, snapshot_created = save_snapshot(
                db,
                run=run,
                platform="XIANYU",
                object_type="PRODUCT",
                external_id=row.external_product_id,
                adapter_name=ADAPTER_NAME,
                adapter_version=ADAPTER_VERSION,
                normalized_data=normalized,
                raw_payload=normalized,
            )
            snapshots_written += int(snapshot_created)
            row.status = "REMOVED"
            row.raw_snapshot = sanitize_payload(normalized)
            row.snapshot_hash = product_snapshot.payload_hash
            row.last_synced_at = payload.captured_at
            products_updated += 1
            products_removed += 1

    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=1 + len(payload.products),
        items_written=snapshots_written,
    )
    write_audit(
        db,
        action="XIANYU_MANUAL_SNAPSHOT_INGESTED",
        entity_type="INTEGRATION_SYNC_RUN",
        entity_id=run.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={
            "products_seen": len(payload.products),
            "products_created": products_created,
            "products_updated": products_updated,
            "products_removed": products_removed,
            "snapshots_written": snapshots_written,
            "snapshot_complete": payload.snapshot_complete,
            "payload_hash": snapshot_hash(payload.model_dump(mode="json")),
        },
        correlation_id=correlation_id,
    )
    db.commit()
    return {
        "sync_run_id": run.id,
        "account_id": account.id,
        "products_seen": len(payload.products),
        "products_created": products_created,
        "products_updated": products_updated,
        "products_removed": products_removed,
        "snapshots_written": snapshots_written,
        "snapshot_complete": payload.snapshot_complete,
    }


def reconcile_manual_xianyu_publication(db, *, payload, user, correlation_id):
    """Link a verified live listing to its internal product and SKU facts."""

    product = db.scalar(
        select(Product).where(Product.id == payload.product_id).options(*product_query_options())
    )
    if product is None:
        raise LookupError("内部商品不存在")
    assert_product_publishable(product)
    product_sku = db.get(ProductSKU, payload.product_sku_id)
    if product_sku is None or product_sku.product_id != product.id:
        raise LookupError("内部 SKU 不属于该商品")
    draft = None
    if payload.draft_id is not None:
        draft = db.get(XianyuDraft, payload.draft_id)
        if draft is None or draft.product_id != product.id:
            raise LookupError("闲鱼草稿不属于该商品")

    account_ref = f"manual-browser:{user.id}"
    account = db.scalar(
        select(Account).where(
            Account.platform == "XIANYU",
            Account.external_account_id == account_ref,
            Account.owner_user_id == user.id,
        )
    )
    if account is None:
        account = Account(
            platform="XIANYU",
            external_account_id=account_ref,
            nickname=payload.nickname,
            owner_user_id=user.id,
            status="MANUAL_READ_ONLY",
            is_enabled=True,
        )
        db.add(account)
        db.flush()
    else:
        account.nickname = payload.nickname
        account.is_enabled = True

    row = db.scalar(
        select(XianyuProduct).where(
            XianyuProduct.account_id == account.id,
            XianyuProduct.external_product_id == payload.external_product_id,
        )
    )
    if row is not None and row.product_id not in {None, product.id}:
        raise ValueError("该闲鱼商品已关联到其他内部商品")

    normalized = {
        **payload.model_dump(mode="json"),
        "ingest_method": PUBLICATION_ADAPTER_NAME,
    }
    run = start_sync_run(
        db,
        platform="XIANYU",
        adapter_name=PUBLICATION_ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        operation="RECONCILE_MANUAL_PUBLICATION",
        requested_by_user_id=user.id,
        correlation_id=correlation_id,
    )
    snapshot, snapshot_created = save_snapshot(
        db,
        run=run,
        platform="XIANYU",
        object_type="PRODUCT",
        external_id=payload.external_product_id,
        adapter_name=PUBLICATION_ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        normalized_data=normalized,
        raw_payload=normalized,
    )

    created = row is None
    if row is None:
        row = XianyuProduct(
            account_id=account.id,
            product_id=product.id,
            external_product_id=payload.external_product_id,
        )
        db.add(row)
        db.flush()
    row.product_id = product.id
    row.status = payload.status
    row.published_title = payload.title
    row.published_price = payload.price
    row.raw_snapshot = sanitize_payload(normalized)
    row.snapshot_hash = snapshot.payload_hash
    row.last_synced_at = payload.captured_at

    product.xianyu_status = (
        XianyuStatus.ACTIVE if payload.status == "ACTIVE" else XianyuStatus.PAUSED
    )
    if payload.status == "ACTIVE" and product.lifecycle in {
        Lifecycle.CANDIDATE,
        Lifecycle.TESTING,
    }:
        product.lifecycle = Lifecycle.ACTIVE
    product_sku.current_sale_price = payload.price
    draft_reconciled = False
    if draft is not None and payload.status == "ACTIVE":
        draft_reconciled = draft.status != "PUBLISHED"
        draft.status = "PUBLISHED"
    browser_reconciliation = _reconcile_verified_browser_publication(
        db,
        draft=draft,
        payload=payload,
        user=user,
        correlation_id=correlation_id,
    )

    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=1,
        items_written=int(snapshot_created),
    )
    write_audit(
        db,
        action="XIANYU_MANUAL_PUBLICATION_RECONCILED",
        entity_type="XIANYU_PRODUCT",
        entity_id=row.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={
            "product_id": product.id,
            "product_sku_id": product_sku.id,
            "external_product_id": payload.external_product_id,
            "status": payload.status,
            "price": str(payload.price),
            "snapshot_created": snapshot_created,
            "draft_id": draft.id if draft else None,
            "draft_reconciled": draft_reconciled,
            **browser_reconciliation,
        },
        correlation_id=correlation_id,
    )
    db.commit()
    return {
        "account_id": account.id,
        "product_id": product.id,
        "product_sku_id": product_sku.id,
        "xianyu_product_id": row.id,
        "external_product_id": payload.external_product_id,
        "status": row.status,
        "created": created,
        "idempotent": not snapshot_created,
        "draft_id": draft.id if draft else None,
        "draft_reconciled": draft_reconciled,
        **browser_reconciliation,
    }
