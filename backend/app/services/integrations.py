import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models import ExternalSnapshot, IntegrationSyncRun, SourcingCandidate
from app.schemas.integrations import (
    CandidateManualVerificationRequest,
    extract_1688_product_id,
)
from app.services.audit import write_audit
from app.services.scoring import ScoreInput, calculate_score, excluded_reason

SENSITIVE_KEY_PARTS = {
    "address",
    "authorization",
    "cookie",
    "password",
    "phone",
    "secret",
    "token",
    "tracking",
}

CATEGORY_ROW_PATTERN = re.compile(r"\|(?:一级|二级|三级)类目\|([^|\n]+)\|")
DETAIL_PRICE_PATTERN = re.compile(r"# 商品价格\s*\n\s*([0-9]+(?:\.[0-9]+)?)\s*元")
SIZE_PATTERN = re.compile(
    r"约?\s*\d+(?:\.\d+)?(?:\s*[xX×*]\s*\d+(?:\.\d+)?){1,2}\s*(?:cm|厘米)?",
    re.IGNORECASE,
)


def sanitize_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def snapshot_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def start_sync_run(
    db,
    *,
    platform,
    adapter_name,
    adapter_version,
    operation,
    requested_by_user_id,
    correlation_id,
):
    run = IntegrationSyncRun(
        platform=platform,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        operation=operation,
        status="RUNNING",
        requested_by_user_id=requested_by_user_id,
        correlation_id=correlation_id,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    return run


def finish_sync_run(run, *, status, items_seen=0, items_written=0, error_code=None, message=None):
    run.status = status
    run.items_seen = items_seen
    run.items_written = items_written
    run.error_code = error_code
    run.safe_message = message
    run.finished_at = datetime.now(UTC)


def save_snapshot(
    db,
    *,
    run,
    platform,
    object_type,
    external_id,
    adapter_name,
    adapter_version,
    normalized_data,
    raw_payload,
):
    safe_raw_payload = sanitize_payload(raw_payload)
    digest = snapshot_hash(safe_raw_payload)
    existing = db.scalar(
        select(ExternalSnapshot).where(
            ExternalSnapshot.platform == platform,
            ExternalSnapshot.object_type == object_type,
            ExternalSnapshot.external_id == str(external_id),
            ExternalSnapshot.payload_hash == digest,
        )
    )
    if existing:
        return existing, False
    snapshot = ExternalSnapshot(
        sync_run_id=run.id if run else None,
        platform=platform,
        object_type=object_type,
        external_id=str(external_id),
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        payload_hash=digest,
        normalized_data=normalized_data,
        raw_payload=safe_raw_payload,
        fetched_at=datetime.now(UTC),
    )
    db.add(snapshot)
    db.flush()
    return snapshot, True


def _decimal(value):
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None


def _money_amount(value):
    if isinstance(value, dict):
        return value.get("amount")
    return value


def _promotion_tag_texts(value):
    if not isinstance(value, list):
        return []
    texts = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("value") or item.get("name") or item.get("title") or "").strip()
        else:
            text = ""
        if text:
            texts.append(text)
    return texts


def enrich_product_find_candidate(normalized_data, raw_detail):
    """Combine structured Product Find facts with signed Shopkeeper detail evidence.

    Missing freight is never guessed. Zero freight is accepted only when the
    supplier title explicitly states 包邮 or the signed Product Find payload has
    an exact, unconditional 包邮 promotion tag. Conditional terms remain a
    purchase-time recheck condition.
    """
    if not isinstance(raw_detail, str) or not raw_detail.strip():
        raise ValueError("1688 商品详情证据为空")
    data = json.loads(json.dumps(normalized_data, ensure_ascii=False, default=str))
    title = str(data.get("title") or "").strip()
    if not title or title not in raw_detail:
        raise ValueError("1688 搜索结果与商品详情标题不一致")

    categories = [value.strip() for value in CATEGORY_ROW_PATTERN.findall(raw_detail)]
    if categories:
        data["category"] = "/".join(dict.fromkeys(categories))

    detail_price_match = DETAIL_PRICE_PATTERN.search(raw_detail)
    detail_price = _decimal(detail_price_match.group(1)) if detail_price_match else None
    stats = dict(data.get("stats") or {})
    promotion_tags = _promotion_tag_texts(stats.get("promotion_tags"))
    title_free_shipping = "包邮" in title
    tag_free_shipping = "包邮" in promotion_tags
    free_shipping = title_free_shipping or tag_free_shipping
    conditional_shipping = (
        "新人专享包邮" in title
        or "新人包邮" in title
        or any("新人" in tag and "包邮" in tag for tag in promotion_tags)
    )
    skus = []
    for item in data.get("skus") or []:
        sku = {**item}
        price = _decimal(_money_amount(sku.get("price")))
        conservative_price = max(
            [value for value in (price, detail_price) if value is not None],
            default=None,
        )
        if conservative_price is not None:
            sku["price"] = {"amount": str(conservative_price), "currency": "CNY"}
        if free_shipping:
            sku["shipping"] = {"amount": "0", "currency": "CNY"}
        spec = dict(sku.get("spec") or {})
        sku_title = str(spec.get("title") or "").strip()
        if sku_title:
            spec["供应规格"] = sku_title
            size_match = SIZE_PATTERN.search(sku_title)
            if size_match:
                spec["尺寸"] = size_match.group(0).strip().removeprefix("约").strip()
        sku["spec"] = spec
        skus.append(sku)
    data["skus"] = skus

    shipping_evidence = None
    if title_free_shipping:
        shipping_evidence = "1688标题明确标注包邮"
    elif tag_free_shipping:
        shipping_evidence = "1688结构化促销标签明确标注包邮"
    stats.update(
        {
            "detail_verified": True,
            "detail_snapshot_hash": snapshot_hash({"raw_content": raw_detail}),
            "detail_base_price": str(detail_price) if detail_price is not None else None,
            "shipping_evidence": shipping_evidence,
            "shipping_condition": "NEW_BUYER_ONLY" if conditional_shipping else None,
            "requires_preorder_recheck": conditional_shipping,
            "recommended_operational_stock": 1 if conditional_shipping else None,
            "price_policy": "MAX_OF_SEARCH_SKU_AND_DETAIL_BASE",
        }
    )
    data["stats"] = {key: value for key, value in stats.items() if value is not None}
    data["adapter_verification"] = {
        "method": "AUTHORIZED_1688_PRODUCT_FIND_PLUS_SHOPKEEPER",
        "verified_at": datetime.now(UTC).isoformat(),
        "free_shipping_asserted_by_title": title_free_shipping,
        "free_shipping_asserted_by_promotion_tag": tag_free_shipping,
        "conditional_shipping": conditional_shipping,
    }
    return data


def upsert_sourcing_candidate(db, *, snapshot, adapter_name, adapter_version, data):
    external_id = str(data["external_product_id"])
    candidate = db.scalar(
        select(SourcingCandidate).where(
            SourcingCandidate.adapter_name == adapter_name,
            SourcingCandidate.external_product_id == external_id,
        )
    )
    if candidate is None:
        candidate = SourcingCandidate(
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            external_product_id=external_id,
            title=str(data.get("title") or "未命名商品"),
            last_fetched_at=datetime.now(UTC),
        )
        db.add(candidate)
    price = data.get("price")
    amount = _money_amount(price)
    skus = data.get("skus") or []
    stocks = [item.get("stock") for item in skus if item.get("stock") is not None]
    sku_prices = [_decimal(_money_amount(item.get("price"))) for item in skus]
    sku_prices = [item for item in sku_prices if item is not None]
    candidate.external_snapshot_id = snapshot.id
    candidate.adapter_version = adapter_version
    candidate.title = str(data.get("title") or candidate.title)
    candidate.url = data.get("url")
    candidate.image_url = data.get("image_url")
    candidate.category = data.get("category")
    candidate.external_supplier_id = data.get("external_supplier_id")
    candidate.supplier_name = data.get("supplier_name")
    candidate.minimum_price = _decimal(data.get("minimum_price") or amount) or (
        min(sku_prices) if sku_prices else None
    )
    candidate.maximum_price = _decimal(data.get("maximum_price") or amount) or (
        max(sku_prices) if sku_prices else None
    )
    candidate.sku_count = len(skus)
    candidate.stock = sum(stocks) if stocks else data.get("stock")
    score_inputs = data.get("score_inputs")
    if score_inputs:
        candidate.score = calculate_score(ScoreInput(**score_inputs)).total
    reason = excluded_reason(candidate.title, candidate.category or "")
    if reason:
        candidate.status = "EXCLUDED"
        data = {**data, "excluded_reason": reason}
    elif candidate.status == "EXCLUDED":
        candidate.status = "DISCOVERED"
    candidate.normalized_data = data
    candidate.last_fetched_at = datetime.now(UTC)
    return candidate


def serialize_candidate(candidate):
    skus = candidate.normalized_data.get("skus") or []
    complete_skus = bool(skus) and all(
        item.get("external_sku_id")
        and _money_amount(item.get("price")) is not None
        and _money_amount(item.get("shipping")) is not None
        and item.get("stock") is not None
        for item in skus
    )
    supplier_traceable = bool(
        candidate.external_supplier_id
        or (
            candidate.external_snapshot_id
            and candidate.supplier_name
            and candidate.adapter_name == "1688-product-find-cli"
        )
    )
    return {
        "id": candidate.id,
        "external_product_id": candidate.external_product_id,
        "adapter_name": candidate.adapter_name,
        "adapter_version": candidate.adapter_version,
        "title": candidate.title,
        "url": candidate.url,
        "image_url": candidate.image_url,
        "category": candidate.category,
        "supplier_name": candidate.supplier_name,
        "minimum_price": (
            str(candidate.minimum_price) if candidate.minimum_price is not None else None
        ),
        "maximum_price": (
            str(candidate.maximum_price) if candidate.maximum_price is not None else None
        ),
        "sku_count": candidate.sku_count,
        "stock": candidate.stock,
        "score": str(candidate.score) if candidate.score is not None else None,
        "status": candidate.status,
        "last_fetched_at": candidate.last_fetched_at,
        "import_ready": bool(
            complete_skus
            and supplier_traceable
            and candidate.supplier_name
            and candidate.category
            and candidate.status not in {"EXCLUDED", "IMPORTED"}
        ),
        "normalized_data": candidate.normalized_data,
    }


def verify_sourcing_candidate_manually(
    db,
    *,
    candidate,
    payload: CandidateManualVerificationRequest,
    actor_user_id,
    correlation_id,
):
    if candidate.status == "IMPORTED":
        raise ValueError("候选已经导入商品中心，不能覆盖供应证据")
    if candidate.status == "EXCLUDED":
        raise ValueError("已排除候选不能补全供应证据")
    source_product_id = extract_1688_product_id(payload.source_url)
    if source_product_id != candidate.external_product_id:
        raise ValueError("来源链接中的商品 ID 与候选不一致")

    before_state = {
        "status": candidate.status,
        "sku_count": candidate.sku_count,
        "stock": candidate.stock,
        "external_snapshot_id": candidate.external_snapshot_id,
    }
    verification_data = payload.model_dump(mode="json")
    normalized = {
        **(candidate.normalized_data or {}),
        "external_product_id": candidate.external_product_id,
        "title": candidate.title,
        "url": payload.source_url,
        "image_url": payload.image_url,
        "category": payload.category,
        "external_supplier_id": payload.external_supplier_id,
        "supplier_name": payload.supplier_name,
        "skus": verification_data["skus"],
        "manual_verification": {
            "method": payload.evidence_method,
            "captured_at": verification_data["captured_at"],
            "source_url": payload.source_url,
            "evidence_note": payload.evidence_note,
            "verified_by_user_id": actor_user_id,
        },
    }
    run = start_sync_run(
        db,
        platform="1688",
        adapter_name="manual-operator-verification",
        adapter_version="1.0",
        operation="VERIFY_SUPPLIER_CANDIDATE",
        requested_by_user_id=actor_user_id,
        correlation_id=correlation_id,
    )
    snapshot, created = save_snapshot(
        db,
        run=run,
        platform="1688",
        object_type="SUPPLIER_PRODUCT_VERIFICATION",
        external_id=candidate.external_product_id,
        adapter_name="manual-operator-verification",
        adapter_version="1.0",
        normalized_data=normalized,
        raw_payload={
            "candidate_id": candidate.id,
            "external_product_id": candidate.external_product_id,
            **verification_data,
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
    write_audit(
        db,
        action="SOURCING_CANDIDATE_MANUALLY_VERIFIED",
        entity_type="SOURCING_CANDIDATE",
        entity_id=candidate.id,
        actor_user_id=actor_user_id,
        actor_type="USER",
        before_data=before_state,
        after_data={
            "snapshot_id": snapshot.id,
            "snapshot_created": created,
            "sku_count": candidate.sku_count,
            "stock": candidate.stock,
            "source": "manual-operator-verification",
        },
        correlation_id=correlation_id,
    )
    return {
        "sync_run_id": run.id,
        "snapshot_id": snapshot.id,
        "snapshot_created": created,
        "candidate": serialize_candidate(candidate),
    }
