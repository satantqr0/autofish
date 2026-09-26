import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.models import (
    AIDecision,
    AutonomousLaunch,
    Product,
    ProductSKU,
    ProductSupplierLink,
    SourcingCandidate,
    SupplierProduct,
    SupplierSKU,
    XianyuDraft,
)
from app.schemas.autonomous import (
    CopyQAOutput,
    DimensionEvidenceOutput,
    ListingCopyOutput,
    VisionQAOutput,
)
from app.schemas.catalog import ProductCreate
from app.services.ai_inference import (
    AIInferenceError,
    chat_json,
    generate_reference_image,
    image_data_uri,
)
from app.services.audit import write_audit
from app.services.catalog import create_catalog_product, product_query_options
from app.services.publication_guard import (
    SELLER_SERVICE_COPY,
    assert_product_publishable,
    publication_copy_blockers,
    sanitize_publication_source_text,
)
from app.services.sourcing_adapter_verification import (
    CandidateAdapterVerificationError,
    verify_candidate_from_authorized_adapter,
)

PIPELINE_VERSION = "1.0"
MIN_SELECTION_SCORE = Decimal("70")
MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUTOMATIC_DETAIL_VERIFICATIONS = 5
DIMENSION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[x×*]\s*\d+(?:\.\d+)?\s*){1,2}(?:cm|mm|厘米|毫米)?",
    flags=re.IGNORECASE,
)
VISIBLE_MEASUREMENT_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:cm|mm|m|厘米|毫米|米)\b", flags=re.IGNORECASE
)
VISIBLE_CAPACITY_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:l|ml|升|毫升)\b", flags=re.IGNORECASE)
UNVERIFIED_CLAIMS = (
    "正品",
    "原装",
    "食品级",
    "母婴级",
    "医用级",
    "防水",
    "抗菌",
    "永久",
    "终身",
    "现货",
    "当天发",
    "24小时发货",
    "48小时发货",
)
INTERNAL_OR_SUPPLIER_COPY = (
    "新人专享",
    "新人价",
    "起批",
    "包邮",
    "来源图",
    "输入图",
    "参考图",
    "图片指令",
    "优化光线",
    "优化背景",
    "文字水印",
    "虚构配件",
    "事实依据",
    "事实一致",
    "商品真实结构",
    "配件数量",
    "非个人闲置",
    "购买经历",
    "不涉及",
    "有保障",
    "供应商",
    "直接供应",
    "厂家",
    "工厂",
    "源头",
    "经核验",
    "真实可查",
    "实物与描述一致",
    "确保购买安心",
    "品质可靠",
    "发货及时",
    "售后保障",
)
GENERIC_MARKETING_COPY = (
    "严选",
    "新款",
    "现货",
    "热卖",
    "爆款",
    "亚马逊",
    "跨境",
    "厂批发",
)


class LaunchManualRequired(RuntimeError):
    def __init__(self, code: str, message: str, blockers: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.blockers = blockers or [message]


def _json_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _money(value) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("amount")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _stage(launch: AutonomousLaunch, name: str, status: str, detail: dict | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    stages = [item for item in (launch.stages or []) if item.get("name") != name]
    stages.append({"name": name, "status": status, "at": now, "detail": detail or {}})
    launch.stages = stages
    launch.current_stage = name


def _decision(
    db: Session,
    launch: AutonomousLaunch,
    *,
    agent: str,
    input_payload: dict,
    decision: dict,
    confidence: Decimal,
    reason: str,
    provider: str,
    model: str,
    prompt_version: str,
) -> None:
    db.add(
        AIDecision(
            agent=agent,
            schema_version="1.0",
            input_context_hash=_json_hash(input_payload),
            decision=decision,
            confidence=confidence,
            reason_summary=reason[:2000],
            model=model[:100],
            provider=provider[:80],
            prompt_version=prompt_version[:40],
            product_id=launch.product_id,
            sourcing_candidate_id=launch.sourcing_candidate_id,
            autonomous_launch_id=launch.id,
        )
    )


def _candidate_sku(candidate: SourcingCandidate) -> dict | None:
    complete = []
    for sku in (candidate.normalized_data or {}).get("skus") or []:
        price = _money(sku.get("price"))
        shipping = _money(sku.get("shipping"))
        stock = sku.get("stock")
        if sku.get("external_sku_id") and price is not None and shipping is not None and stock:
            complete.append((price + shipping, -int(stock), str(sku["external_sku_id"]), sku))
    return min(complete, default=(None, None, None, None))[3]


def score_candidate(candidate: SourcingCandidate) -> dict:
    data = candidate.normalized_data or {}
    stats = data.get("stats") or {}
    sku = _candidate_sku(candidate)
    blockers: list[str] = []
    warnings: list[str] = []
    if candidate.status == "EXCLUDED":
        blockers.append(data.get("excluded_reason") or "候选属于排除类目")
    if not candidate.category:
        blockers.append("缺少已核验类目")
    if not candidate.supplier_name:
        blockers.append("缺少已核验供应商")
    if not candidate.image_url:
        blockers.append("缺少来源商品图")
    if sku is None:
        blockers.append("没有价格、运费和库存完整的可售 SKU")
    detail_verified = bool(stats.get("detail_verified"))
    adapter_method = (data.get("adapter_verification") or {}).get("method")
    if not detail_verified or not adapter_method:
        blockers.append("缺少授权 Adapter 的商品详情核验")
    age = datetime.now(UTC) - (
        candidate.last_fetched_at.replace(tzinfo=UTC)
        if candidate.last_fetched_at.tzinfo is None
        else candidate.last_fetched_at.astimezone(UTC)
    )
    if age > timedelta(days=7):
        blockers.append("供应事实快照超过 7 天")
    if stats.get("requires_preorder_recheck"):
        warnings.append("供应价或包邮条件具有时效性，下单前必须复核")

    evidence = Decimal("0")
    evidence += Decimal("15") if detail_verified else 0
    evidence += Decimal("10") if adapter_method else 0
    evidence += Decimal("5") if candidate.image_url else 0
    supply = Decimal("0")
    supply += Decimal("8") if candidate.supplier_name else 0
    supply += Decimal("4") if candidate.category else 0
    supply += Decimal("4") if candidate.sku_count else 0
    supply += Decimal("4") if candidate.stock and candidate.stock > 0 else 0
    economics = Decimal("0")
    stock_score = Decimal("0")
    if sku:
        price = _money(sku.get("price")) or Decimal("0")
        shipping = _money(sku.get("shipping")) or Decimal("0")
        total_cost = price + shipping
        economics = Decimal("10")
        assumed_sale = total_cost + Decimal("2") + Decimal("3") + Decimal("14")
        margin_ratio = Decimal("14") / assumed_sale if assumed_sale else Decimal("0")
        economics += min(Decimal("15"), margin_ratio * Decimal("35"))
        stock_score = min(Decimal("10"), Decimal(str(sku.get("stock") or 0)) / 100)
    risk = Decimal("15") if not blockers else Decimal("0")
    if stats.get("requires_preorder_recheck"):
        risk = max(Decimal("0"), risk - Decimal("3"))
    total = (evidence + supply + economics + stock_score + risk).quantize(Decimal("0.01"))
    if total < MIN_SELECTION_SCORE:
        blockers.append(f"可解释安全评分 {total} 低于门槛 {MIN_SELECTION_SCORE}")
    return {
        "candidate_id": candidate.id,
        "external_product_id": candidate.external_product_id,
        "total_score": str(total),
        "dimensions": {
            "evidence": str(evidence),
            "supply_traceability": str(supply),
            "economics": str(economics.quantize(Decimal("0.01"))),
            "stock": str(stock_score.quantize(Decimal("0.01"))),
            "risk": str(risk),
        },
        "selected_sku": sku,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
        "eligible": not blockers,
    }


def select_candidate(db: Session, launch: AutonomousLaunch) -> tuple[SourcingCandidate, dict]:
    request = launch.request_payload or {}
    candidate_id = request.get("candidate_id")
    if candidate_id:
        items = [db.get(SourcingCandidate, int(candidate_id))]
        if items[0] is None:
            raise LaunchManualRequired("CANDIDATE_NOT_FOUND", "指定的选品候选不存在")
    else:
        statement = select(SourcingCandidate).where(SourcingCandidate.status == "DISCOVERED")
        if request.get("query"):
            statement = statement.where(SourcingCandidate.title.ilike(f"%{request['query']}%"))
        items = db.scalars(
            statement.order_by(SourcingCandidate.last_fetched_at.desc()).limit(
                int(request.get("candidate_limit") or 50)
            )
        ).all()
    scored = [(item, score_candidate(item)) for item in items if item is not None]
    eligible = [(item, score) for item, score in scored if score["eligible"]]
    if not eligible:
        reasons = [
            f"候选 {score['candidate_id']}：{'；'.join(score['blockers'])}"
            for _item, score in scored[:10]
        ] or ["没有可评估的真实候选"]
        raise LaunchManualRequired("NO_ELIGIBLE_CANDIDATE", "没有通过安全门禁的候选", reasons)
    item, result = max(
        eligible,
        key=lambda pair: (Decimal(pair[1]["total_score"]), -pair[0].id),
    )
    result["considered"] = [
        {
            "candidate_id": score["candidate_id"],
            "total_score": score["total_score"],
            "eligible": score["eligible"],
            "blockers": score["blockers"],
        }
        for _candidate, score in scored
    ]
    return item, result


async def prepare_candidate_pool(db: Session, launch: AutonomousLaunch) -> dict:
    """Use authorized read-only adapters to complete candidate evidence before scoring."""
    request = launch.request_payload or {}
    candidate_id = request.get("candidate_id")
    if candidate_id:
        item = db.get(SourcingCandidate, int(candidate_id))
        items = [item] if item is not None else []
    else:
        statement = select(SourcingCandidate).where(
            SourcingCandidate.status == "DISCOVERED",
            SourcingCandidate.adapter_name == "1688-product-find-cli",
        )
        if request.get("query"):
            statement = statement.where(
                SourcingCandidate.title.ilike(f"%{request['query']}%")
            )
        items = db.scalars(
            statement.order_by(SourcingCandidate.last_fetched_at.desc()).limit(
                int(request.get("candidate_limit") or 50)
            )
        ).all()

    pending = [
        item
        for item in items
        if item is not None
        and item.status == "DISCOVERED"
        and item.adapter_name == "1688-product-find-cli"
        and not (
            (item.normalized_data or {}).get("stats", {}).get("detail_verified")
            and (item.normalized_data or {}).get("adapter_verification", {}).get("method")
        )
    ]
    pending.sort(key=lambda item: ("包邮" not in item.title, -item.id))
    verified: list[int] = []
    failed: list[dict] = []
    for candidate in pending[:MAX_AUTOMATIC_DETAIL_VERIFICATIONS]:
        try:
            await verify_candidate_from_authorized_adapter(
                db,
                candidate=candidate,
                actor_user_id=launch.requested_by_user_id,
                actor_type="SYSTEM",
                correlation_id=launch.correlation_id,
            )
            db.commit()
            verified.append(candidate.id)
        except CandidateAdapterVerificationError as exc:
            db.commit()
            failed.append(
                {
                    "candidate_id": candidate.id,
                    "code": exc.code or exc.status.value,
                    "message": exc.safe_message,
                    "retryable": exc.retryable,
                }
            )
        except ValueError as exc:
            db.commit()
            failed.append(
                {
                    "candidate_id": candidate.id,
                    "code": "INVALID_DETAIL_EVIDENCE",
                    "message": str(exc),
                    "retryable": False,
                }
            )
    return {
        "considered": len(items),
        "attempted": min(len(pending), MAX_AUTOMATIC_DETAIL_VERIFICATIONS),
        "verified_candidate_ids": verified,
        "failures": failed,
    }


def _verified_facts(candidate: SourcingCandidate, selection: dict) -> dict:
    data = candidate.normalized_data or {}
    sku = selection["selected_sku"]
    return {
        "source_title": candidate.title,
        "category": candidate.category,
        "supplier_name": candidate.supplier_name,
        "external_product_id": candidate.external_product_id,
        "sku": {
            "external_sku_id": sku.get("external_sku_id"),
            "spec": sku.get("spec") or {},
            "price": str(_money(sku.get("price"))),
            "shipping": str(_money(sku.get("shipping"))),
            "stock": int(sku.get("stock") or 0),
        },
        "verified_stats": {
            key: (data.get("stats") or {}).get(key)
            for key in (
                "detail_verified",
                "shipping_condition",
                "requires_preorder_recheck",
                "price_policy",
            )
            if (data.get("stats") or {}).get(key) is not None
        },
    }


def _copy_blockers(title: str, description: str, facts: dict) -> list[str]:
    blockers = publication_copy_blockers(title, description)
    combined = f"{title}\n{description}"
    source_text = json.dumps(facts, ensure_ascii=False)
    for claim in UNVERIFIED_CLAIMS:
        if claim in combined and claim not in source_text:
            blockers.append(f"文案包含未经来源证据支持的承诺：{claim}")
    allowed_dimensions = {
        re.sub(r"\s+", "", value).lower().replace("*", "×").replace("x", "×")
        for value in DIMENSION_PATTERN.findall(source_text)
    }
    for value in DIMENSION_PATTERN.findall(combined):
        normalized = re.sub(r"\s+", "", value).lower().replace("*", "×").replace("x", "×")
        if normalized not in allowed_dimensions:
            blockers.append(f"文案出现未经核验的尺寸：{value}")
    if len(title) > 30:
        blockers.append("标题超过闲鱼 30 字限制")
    return list(dict.fromkeys(blockers))


def _internal_copy_blockers(title: str, description: str, facts: dict) -> list[str]:
    combined = f"{title}\n{description}"
    blockers = publication_copy_blockers(combined, SELLER_SERVICE_COPY)
    blockers.extend(
        f"文案包含供应端条件或内部处理话术：{phrase}"
        for phrase in INTERNAL_OR_SUPPLIER_COPY
        if phrase in combined
    )
    supplier_name = str(facts.get("supplier_name") or "").strip()
    if supplier_name and supplier_name in combined:
        blockers.append("面向买家的文案不得出现供应商名称")
    return list(dict.fromkeys(blockers))


def _basis_supported(basis: str, normalized_source_text: str) -> bool:
    variants = [basis]
    for separator in (":", "："):
        if separator in basis:
            variants.append(basis.split(separator, 1)[1])
    return any(
        bool(normalized) and normalized in normalized_source_text
        for value in variants
        if (normalized := re.sub(r"\s+", "", value).lower().strip())
    )


def _copy_messages(facts: dict, image_count: int) -> list[dict]:
    schema = {
        "title": "不超过30个中文字符",
        "description": "80至350字",
        "image_prompts": [f"恰好{image_count}条参考图编辑指令"],
        "factual_basis": ["只列已核验事实"],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是 AutoFish 商品内容引擎。只能使用输入的已核验事实，不得猜测材质、品牌、功能、"
                "发货时效或库存承诺。不得出现一件代发、代发、供应链、货源、批发、铺货等货源话术；"
                "不得冒充个人闲置或声称购买经历。使用自然、简洁的商品介绍语气，不得写本人发货、"
                "本人负责销售、发货安排等卖家身份或履约自述。"
                "供应端的新人价、包邮、起批等采购条件不得写入面向买家的文案。文案只描述买家关心的"
                "商品信息，不得复述本提示、已核验事实、来源图、图片优化、质检、无水印等内部过程。"
                "图片指令必须要求保留商品真实结构、颜色、比例与配件数量，只改善光线、背景和构图，"
                "不要文字、水印、人物、手或虚构配件。只返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"verified_facts": facts, "required_output": schema},
                ensure_ascii=False,
            ),
        },
    ]


def _copy_review_messages(facts: dict, title: str, description: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是独立的闲鱼商品事实审核器。逐项检查标题和描述中的每一个商品属性、功能、用途、"
                "质量、包装、服务和承诺是否被已核验事实明确支持。常识推断也不能视为证据；未明确提供"
                "的耐用、稳固、防尘、适用物品、品质、色差、库存、发货时效和售后保证必须判为不支持。"
                "1688 的新人价、包邮、发货保障、退货运费等供应端条件不得作为闲鱼买家承诺。不得出现"
                "供应链、代发、图片生成、来源图、质检等内部过程。只返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "verified_facts": facts,
                    "listing": {"title": title, "description": description},
                    "required_checks": [
                        "factual_consistency",
                        "owner_voice（自然卖家语气且没有本人发货等身份自述）",
                        "no_supplier_context",
                        "buyer_relevance",
                        "confidence",
                        "unsupported_claims",
                        "blockers",
                        "summary",
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _review_copy(db: Session, launch: AutonomousLaunch, facts: dict, title: str, description: str):
    result = chat_json(
        db,
        task="vision_quality",
        messages=_copy_review_messages(facts, title, description),
        schema=CopyQAOutput,
        model_kind="vision",
    )
    output = result.data
    unsupported_claims = [
        item for item in output.unsupported_claims if not item.lower().endswith("_not_claimed")
    ]
    blockers = [item for item in output.blockers if not item.lower().startswith("no_")]
    checks = (
        output.factual_consistency,
        output.owner_voice,
        output.no_supplier_context,
        output.buyer_relevance,
    )
    passed = (
        all(checks)
        and output.confidence >= Decimal("0.90")
        and not unsupported_claims
        and not blockers
    )
    payload = {
        **output.model_dump(mode="json"),
        "raw_unsupported_claims": output.unsupported_claims,
        "raw_blockers": output.blockers,
        "unsupported_claims": unsupported_claims,
        "blockers": blockers,
        "passed": passed,
    }
    _decision(
        db,
        launch,
        agent="listing_copy_fact_checker",
        input_payload={"facts": facts, "title": title, "description": description},
        decision=payload,
        confidence=output.confidence,
        reason=output.summary,
        provider=result.provider,
        model=result.model,
        prompt_version="listing-copy-qa-1.0",
    )
    return payload, passed


def _fallback_copy(
    db: Session,
    launch: AutonomousLaunch,
    facts: dict,
    image_count: int,
    previous_blockers: list[str],
    fallback_reason: str | None = None,
) -> dict:
    source_title = str(facts.get("source_title") or "").strip()
    supplier_name = str(facts.get("supplier_name") or "").strip()
    clean_title = source_title.replace(supplier_name, "")
    for phrase in INTERNAL_OR_SUPPLIER_COPY:
        clean_title = clean_title.replace(phrase, "")
    clean_title = sanitize_publication_source_text(clean_title)
    for phrase in GENERIC_MARKETING_COPY:
        clean_title = clean_title.replace(phrase, "")
    clean_title = re.sub(r"[【】\[\]]", "", clean_title)
    clean_title = re.sub(r"\s+", "", clean_title).strip("，、；;|- ")
    clean_title = re.sub(r"(?:厂|O)$", "", clean_title).strip()

    spec = (facts.get("sku") or {}).get("spec") or {}
    spec_values = list(
        dict.fromkeys(
            sanitized
            for value in spec.values()
            if value
            and (sanitized := sanitize_publication_source_text(str(value).strip()))
        )
    )
    if not clean_title or not spec_values:
        raise LaunchManualRequired(
            "COPY_GUARD_BLOCKED",
            "模型文案未通过门禁，且已核验事实不足以生成安全回退文案",
        )
    spec_summary = max(spec_values, key=len)
    category_name = str(facts.get("category") or "商品").rsplit("/", 1)[-1]
    color_match = re.search(r"【([^】]+)】", spec_summary)
    pack_match = re.search(r"\d+\s*(?:个|件|只|套|盒|包)装", spec_summary)
    spec_title = "".join(
        part
        for part in (
            color_match.group(1) if color_match else "",
            category_name,
            pack_match.group(0).replace(" ", "") if pack_match else "",
        )
        if part
    )
    title = clean_title[:30] or spec_title[:30]
    description = f"{clean_title}。\n规格：{spec_summary}。\n\n{SELLER_SERVICE_COPY}"
    blockers = _copy_blockers(title, description, facts)
    blockers.extend(_internal_copy_blockers(title, description, facts))
    if blockers:
        raise LaunchManualRequired(
            "COPY_GUARD_BLOCKED", "安全回退文案仍未通过确定性门禁", blockers
        )
    prompts = [
        "保持商品本体完全不变，置于干净的暖白高端家居背景，柔和自然侧光，方形电商主图构图。",
        "保持商品本体完全不变，使用轻微俯拍展示真实结构与分隔细节，简洁高级背景，方形构图。",
        "保持商品本体完全不变，近景展示边缘、开合或收纳细节，真实材质光影，方形构图。",
        "保持商品本体完全不变，放在符合用途的整洁场景中，主体突出，方形构图。",
    ][:image_count]
    reason = fallback_reason or "连续三次模型文案未通过，已降级为仅含核验原文的确定性模板"
    decision = {
        "attempt": "DETERMINISTIC_FALLBACK",
        "title": title,
        "description": description,
        "image_prompts": prompts,
        "factual_basis": [source_title, *spec_values],
        "copy_qa": {
            "passed": True,
            "mode": "VERIFIED_FACT_TEMPLATE",
            "reason": reason,
            "previous_blockers": previous_blockers,
        },
    }
    _decision(
        db,
        launch,
        agent="listing_copy_fallback",
        input_payload=facts,
        decision=decision,
        confidence=Decimal("1.0"),
        reason=reason,
        provider="autofish-rules",
        model="verified-fact-template-1.0",
        prompt_version="listing-copy-fallback-1.0",
    )
    return decision


def _generate_copy(db: Session, launch: AutonomousLaunch, facts: dict) -> dict:
    requested_count = int((launch.request_payload or {}).get("image_count") or 2)
    base_messages = _copy_messages(facts, requested_count)
    messages = base_messages
    last_blockers: list[str] = []
    for attempt in range(1, 4):
        try:
            result = chat_json(
                db,
                task="product_copy",
                messages=messages,
                schema=ListingCopyOutput,
            )
        except AIInferenceError as exc:
            return _fallback_copy(
                db,
                launch,
                facts,
                requested_count,
                [f"{exc.code}: {exc.safe_message}"],
                fallback_reason="模型服务不可用，已使用仅含核验事实的离线文案模板",
            )
        output = result.data
        raw_blockers = _internal_copy_blockers(output.title, output.description, facts)
        title = sanitize_publication_source_text(output.title)[:30]
        description = sanitize_publication_source_text(output.description)
        if SELLER_SERVICE_COPY not in description:
            description = f"{description.rstrip()}\n\n{SELLER_SERVICE_COPY}"
        blockers = [*raw_blockers, *_copy_blockers(title, description, facts)]
        source_text = re.sub(r"\s+", "", json.dumps(facts, ensure_ascii=False)).lower()
        blockers.extend(
            f"模型列出的事实依据未在核验输入中找到：{basis}"
            for basis in output.factual_basis
            if not _basis_supported(basis, source_text)
        )
        blockers = list(dict.fromkeys(blockers))
        copy_qa = None
        if not blockers:
            copy_qa, copy_passed = _review_copy(db, launch, facts, title, description)
            if not copy_passed:
                blockers.extend(copy_qa["unsupported_claims"])
                blockers.extend(copy_qa["blockers"])
                if not blockers:
                    blockers.append(copy_qa["summary"])
                blockers = list(dict.fromkeys(blockers))
        if blockers:
            last_blockers = blockers
            rejected = {
                "attempt": attempt,
                "accepted": False,
                "title": title,
                "description": description,
                "blockers": blockers,
                "copy_qa": copy_qa,
                "provider_request_id": result.request_id,
                "usage": result.usage,
            }
            _decision(
                db,
                launch,
                agent="listing_copy_guard",
                input_payload={"facts": facts, "attempt": attempt},
                decision=rejected,
                confidence=Decimal("1.0"),
                reason="确定性门禁驳回模型文案并要求自动纠错",
                provider=result.provider,
                model=result.model,
                prompt_version="listing-copy-guard-1.0",
            )
            db.flush()
            if attempt < 3:
                messages = [
                    *base_messages,
                    {
                        "role": "assistant",
                        "content": json.dumps(output.model_dump(mode="json"), ensure_ascii=False),
                    },
                    {
                        "role": "user",
                        "content": (
                            "上次输出被确定性门禁驳回。请删除或改写所有违规内容，其他要求不变。"
                            "驳回原因："
                            f"{json.dumps(blockers, ensure_ascii=False)}。"
                            "只返回修正后的 JSON。"
                        ),
                    },
                ]
                continue
            break

        prompts = output.image_prompts[:requested_count]
        defaults = [
            "保持商品本体完全不变，置于干净的暖白高端家居背景，柔和自然侧光，方形电商主图构图。",
            "保持商品本体完全不变，使用轻微俯拍展示真实结构与分隔细节，简洁高级背景，方形构图。",
            "保持商品本体完全不变，近景展示边缘、开合或收纳细节，真实材质光影，方形构图。",
            "保持商品本体完全不变，放在符合用途的整洁场景中，主体突出，方形构图。",
        ]
        while len(prompts) < requested_count:
            prompts.append(defaults[len(prompts)])
        decision = {
            "attempt": attempt,
            "title": title,
            "description": description,
            "image_prompts": prompts,
            "factual_basis": output.factual_basis,
            "copy_qa": copy_qa,
            "provider_request_id": result.request_id,
            "usage": result.usage,
        }
        _decision(
            db,
            launch,
            agent="listing_copy_generator",
            input_payload=facts,
            decision=decision,
            confidence=Decimal("0.90"),
            reason="文案已通过确定性禁词、货主口吻和事实一致性校验",
            provider=result.provider,
            model=result.model,
            prompt_version="listing-copy-1.1",
        )
        return decision

    return _fallback_copy(db, launch, facts, requested_count, last_blockers)


def _assert_public_https(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise LaunchManualRequired("UNSAFE_IMAGE_URL", "图片地址必须是无凭证的 HTTPS 公网地址")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AIInferenceError("IMAGE_DNS_FAILED", "图片域名暂时无法解析", retryable=True) from exc
    for address in {item[4][0] for item in addresses}:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise LaunchManualRequired("UNSAFE_IMAGE_HOST", "图片地址解析到非公网网络")


def download_image(url: str, destination: Path) -> dict:
    _assert_public_https(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.1688.com/",
    }
    try:
        with httpx.stream(
            "GET",
            url,
            headers=headers,
            timeout=60,
            follow_redirects=False,
        ) as response:
            if response.status_code >= 500:
                raise AIInferenceError("IMAGE_UPSTREAM_ERROR", "图片服务暂时不可用", retryable=True)
            if response.status_code in {408, 420, 425, 429}:
                raise AIInferenceError(
                    "IMAGE_DOWNLOAD_RATE_LIMITED",
                    "图片服务暂时限流",
                    retryable=True,
                )
            if response.status_code != 200:
                raise LaunchManualRequired(
                    "IMAGE_DOWNLOAD_REJECTED", f"图片下载失败（HTTP {response.status_code}）"
                )
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise LaunchManualRequired("INVALID_IMAGE_TYPE", "远程文件不是受支持的商品图片")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_SOURCE_IMAGE_BYTES:
                    raise LaunchManualRequired("IMAGE_TOO_LARGE", "商品图片超过 10MB 限制")
    except httpx.TimeoutException as exc:
        raise AIInferenceError("IMAGE_DOWNLOAD_TIMEOUT", "图片下载超时", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise AIInferenceError(
            "IMAGE_DOWNLOAD_NETWORK", "图片下载网络错误", retryable=True
        ) from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        with Image.open(destination) as image:
            image.verify()
        with Image.open(destination) as image:
            width, height = image.size
            image_format = image.format
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise LaunchManualRequired("INVALID_IMAGE_FILE", "下载文件未通过图片完整性校验") from exc
    if min(width, height) < 384:
        destination.unlink(missing_ok=True)
        raise LaunchManualRequired("IMAGE_RESOLUTION_TOO_LOW", "来源图片分辨率低于 384 像素")
    return {
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "bytes": destination.stat().st_size,
        "mime_type": content_type,
        "format": image_format,
        "width": width,
        "height": height,
    }


def _asset_dir(launch: AutonomousLaunch) -> Path:
    root = Path(get_settings().asset_root).expanduser().resolve()
    return root / "autonomous" / str(launch.id)


def _asset_entry(launch: AutonomousLaunch, path: Path, kind: str, metadata: dict) -> dict:
    return {
        "filename": path.name,
        "relative_path": str(
            path.relative_to(Path(get_settings().asset_root).expanduser().resolve())
        ),
        "kind": kind,
        **metadata,
    }


def _create_source_preserving_premium_image(source_path: Path, target: Path) -> dict:
    """Build a premium square card without altering or inventing product pixels."""

    canvas_size = 1200
    with Image.open(source_path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    background = ImageOps.fit(
        source,
        (canvas_size, canvas_size),
        method=Image.Resampling.LANCZOS,
    ).filter(ImageFilter.GaussianBlur(radius=42))
    warm_veil = Image.new("RGB", background.size, "#F3EFE8")
    background = Image.blend(background, warm_veil, 0.78).convert("RGBA")

    foreground = ImageOps.contain(
        source,
        (1000, 1000),
        method=Image.Resampling.LANCZOS,
    )
    card_size = (foreground.width + 56, foreground.height + 56)
    card_mask = Image.new("L", card_size, 0)
    from PIL import ImageDraw

    ImageDraw.Draw(card_mask).rounded_rectangle(
        (0, 0, card_size[0] - 1, card_size[1] - 1),
        radius=28,
        fill=255,
    )
    card = Image.new("RGBA", card_size, (255, 255, 255, 0))
    card.paste((255, 255, 255, 250), (0, 0), card_mask)
    card.paste(foreground, (28, 28))

    position = (
        (canvas_size - card_size[0]) // 2,
        (canvas_size - card_size[1]) // 2,
    )
    shadow_mask = Image.new("L", (canvas_size, canvas_size), 0)
    shadow_mask.paste(card_mask, (position[0], position[1] + 18))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=24))
    shadow = Image.new("RGBA", (canvas_size, canvas_size), (30, 24, 18, 0))
    shadow.putalpha(shadow_mask.point(lambda value: int(value * 0.22)))
    background.alpha_composite(shadow)
    background.alpha_composite(card, position)

    target.parent.mkdir(parents=True, exist_ok=True)
    background.convert("RGB").save(target, format="PNG", optimize=True)
    content = target.read_bytes()
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "mime_type": "image/png",
        "format": "PNG",
        "width": canvas_size,
        "height": canvas_size,
        "source_preserving": True,
        "source_rect": {
            "x": position[0] + 28,
            "y": position[1] + 28,
            "width": foreground.width,
            "height": foreground.height,
        },
    }


def _local_image_qa(
    reason: AIInferenceError,
    *,
    candidate: SourcingCandidate,
    source: dict,
    facts: dict,
) -> dict:
    """Fail closed when the image model falls back to a framed source card.

    The local renderer is useful as a lossless preview because it never redraws
    the product.  It does not remove supplier copy, rebuild the scene, or create
    premium product photography, so it must never satisfy the publish image
    gate even when the source image has been bound to the selected SKU.
    """

    verification = (candidate.normalized_data or {}).get("image_sku_verification") or {}
    verified = bool(
        verification.get("method") in {"OPERATOR_VISUAL_REVIEW", "AUTHORIZED_VISION_REVIEW"}
        and verification.get("source_sha256") == source.get("sha256")
        and str(verification.get("external_sku_id") or "")
        == str((facts.get("sku") or {}).get("external_sku_id") or "")
    )
    blockers = ["离线加框图仅是来源图预览，不属于精品商品图"]
    if not verified:
        blockers.append("离线构图缺少与当前来源图和SKU绑定的图片规格核验记录")
    blockers.append("来源图中的促销文字和场景元素未经清理，必须使用可用图片模型或人工精品图")
    return {
        "structure_match": True,
        "color_match": True,
        "specification_consistent": verified,
        "no_added_accessories": True,
        "no_misleading_text": False,
        "premium_quality": False,
        "source_visible_text": [],
        "generated_visible_text": [],
        "unreadable_text": True,
        "confidence": "1.0",
        "blockers": blockers,
        "unexpected_measurements": [],
        "summary": (
            "离线加框图保持了已绑定SKU的商品像素，但未达到精品图标准，禁止进入发布"
            if verified
            else "离线加框图既未达到精品图标准，也未与当前SKU完成绑定核验，禁止进入发布"
        ),
        "passed": False,
        "provider": "autofish-local",
        "model": "source-preserving-card-1.0",
        "provider_request_id": None,
        "usage": {},
        "fallback_reason": {"code": reason.code, "message": reason.safe_message},
    }


def _vision_messages(source_path: Path, generated_path: Path, facts: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是商品图片事实质检器。图一是已核验来源图，图二是待发布精品图。"
                "严格比较商品结构、颜色、规格比例、配件数量和任何画面文字；不确定时必须判失败。"
                "必须逐字识别两张图中所有可见文字，尤其是每一个数字和长度单位，分别写入"
                "source_visible_text 与 generated_visible_text。任何文字看不清时 "
                "unreadable_text=true。"
                "图二出现图一和已核验事实中不存在的尺寸数字时 no_misleading_text 必须为 false。"
                "只返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_uri(source_path)}},
                {"type": "image_url", "image_url": {"url": image_data_uri(generated_path)}},
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "verified_facts": facts,
                            "checks": [
                                "structure_match",
                                "color_match",
                                "specification_consistent",
                                "no_added_accessories",
                                "no_misleading_text",
                                "premium_quality",
                                "source_visible_text",
                                "generated_visible_text",
                                "unreadable_text",
                                "confidence",
                                "blockers",
                                "summary",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        },
    ]


def _qa_image(db, launch, source_path, generated_path, facts) -> tuple[dict, bool]:
    result = chat_json(
        db,
        task="vision_quality",
        messages=_vision_messages(source_path, generated_path, facts),
        schema=VisionQAOutput,
        model_kind="vision",
    )
    output = result.data
    allowed_measurements = {
        value.lower().replace(" ", "")
        for value in VISIBLE_MEASUREMENT_PATTERN.findall(
            " ".join(output.source_visible_text) + " " + json.dumps(facts, ensure_ascii=False)
        )
    }
    generated_measurements = {
        value.lower().replace(" ", "")
        for value in VISIBLE_MEASUREMENT_PATTERN.findall(" ".join(output.generated_visible_text))
    }
    unexpected_measurements = sorted(generated_measurements - allowed_measurements)
    blockers = list(output.blockers)
    blockers.extend(f"精品图包含来源未核验的尺寸文字：{value}" for value in unexpected_measurements)
    blockers = list(dict.fromkeys(blockers))
    checks = (
        output.structure_match,
        output.color_match,
        output.specification_consistent,
        output.no_added_accessories,
        output.no_misleading_text,
        output.premium_quality,
        not output.unreadable_text,
    )
    passed = all(checks) and output.confidence >= Decimal("0.85") and not blockers
    payload = {
        **output.model_dump(mode="json"),
        "blockers": blockers,
        "unexpected_measurements": unexpected_measurements,
        "passed": passed,
        "provider": result.provider,
        "model": result.model,
        "provider_request_id": result.request_id,
        "usage": result.usage,
    }
    _decision(
        db,
        launch,
        agent="listing_image_fact_checker",
        input_payload={
            "facts": facts,
            "generated_sha256": hashlib.sha256(generated_path.read_bytes()).hexdigest(),
        },
        decision=payload,
        confidence=output.confidence,
        reason=output.summary,
        provider=result.provider,
        model=result.model,
        prompt_version="image-fact-qa-1.0",
    )
    return payload, passed


def _measurement_tokens(value: str) -> set[str]:
    patterns = (VISIBLE_MEASUREMENT_PATTERN, VISIBLE_CAPACITY_PATTERN)
    return {
        match.group(0).lower().replace(" ", "")
        for pattern in patterns
        for match in pattern.finditer(value)
    }


def _dimension_evidence_messages(source_path: Path, facts: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是商品尺寸证据提取器。只能逐字使用来源图可见文字或 verified_facts "
                "中明确出现的尺寸，"
                "禁止估算、推断或套用同类商品尺寸。measurements 写带单位的单项数值；"
                "layout_instructions 说明每个数值对应的长、宽、高、直径、挡板或其他明确部位；"
                "summary 是尺寸图底部可直接使用的汇总规格。方向、部位或数字任一项不确定时，"
                "available=false 并说明 blocker。只返回 JSON。"
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_uri(source_path)}},
                {
                    "type": "text",
                    "text": json.dumps({"verified_facts": facts}, ensure_ascii=False),
                },
            ],
        },
    ]


def _extract_dimension_evidence(
    db: Session,
    launch: AutonomousLaunch,
    source_path: Path,
    facts: dict,
) -> dict:
    cached = (launch.selection or {}).get("dimension_evidence") or {}
    if (
        cached.get("status") == "VERIFIED"
        and cached.get("measurements")
        and cached.get("layout_instructions")
        and cached.get("summary")
    ):
        return cached
    result = chat_json(
        db,
        task="vision_quality",
        messages=_dimension_evidence_messages(source_path, facts),
        schema=DimensionEvidenceOutput,
        model_kind="vision",
    )
    output = result.data
    evidence_blob = " ".join(output.evidence_text) + " " + json.dumps(facts, ensure_ascii=False)
    allowed = _measurement_tokens(evidence_blob)
    requested = _measurement_tokens(" ".join(output.measurements))
    blockers = list(output.blockers)
    blockers.extend(f"尺寸数值缺少来源证据：{value}" for value in sorted(requested - allowed))
    if not output.measurements:
        blockers.append("来源图和已核验规格中没有可用尺寸")
    if not output.layout_instructions:
        blockers.append("尺寸与商品部位的对应关系不明确")
    if not output.summary:
        blockers.append("缺少可核验的汇总尺寸")
    blockers = list(dict.fromkeys(blockers))
    passed = bool(output.available) and output.confidence >= Decimal("0.90") and not blockers
    payload = {
        **output.model_dump(mode="json"),
        "blockers": blockers,
        "status": "VERIFIED" if passed else "BLOCKED",
        "provider": result.provider,
        "model": result.model,
        "provider_request_id": result.request_id,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    selection = dict(launch.selection or {})
    selection["dimension_evidence"] = payload
    launch.selection = selection
    _decision(
        db,
        launch,
        agent="listing_dimension_evidence_extractor",
        input_payload={"facts": facts, "source_path": source_path.name},
        decision=payload,
        confidence=output.confidence,
        reason=(output.summary or "; ".join(blockers) or "尺寸证据提取完成"),
        provider=result.provider,
        model=result.model,
        prompt_version="dimension-evidence-1.0",
    )
    if not passed:
        raise LaunchManualRequired(
            "DIMENSION_EVIDENCE_MISSING",
            "商品缺少可核验尺寸，已停止生成尺寸图",
            blockers or ["尺寸证据不足"],
        )
    return payload


def _asset_role(item: dict) -> str:
    role = str(item.get("role") or "")
    if role:
        return role
    visible_text = (item.get("qa") or {}).get("generated_visible_text") or []
    return "dimensions" if _measurement_tokens(" ".join(visible_text)) else "clean"


def _image_pair_ready(manifest: list[dict], dimension_required: bool) -> bool:
    passed = [
        item
        for item in manifest
        if item.get("kind") == "generated" and (item.get("qa") or {}).get("passed")
    ]
    if not passed:
        return False
    if not dimension_required:
        return True
    roles = {_asset_role(item) for item in passed}
    return {"clean", "dimensions"}.issubset(roles)


def _generate_assets(db: Session, launch: AutonomousLaunch, candidate, facts) -> tuple[list, dict]:
    asset_dir = _asset_dir(launch)
    asset_dir.mkdir(parents=True, exist_ok=True)
    manifest = list(launch.asset_manifest or [])
    source = next((item for item in manifest if item.get("kind") == "source"), None)
    if source is None:
        source_path = asset_dir / "00-source.jpg"
        metadata = download_image(str(candidate.image_url), source_path)
        source = _asset_entry(
            launch, source_path, "source", {**metadata, "source_url": candidate.image_url}
        )
        manifest.append(source)
        launch.asset_manifest = manifest
        db.commit()
    else:
        source_path = Path(get_settings().asset_root) / source["relative_path"]
        if not source_path.is_file():
            raise AIInferenceError("SOURCE_ASSET_MISSING", "已记录的来源图片文件缺失")

    request = launch.request_payload or {}
    dimension_required = bool(request.get("dimension_image_required", False))
    requested_count = int(request.get("image_count") or 2)
    if dimension_required:
        requested_count = max(2, requested_count)
    prompts = (launch.ai_copy or {}).get("image_prompts") or []
    passed_assets = [
        item
        for item in manifest
        if item.get("kind") == "generated" and item.get("qa", {}).get("passed")
    ]
    qa_results = list((launch.vision_qa or {}).get("items") or [])
    for index in range(requested_count):
        role = "dimensions" if dimension_required and index == 1 else "clean"
        required_role_count = (
            1
            if role == "dimensions"
            else index if dimension_required and index > 1 else index + 1
        )
        if sum(1 for item in passed_assets if _asset_role(item) == role) >= required_role_count:
            continue
        base_prompt = prompts[index] if index < len(prompts) else prompts[-1]
        dimension_evidence = None
        if role == "dimensions":
            dimension_evidence = _extract_dimension_evidence(
                db, launch, source_path, facts
            )
            measurement_text = json.dumps(
                dimension_evidence["measurements"], ensure_ascii=False
            )
            layout_text = json.dumps(
                dimension_evidence["layout_instructions"], ensure_ascii=False
            )
            strict_prompt = (
                "保持来源图商品结构、颜色、数量和可见配件完全不变，制作1:1高端电商尺寸说明图。"
                "采用暖象牙白背景、商品居中、柔和自然阴影、细灰色尺寸延长线与双箭头、"
                "深灰无衬线字体、左上规格名、底部汇总尺寸，版式克制高级。\n"
                f"左上规格名：{dimension_evidence.get('variant_label') or '商品规格'}。\n"
                f"必须逐字使用尺寸：{measurement_text}。\n"
                f"箭头与部位对应：{layout_text}。\n"
                f"底部只写：{dimension_evidence['summary']}。\n"
                "数字、单位、小数点和方向必须完全正确；除上述规格与尺寸外，不得添加商标、水印、"
                "卖点、人物、手或其他文字。"
            )
        else:
            strict_prompt = (
                f"{base_prompt}\n以输入图中的商品为唯一主体和唯一事实依据。必须逐项保持商品结构、颜色、"
                "尺寸比例、分隔数量、边缘形状及可见配件完全一致；只允许优化背景、布光、清晰度和构图。"
                "不得添加文字、水印、Logo、人物、手、额外物品或未经证实的功能展示。"
            )
        accepted = False
        for attempt in (1, 2):
            target = asset_dir / f"{index + 1:02d}-premium-a{attempt}.png"
            if target.exists():
                target = asset_dir / f"{index + 1:02d}-{role}-a{attempt}.png"
            try:
                generated = generate_reference_image(
                    db,
                    source_path=source_path,
                    prompt=strict_prompt,
                    seed=launch.id * 1000 + index * 10 + attempt,
                )
                metadata = download_image(generated.data[0], target)
                qa, passed = _qa_image(db, launch, source_path, target, facts)
                provider = generated.provider
                model = generated.model
                provider_request_id = generated.request_id
            except AIInferenceError as exc:
                metadata = _create_source_preserving_premium_image(source_path, target)
                qa = _local_image_qa(
                    exc,
                    candidate=candidate,
                    source=source,
                    facts=facts,
                )
                passed = qa["passed"]
                provider = "autofish-local"
                model = "source-preserving-card-1.0"
                provider_request_id = None
                _decision(
                    db,
                    launch,
                    agent="listing_image_offline_fallback",
                    input_payload={
                        "facts": facts,
                        "source_sha256": source.get("sha256"),
                        "fallback_code": exc.code,
                    },
                    decision=qa,
                    confidence=Decimal(str(qa["confidence"])),
                    reason=qa["summary"],
                    provider=provider,
                    model=model,
                    prompt_version="source-preserving-card-1.0",
                )
            entry = _asset_entry(
                launch,
                target,
                "generated" if passed else "rejected",
                {
                    **metadata,
                    "role": role,
                    "dimension_evidence": dimension_evidence,
                    "prompt": strict_prompt,
                    "attempt": attempt,
                    "provider": provider,
                    "model": model,
                    "provider_request_id": provider_request_id,
                    "qa": qa,
                },
            )
            manifest.append(entry)
            qa_results.append({"filename": target.name, "role": role, **qa})
            launch.asset_manifest = list(manifest)
            launch.vision_qa = {
                "items": list(qa_results),
                "passed_count": len(passed_assets) + int(passed),
            }
            db.commit()
            if passed:
                passed_assets.append(entry)
                accepted = True
                break
        if not accepted:
            break
    vision = {
        "items": qa_results,
        "passed_count": len(passed_assets),
        "required_count": requested_count,
        "dimension_image_required": dimension_required,
        "dimension_image_passed": any(
            _asset_role(item) == "dimensions" for item in passed_assets
        ),
        "image_pair_ready": _image_pair_ready(manifest, dimension_required),
        "passed": len(passed_assets) >= requested_count
        and _image_pair_ready(manifest, dimension_required),
    }
    if not vision["passed"]:
        failed = [
            blocker
            for item in qa_results
            if not item.get("passed")
            for blocker in (item.get("blockers") or [item.get("summary") or "视觉质检未通过"])
        ]
        raise LaunchManualRequired(
            "IMAGE_QA_BLOCKED",
            "精品图未达到事实一致性门槛，已停止进入发布草稿",
            list(dict.fromkeys(failed)),
        )
    return manifest, vision


def _existing_product(db: Session, candidate: SourcingCandidate) -> Product | None:
    return db.scalar(
        select(Product)
        .join(ProductSKU, ProductSKU.product_id == Product.id)
        .join(ProductSupplierLink, ProductSupplierLink.product_sku_id == ProductSKU.id)
        .join(SupplierSKU, SupplierSKU.id == ProductSupplierLink.supplier_sku_id)
        .join(SupplierProduct, SupplierProduct.id == SupplierSKU.supplier_product_id)
        .where(SupplierProduct.external_product_id == candidate.external_product_id)
        .options(*product_query_options())
        .order_by(Product.id)
    )


def _import_or_update_product(db, launch, candidate, facts) -> Product:
    product = _existing_product(db, candidate)
    copy = launch.ai_copy
    generated_refs = [
        f"autofish-asset://launch/{launch.id}/{item['filename']}"
        for item in launch.asset_manifest
        if item.get("kind") == "generated" and item.get("qa", {}).get("passed")
    ]
    if product:
        before = {
            "title": product.title,
            "description": product.description,
            "images": product.images,
        }
        product.title = copy["title"]
        product.description = copy["description"]
        product.images = generated_refs
        write_audit(
            db,
            action="AUTONOMOUS_PRODUCT_CONTENT_UPDATED",
            entity_type="PRODUCT",
            entity_id=product.id,
            actor_type="SYSTEM",
            before_data=before,
            after_data={
                "title": product.title,
                "asset_count": len(generated_refs),
                "launch_id": launch.id,
            },
            correlation_id=launch.correlation_id,
        )
        return product
    sku = launch.selection["selected_sku"]
    request = launch.request_payload
    external_sku_id = str(sku["external_sku_id"])
    suffix = re.sub(r"[^A-Za-z0-9]", "", external_sku_id)[-12:] or str(launch.id)
    supplier_identity = candidate.external_supplier_id or candidate.external_product_id
    supplier_code = f"{candidate.adapter_name}:{supplier_identity}"[:80]
    stock = int(sku.get("stock") or 0)
    if facts.get("verified_stats", {}).get("requires_preorder_recheck"):
        stock = min(stock, 1)
    product = create_catalog_product(
        db,
        ProductCreate(
            supplier_code=supplier_code,
            supplier_name=str(candidate.supplier_name),
            supplier_source_type=candidate.adapter_name,
            external_product_id=candidate.external_product_id,
            external_supplier_id=candidate.external_supplier_id,
            supplier_url=candidate.url,
            external_sku_id=external_sku_id,
            internal_code=f"AF-{candidate.external_product_id}-{suffix}"[:100],
            sku_code=f"AF-{candidate.external_product_id}-{suffix}-SKU"[:120],
            title=copy["title"],
            category=str(candidate.category),
            description=copy["description"],
            images=[str(candidate.image_url)],
            spec=sku.get("spec") or {},
            supplier_price=_money(sku.get("price")) or Decimal("0"),
            shipping_cost=_money(sku.get("shipping")) or Decimal("0"),
            platform_fee=Decimal(str(request["platform_fee"])),
            after_sales_reserve=Decimal(str(request["after_sales_reserve"])),
            minimum_profit=Decimal(str(request["minimum_profit"])),
            target_profit=Decimal(str(request["target_profit"])),
            negotiation_margin=Decimal(str(request["negotiation_margin"])),
            stock=stock,
            lifecycle="TESTING",
            after_sales_safety=Decimal("70"),
            fault_safety=Decimal("75"),
            compatibility_safety=Decimal("75"),
            transport_safety=Decimal("80"),
            stock_stability=Decimal("70"),
            price_stability=Decimal("65"),
            verticality=Decimal("80"),
        ),
        actor_user_id=launch.requested_by_user_id,
        correlation_id=launch.correlation_id,
    )
    product.images = generated_refs
    candidate.status = "IMPORTED"
    return product


def _create_draft(
    db: Session, launch: AutonomousLaunch, product: Product, facts: dict
) -> XianyuDraft:
    product = db.scalar(
        select(Product).where(Product.id == product.id).options(*product_query_options())
    )
    assert_product_publishable(product)
    sku = product.skus[0]
    images = [
        f"autofish-asset://launch/{launch.id}/{item['filename']}"
        for item in launch.asset_manifest
        if item.get("kind") == "generated" and item.get("qa", {}).get("passed")
    ]
    blockers = publication_copy_blockers(launch.ai_copy["title"], launch.ai_copy["description"])
    if sku.recommended_price < sku.minimum_sale_price:
        blockers.append("推荐价格低于最低安全售价")
    if not images:
        blockers.append("没有通过视觉事实质检的精品图片")
    dimension_required = bool((launch.request_payload or {}).get("dimension_image_required", False))
    if not _image_pair_ready(launch.asset_manifest or [], dimension_required):
        blockers.append("缺少已核验的精品净图与精品尺寸图组合")
    warnings = list(launch.selection.get("warnings") or [])
    validation = {
        "passed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "requires_preorder_recheck": bool(
            facts.get("verified_stats", {}).get("requires_preorder_recheck")
        ),
        "vision_required_count": launch.vision_qa.get("required_count"),
        "vision_passed_count": launch.vision_qa.get("passed_count"),
        "dimension_image_required": dimension_required,
        "dimension_image_passed": launch.vision_qa.get("dimension_image_passed", False),
        "image_pair_ready": launch.vision_qa.get("image_pair_ready", False),
    }
    if blockers:
        raise LaunchManualRequired("DRAFT_GUARD_BLOCKED", "发布草稿未通过最终门禁", blockers)
    payload = {
        "launch_id": launch.id,
        "product_id": product.id,
        "title": launch.ai_copy["title"],
        "description": launch.ai_copy["description"],
        "price": str(sku.recommended_price),
        "category": product.category,
        "images": images,
        "asset_hashes": [
            item["sha256"] for item in launch.asset_manifest if item.get("kind") == "generated"
        ],
    }
    digest = _json_hash(payload)
    existing = db.scalar(
        select(XianyuDraft).where(
            XianyuDraft.product_id == product.id, XianyuDraft.input_hash == digest
        )
    )
    if existing:
        return existing
    version = db.scalar(
        select(func.max(XianyuDraft.version)).where(XianyuDraft.product_id == product.id)
    )
    draft = XianyuDraft(
        product_id=product.id,
        version=int(version or 0) + 1,
        title=launch.ai_copy["title"],
        description=launch.ai_copy["description"],
        price=sku.recommended_price,
        category=product.category,
        attributes={
            "autonomous_launch_id": launch.id,
            "source_candidate_id": launch.sourcing_candidate_id,
            "spec": sku.spec,
            "asset_manifest": launch.asset_manifest,
        },
        images=images,
        validation=validation,
        pipeline_trace=launch.stages,
        status="REVIEW_READY",
        input_hash=digest,
        created_by_user_id=launch.requested_by_user_id,
    )
    db.add(draft)
    db.flush()
    write_audit(
        db,
        action="AUTONOMOUS_XIANYU_DRAFT_CREATED",
        entity_type="XIANYU_DRAFT",
        entity_id=draft.id,
        actor_type="SYSTEM",
        actor_user_id=launch.requested_by_user_id,
        after_data={"launch_id": launch.id, "status": draft.status, "asset_count": len(images)},
        correlation_id=launch.correlation_id,
    )
    return draft


def _persist_draft_asset_manifest(
    launch: AutonomousLaunch,
    draft: XianyuDraft,
) -> None:
    """Make the draft's audited asset snapshot the final launch manifest.

    Real inference providers commit usage records while assets are generated. A
    final explicit assignment prevents an expired ORM instance from restoring an
    earlier source-only JSON value after the draft has already captured the
    complete, QA-approved manifest.
    """
    attributes = draft.attributes or {}
    if attributes.get("autonomous_launch_id") != launch.id:
        raise LaunchManualRequired(
            "DRAFT_ASSET_PROVENANCE_MISMATCH",
            "发布草稿与自主上架任务的图片来源不一致",
        )
    manifest = list(attributes.get("asset_manifest") or [])
    passed = [
        item
        for item in manifest
        if item.get("kind") == "generated" and (item.get("qa") or {}).get("passed")
    ]
    required = int((launch.request_payload or {}).get("image_count") or 2)
    if len(passed) < required:
        raise LaunchManualRequired(
            "DRAFT_ASSET_MANIFEST_INCOMPLETE",
            "发布草稿缺少已通过视觉质检的精品图清单",
            [f"需要 {required} 张，草稿证据中只有 {len(passed)} 张"],
        )
    dimension_required = bool((launch.request_payload or {}).get("dimension_image_required", False))
    if not _image_pair_ready(manifest, dimension_required):
        raise LaunchManualRequired(
            "DRAFT_DIMENSION_IMAGE_INCOMPLETE",
            "发布草稿缺少已核验的精品净图与精品尺寸图组合",
        )
    launch.asset_manifest = manifest
    # Production launches are SQLAlchemy mapped objects.  Some unit-level callers
    # intentionally use a lightweight namespace, so only invoke SQLAlchemy's
    # change tracker when ORM state is actually present.
    if hasattr(launch, "_sa_instance_state"):
        flag_modified(launch, "asset_manifest")


def _supersede_previous_outputs(db: Session, launch: AutonomousLaunch) -> None:
    if not launch.product_id or not launch.draft_id:
        return
    previous_drafts = db.scalars(
        select(XianyuDraft).where(
            XianyuDraft.product_id == launch.product_id,
            XianyuDraft.id != launch.draft_id,
            XianyuDraft.status == "REVIEW_READY",
        )
    ).all()
    for draft in previous_drafts:
        draft.status = "SUPERSEDED"

    previous_launches = db.scalars(
        select(AutonomousLaunch).where(
            AutonomousLaunch.id != launch.id,
            AutonomousLaunch.sourcing_candidate_id == launch.sourcing_candidate_id,
            AutonomousLaunch.status == "READY_FOR_BROWSER_HANDOFF",
        )
    ).all()
    for previous in previous_launches:
        previous.status = "SUPERSEDED"
        previous.error_code = "SUPERSEDED_BY_LAUNCH"
        previous.error_message = f"已由自主上架任务 {launch.id} 的合格结果取代"
        previous.blockers = [previous.error_message]
        _stage(
            previous,
            "BROWSER_HANDOFF",
            "SUPERSEDED",
            {"replacement_launch_id": launch.id, "replacement_draft_id": launch.draft_id},
        )
    if previous_drafts or previous_launches:
        write_audit(
            db,
            action="AUTONOMOUS_PREVIOUS_OUTPUTS_SUPERSEDED",
            entity_type="AUTONOMOUS_LAUNCH",
            entity_id=launch.id,
            actor_type="SYSTEM",
            actor_user_id=launch.requested_by_user_id,
            after_data={
                "draft_ids": [item.id for item in previous_drafts],
                "launch_ids": [item.id for item in previous_launches],
            },
            correlation_id=launch.correlation_id,
        )


def serialize_launch(launch: AutonomousLaunch) -> dict:
    return {
        "id": launch.id,
        "automation_job_id": launch.automation_job_id,
        "sourcing_candidate_id": launch.sourcing_candidate_id,
        "product_id": launch.product_id,
        "draft_id": launch.draft_id,
        "status": launch.status,
        "current_stage": launch.current_stage,
        "idempotency_key": launch.idempotency_key,
        "selection": launch.selection,
        "ai_copy": launch.ai_copy,
        "asset_manifest": launch.asset_manifest,
        "vision_qa": launch.vision_qa,
        "stages": launch.stages,
        "blockers": launch.blockers,
        "error_code": launch.error_code,
        "error_message": launch.error_message,
        "created_at": launch.created_at,
        "updated_at": launch.updated_at,
        "started_at": launch.started_at,
        "finished_at": launch.finished_at,
        "handoff": {
            "required_actor": "LOCAL_BROWSER_BRIDGE_OR_OPERATOR_ON_EXCEPTION",
            "scope": "LOGIN_SESSION_EXECUTION_AND_EXCEPTION_HANDLING",
            "external_submission_performed": launch.status == "PUBLISHED",
        },
    }


def execute_autonomous_launch(db: Session, launch_id: int) -> dict:
    launch = db.get(AutonomousLaunch, launch_id)
    if launch is None:
        raise ValueError("autonomous launch not found")
    if launch.status == "READY_FOR_BROWSER_HANDOFF":
        _supersede_previous_outputs(db, launch)
        db.commit()
        return serialize_launch(launch)
    launch.status = "RUNNING"
    launch.started_at = launch.started_at or datetime.now(UTC)
    launch.error_code = None
    launch.error_message = None
    launch.blockers = []
    db.commit()
    try:
        if not launch.sourcing_candidate_id:
            _stage(launch, "SOURCE_EVIDENCE_ENRICHMENT", "RUNNING")
            db.commit()
            enrichment = asyncio.run(prepare_candidate_pool(db, launch))
            _stage(
                launch,
                "SOURCE_EVIDENCE_ENRICHMENT",
                "OK" if not enrichment["failures"] else "COMPLETED_WITH_WARNINGS",
                enrichment,
            )
            _stage(launch, "CANDIDATE_SELECTION", "RUNNING")
            db.commit()
            candidate, selection = select_candidate(db, launch)
            launch.sourcing_candidate_id = candidate.id
            launch.selection = selection
            _decision(
                db,
                launch,
                agent="candidate_safety_selector",
                input_payload={
                    "request": launch.request_payload,
                    "considered": selection["considered"],
                },
                decision=selection,
                confidence=Decimal("1.0"),
                reason="候选通过来源、SKU、成本、库存、新鲜度和排除类目的确定性门禁",
                provider="autofish-rules",
                model="candidate-scorer-1.0",
                prompt_version="selection-1.0",
            )
            _stage(
                launch,
                "CANDIDATE_SELECTION",
                "OK",
                {"candidate_id": candidate.id, "score": selection["total_score"]},
            )
            db.commit()
        candidate = db.get(SourcingCandidate, launch.sourcing_candidate_id)
        facts = _verified_facts(candidate, launch.selection)

        if not launch.ai_copy:
            _stage(launch, "AI_COPY", "RUNNING")
            db.commit()
            launch.ai_copy = _generate_copy(db, launch, facts)
            _stage(launch, "AI_COPY", "OK", {"title": launch.ai_copy["title"]})
            db.commit()

        requested_count = int(launch.request_payload.get("image_count") or 2)
        dimension_required = bool(
            launch.request_payload.get("dimension_image_required", False)
        )
        if dimension_required:
            requested_count = max(2, requested_count)
        passed_assets = [
            item
            for item in launch.asset_manifest or []
            if item.get("kind") == "generated" and item.get("qa", {}).get("passed")
        ]
        if len(passed_assets) < requested_count or not _image_pair_ready(
            launch.asset_manifest or [], dimension_required
        ):
            _stage(launch, "AI_IMAGE_AND_VISION_QA", "RUNNING")
            db.commit()
            launch.asset_manifest, launch.vision_qa = _generate_assets(db, launch, candidate, facts)
            _stage(
                launch,
                "AI_IMAGE_AND_VISION_QA",
                "OK",
                {"passed": launch.vision_qa["passed_count"], "required": requested_count},
            )
            db.commit()

        if not launch.product_id:
            _stage(launch, "CATALOG_IMPORT", "RUNNING")
            db.commit()
            product = _import_or_update_product(db, launch, candidate, facts)
            db.flush()
            launch.product_id = product.id
            _stage(launch, "CATALOG_IMPORT", "OK", {"product_id": product.id})
            db.commit()
        product = db.get(Product, launch.product_id)

        if not launch.draft_id:
            _stage(launch, "DRAFT_BUILD", "RUNNING")
            db.commit()
            draft = _create_draft(db, launch, product, facts)
            launch.draft_id = draft.id
            _persist_draft_asset_manifest(launch, draft)
            _stage(launch, "DRAFT_BUILD", "OK", {"draft_id": draft.id, "status": draft.status})
            db.commit()

        _supersede_previous_outputs(db, launch)
        _stage(
            launch,
            "BROWSER_HANDOFF",
            "READY",
            {"external_submission": False, "actor": "LOCAL_BROWSER_BRIDGE"},
        )
        launch.status = "READY_FOR_BROWSER_HANDOFF"
        launch.finished_at = datetime.now(UTC)
        write_audit(
            db,
            action="AUTONOMOUS_LAUNCH_READY",
            entity_type="AUTONOMOUS_LAUNCH",
            entity_id=launch.id,
            actor_type="SYSTEM",
            actor_user_id=launch.requested_by_user_id,
            after_data={
                "candidate_id": launch.sourcing_candidate_id,
                "product_id": launch.product_id,
                "draft_id": launch.draft_id,
                "asset_count": len(
                    [item for item in launch.asset_manifest if item.get("kind") == "generated"]
                ),
                "external_submission": False,
            },
            correlation_id=launch.correlation_id,
        )
        db.commit()
        return serialize_launch(launch)
    except LaunchManualRequired as exc:
        launch.status = "MANUAL_REQUIRED"
        launch.error_code = exc.code
        launch.error_message = exc.safe_message
        launch.blockers = exc.blockers
        launch.finished_at = datetime.now(UTC)
        _stage(
            launch, launch.current_stage, "BLOCKED", {"code": exc.code, "blockers": exc.blockers}
        )
        db.commit()
        raise
    except AIInferenceError as exc:
        db.rollback()
        launch = db.get(AutonomousLaunch, launch_id)
        launch.status = "RETRY" if exc.retryable else "MANUAL_REQUIRED"
        launch.error_code = exc.code
        launch.error_message = exc.safe_message
        launch.blockers = [exc.safe_message]
        _stage(
            launch,
            launch.current_stage,
            "RETRY" if exc.retryable else "BLOCKED",
            {"code": exc.code, "message": exc.safe_message},
        )
        db.commit()
        raise
