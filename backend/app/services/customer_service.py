import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import (
    AIDecision,
    Conversation,
    ManualTask,
    Message,
    Product,
    ProductSKU,
)
from app.services.ai_inference import AIInferenceError, chat_json

HIGH_RISK_PATTERNS = {
    "REFUND": ("退款", "退钱", "仅退款"),
    "RETURN": ("退货", "换货"),
    "COMPLAINT": ("投诉", "举报", "差评", "曝光"),
    "PLATFORM_INTERVENTION": ("平台介入", "小法庭", "客服介入"),
    "COMPENSATION": ("赔偿", "补偿", "索赔"),
    "LEGAL": ("律师", "起诉", "法院", "违法", "消协"),
}
INTENT_PATTERNS = {
    "STOCK": ("有货", "库存", "现货", "还有吗"),
    "SHIPPING": ("什么时候发", "多久发", "发货", "什么快递", "哪家快递"),
    "LOGISTICS": ("物流", "快递到哪", "到哪里", "几天到"),
    "COMPATIBILITY": ("兼容", "支持", "适配", "能不能用", "可以用吗"),
    "SPEC": ("尺寸", "材质", "规格", "颜色", "型号", "多大"),
    "NEGOTIATION": (
        "便宜",
        "最低",
        "优惠",
        "少点",
        "刀",
        "包邮",
        "元可以",
        "这个价",
        "多少钱",
        "怎么卖",
        "价格",
    ),
    "GREETING": ("你好", "您好", "在吗", "哈喽", "hello"),
}
CURRENCY_PRICE_PATTERN = re.compile(
    r"(?:[¥￥]\s*(\d{1,6}(?:\.\d{1,2})?)|"
    r"(?<!\d)(\d{1,6}(?:\.\d{1,2})?)\s*(?:元|块)(?!\d))"
)
PLAIN_OFFER_PATTERN = re.compile(
    r"(?<!\d)(\d{1,6}(?:\.\d{1,2})?)\s*"
    r"(?:可以吗|行吗|能出吗|能卖吗|卖吗|收吗|包邮吗)"
)
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?")
STOCK_FACT_PATTERN = re.compile(
    r"(?:库存|现货|剩余|还剩)[^0-9]{0,10}(\d+(?:\.\d+)?)"
    r"\s*(?:件|个|套|只|台|盒|包)?|"
    r"(\d+(?:\.\d+)?)\s*(?:件|个|套|只|台|盒|包)\s*(?:库存|现货)"
)
LOAD_FACT_PATTERN = re.compile(
    r"(?:承重|载重|负重)[^0-9]{0,8}(\d+(?:\.\d+)?)|"
    r"(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)",
    flags=re.IGNORECASE,
)
FORBIDDEN_REPLY_TERMS = (
    "一键代发",
    "一件代发",
    "支持代发",
    "代发货",
    "供应商直发",
    "保证到货",
    "保证发货",
    "全网最低",
)
SENSITIVE_FACT_TERMS = {
    "真伪": ("正品", "真品", "原装", "官方授权", "全新"),
    "质保": ("质保", "保修", "包退", "包换", "无理由退换", "终身"),
    "进口": ("进口", "原产", "海外原装"),
    "材质": ("材质", "材料", "食品级", "医用级", "母婴级", "航空级"),
    "承重": ("承重", "载重", "负重"),
}
MATERIAL_FACT_TERMS = (
    "铝合金",
    "钛合金",
    "不锈钢",
    "碳纤维",
    "亚克力",
    "硅胶",
    "橡胶",
    "尼龙",
    "陶瓷",
    "玻璃",
    "实木",
    "竹制",
    "纯棉",
    "涤纶",
    "真皮",
    "金属",
    "塑料",
    "abs",
    "pp",
    "pc",
    "pet",
    "pu",
)


class CustomerReplyDraft(BaseModel):
    reply: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=300)


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _intent(text: str) -> tuple[str, bool, str]:
    lowered = text.casefold()
    for intent, patterns in HIGH_RISK_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return intent, True, f"命中高风险意图 {intent}"
    for intent, patterns in INTENT_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return intent, False, f"命中低风险意图 {intent}"
    return "UNKNOWN", True, "无法可靠识别意图"


def _spec_text(product: Product) -> str | None:
    if not product.skus:
        return None
    spec = product.skus[0].spec or {}
    if not spec:
        return None
    return "；".join(f"{key}：{value}" for key, value in list(spec.items())[:8])


def _price_from_message(text: str) -> Decimal | None:
    contextual_matches = CURRENCY_PRICE_PATTERN.findall(text)
    values = [left or right for left, right in contextual_matches]
    if not values:
        values = PLAIN_OFFER_PATTERN.findall(text)
    if not values:
        return None
    try:
        return Decimal(values[-1])
    except (InvalidOperation, ValueError):
        return None


def _negotiation_prices(sku: ProductSKU) -> tuple[Decimal, Decimal]:
    """Return public reference and safe counter prices without exposing the hard floor."""
    floor = Decimal(sku.minimum_sale_price)
    reference = Decimal(sku.current_sale_price or sku.recommended_price)
    reference = max(reference, floor)
    margin = max(Decimal(sku.negotiation_margin or 0), Decimal("0"))
    counter = max(reference - margin, floor)
    return reference, counter


def _build_reply(intent: str, text: str, product: Product | None) -> tuple[str | None, list[str]]:
    blockers: list[str] = []
    sku: ProductSKU | None = product.skus[0] if product and product.skus else None
    if intent == "STOCK":
        if sku is None:
            return None, ["缺少商品 SKU 库存事实"]
        if sku.stock <= 0:
            return "您好，这款当前库存不足，暂时不建议下单。我确认补货后再回复您。", []
        return f"您好，这款当前系统库存为 {sku.stock} 件。下单前我会再核对一次实时库存和规格。", []
    if intent == "SPEC":
        details = _spec_text(product) if product else None
        if not details:
            return None, ["缺少可核验的商品规格"]
        return f"您好，当前商品资料中的规格是：{details}。请以您要购买的具体规格为准。", []
    if intent == "SHIPPING":
        return (
            "您好，发货时间和承运快递需要按下单时的实时库存与供应链情况确认；"
            "下单前我可以先帮您核对。",
            [],
        )
    if intent == "LOGISTICS":
        return None, ["物流咨询必须绑定可核验订单"]
    if intent == "COMPATIBILITY":
        compatibility = product.compatibility if product else None
        if not compatibility:
            return None, ["缺少明确兼容性事实"]
        details = "；".join(f"{key}：{value}" for key, value in list(compatibility.items())[:8])
        return (
            f"您好，当前已核验的兼容信息是：{details}。如果您的具体型号不在其中，请先不要下单。",
            [],
        )
    if intent == "NEGOTIATION":
        if sku is None:
            return None, ["缺少最低安全售价"]
        reference_price, counter_price = _negotiation_prices(sku)
        offered = _price_from_message(text)
        if offered is None:
            return (
                f"您好，这款当前标价是 ¥{reference_price:.2f}。"
                "如果您确认好规格和数量，我可以再帮您核对当前可用优惠。",
                [],
            )
        if offered < counter_price:
            return (
                f"您好，¥{offered:.2f} 暂时无法成交，"
                f"当前可确认到 ¥{counter_price:.2f}。成交前还需要核对规格与实时库存。",
                [],
            )
        return (
            f"您好，¥{offered:.2f} 在当前可协商范围内，可以继续确认具体规格与实时库存。",
            [],
        )
    if intent == "GREETING":
        return "您好，我在。请告诉我想确认的商品规格、库存或发货问题。", []
    blockers.append("该咨询不在低风险自动回复范围")
    return None, blockers


def _canonical_number(value: str) -> str:
    try:
        normalized = format(Decimal(value).normalize(), "f")
    except InvalidOperation:
        return value
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _reply_numbers(value: str) -> set[str]:
    return {_canonical_number(match) for match in NUMBER_PATTERN.findall(value)}


def _pattern_numbers(pattern: re.Pattern, value: str) -> set[str]:
    numbers: set[str] = set()
    for match in pattern.finditer(value):
        numbers.update(
            _canonical_number(group)
            for group in match.groups()
            if group is not None
        )
    return numbers


def _typed_reply_numbers(value: str) -> dict[str, set[str]]:
    price_matches = CURRENCY_PRICE_PATTERN.findall(value)
    return {
        "price": {
            _canonical_number(left or right)
            for left, right in price_matches
            if left or right
        },
        "stock": _pattern_numbers(STOCK_FACT_PATTERN, value),
        "load": _pattern_numbers(LOAD_FACT_PATTERN, value),
    }


def _contains_fact_term(value: str, term: str) -> bool:
    if term.isascii() and term.isalnum():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", value))
    return term in value


def _unsupported_sensitive_claims(guarded_reply: str, drafted_reply: str) -> list[str]:
    guarded = re.sub(r"\s+", "", guarded_reply).casefold()
    drafted = re.sub(r"\s+", "", drafted_reply).casefold()
    unsupported = [
        label
        for label, terms in SENSITIVE_FACT_TERMS.items()
        if any(
            _contains_fact_term(drafted, term.casefold())
            and not _contains_fact_term(guarded, term.casefold())
            for term in terms
        )
    ]
    if any(
        _contains_fact_term(drafted, term)
        and not _contains_fact_term(guarded, term)
        for term in MATERIAL_FACT_TERMS
    ):
        unsupported.append("材质")
    return list(dict.fromkeys(unsupported))


def _ai_reply(
    db,
    *,
    intent: str,
    buyer_message: str,
    guarded_reply: str,
    product: Product | None,
) -> tuple[str, str, str, str, dict]:
    """Polish a guarded reply without allowing the model to invent facts or prices."""
    result = chat_json(
        db,
        task="customer_service",
        schema=CustomerReplyDraft,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是闲鱼卖家的客服助手。只能改写给定的安全回复，不得新增价格、库存、规格、"
                    "发货时效、快递、赠品、售后承诺或身份信息。不要提及供应商、代发、一键代发、"
                    "模型或系统。不得把库存数字改写成价格或把价格改写成库存；不得新增正品、质保、"
                    "进口、材质或承重声明。语气自然、简短，以货主角度答复。只返回 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"意图：{intent}\n"
                    f"商品：{product.title if product else '未关联商品'}\n"
                    f"买家消息：{buyer_message[:1000]}\n"
                    f"唯一允许使用的事实回复：{guarded_reply}\n"
                    "请在不改变任何事实和数字的前提下，使回复更自然。"
                ),
            },
        ],
    )
    drafted = result.data.reply.strip()
    if any(term in drafted for term in FORBIDDEN_REPLY_TERMS):
        raise AIInferenceError("AI_REPLY_FORBIDDEN_COPY", "模型回复命中禁用表述")
    guarded_numbers = _reply_numbers(guarded_reply)
    drafted_numbers = _reply_numbers(drafted)
    if drafted_numbers != guarded_numbers:
        raise AIInferenceError("AI_REPLY_FACT_DRIFT", "模型回复中的数字与事实模板不一致")
    if _typed_reply_numbers(drafted) != _typed_reply_numbers(guarded_reply):
        raise AIInferenceError("AI_REPLY_FACT_DRIFT", "模型回复改变了价格、库存或承重数字的语义")
    unsupported_claims = _unsupported_sensitive_claims(guarded_reply, drafted)
    if unsupported_claims:
        raise AIInferenceError(
            "AI_REPLY_FACT_DRIFT",
            f"模型回复新增未核验的{'、'.join(unsupported_claims)}声明",
        )
    return drafted, result.provider, result.model, result.data.reason, result.usage


def _manual_task(db, conversation: Conversation, reason: str):
    exists = db.scalar(
        select(ManualTask).where(
            ManualTask.type == "CUSTOMER_SERVICE_TAKEOVER",
            ManualTask.entity_type == "CONVERSATION",
            ManualTask.entity_id == str(conversation.id),
            ManualTask.status == "OPEN",
        )
    )
    if exists is None:
        db.add(
            ManualTask(
                type="CUSTOMER_SERVICE_TAKEOVER",
                title="客服会话需要人工接管",
                reason=reason[:2000],
                priority=90,
                entity_type="CONVERSATION",
                entity_id=str(conversation.id),
            )
        )


def generate_reply_suggestion(db, conversation_id: int) -> AIDecision:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError("会话不存在")
    message = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    if message is None or message.direction != "INBOUND":
        raise ValueError("会话没有待处理的买家消息")
    context_hash = _hash(
        {
            "conversation_id": conversation.id,
            "message_id": message.id,
            "external_message_id": message.external_message_id,
            "content": message.content,
            "product_id": conversation.product_id,
        }
    )
    existing = db.scalar(
        select(AIDecision).where(
            AIDecision.agent == "CUSTOMER_ROUTER",
            AIDecision.input_context_hash == context_hash,
        )
    )
    if existing:
        return existing
    product = (
        db.scalar(select(Product).where(Product.id == conversation.product_id))
        if conversation.product_id
        else None
    )
    intent, requires_human, reason = _intent(message.content)
    reply, blockers = _build_reply(intent, message.content, product)
    if blockers:
        requires_human = True
        reason = "; ".join(blockers)
    provider = "local"
    model = "deterministic-policy-engine"
    prompt_version = "customer-policy-1.1"
    model_usage: dict = {}
    generation_mode = "POLICY_FALLBACK"
    if reply and not requires_human:
        try:
            reply, provider, model, model_reason, model_usage = _ai_reply(
                db,
                intent=intent,
                buyer_message=message.content,
                guarded_reply=reply,
                product=product,
            )
            reason = f"{reason}；大模型在事实模板内润色：{model_reason}"
            generation_mode = "MODEL_GUARDED"
        except AIInferenceError as exc:
            reason = f"{reason}；大模型不可用，已使用本地安全模板（{exc.safe_message}）"
    decision = {
        "intent": intent,
        "requires_human": requires_human,
        "auto_eligible": bool(reply and not requires_human and not conversation.manual_mode),
        "reply": reply,
        "blockers": blockers,
        "source_message_id": message.id,
        "external_message_id": message.external_message_id,
        "recipient_id": conversation.customer.external_customer_id,
        "generation_mode": generation_mode,
        "model_usage": model_usage,
    }
    item = AIDecision(
        agent="CUSTOMER_ROUTER",
        schema_version="1.0",
        input_context_hash=context_hash,
        decision=decision,
        confidence=Decimal("0.95") if not requires_human else Decimal("0.60"),
        reason_summary=reason,
        model=model,
        provider=provider,
        prompt_version=prompt_version,
        conversation_id=conversation.id,
        product_id=conversation.product_id,
    )
    db.add(item)
    if requires_human:
        conversation.manual_mode = True
        conversation.manual_reason = reason[:300]
        _manual_task(db, conversation, reason)
    db.commit()
    db.refresh(item)
    return item


def serialize_suggestion(item: AIDecision) -> dict:
    return {
        "id": item.id,
        "conversation_id": item.conversation_id,
        "intent": item.decision.get("intent"),
        "reply": item.decision.get("reply"),
        "auto_eligible": item.decision.get("auto_eligible", False),
        "requires_human": item.decision.get("requires_human", True),
        "blockers": item.decision.get("blockers", []),
        "recipient_id": item.decision.get("recipient_id"),
        "reason": item.reason_summary,
        "confidence": str(item.confidence),
        "provider": item.provider,
        "model": item.model,
        "generation_mode": item.decision.get("generation_mode", "POLICY_FALLBACK"),
        "created_at": item.created_at,
    }


async def send_suggestion(
    db,
    *,
    suggestion: AIDecision,
    user_id: int,
    correlation_id: str,
    confirm: bool,
):
    from app.services.clawhub import execute_action, preview_action

    decision = suggestion.decision or {}
    if decision.get("requires_human") or not decision.get("reply"):
        raise ValueError("该建议必须人工处理，不能发送")
    action = preview_action(
        db,
        action_type="SEND_REPLY",
        target_type="CONVERSATION",
        target_id=suggestion.conversation_id,
        payload={
            "recipient_id": decision.get("recipient_id"),
            "text": decision.get("reply"),
        },
        idempotency_key=f"reply:{suggestion.input_context_hash}",
        user_id=user_id,
        correlation_id=correlation_id,
    )
    if action.status == "PREVIEWED" and (confirm or not action.requires_confirmation):
        action = await execute_action(
            db,
            action_id=action.id,
            confirm=confirm,
            user_id=user_id,
            correlation_id=correlation_id,
        )
    return action
