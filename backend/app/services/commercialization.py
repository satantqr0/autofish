from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.models import CommercialDeployment, CommercialEvidencePeriod

TERMS_VERSION = "2026-08-19"
PRIVACY_VERSION = "2026-08-19"
COMPLIANCE_VERSION = "2026-08-19"

COMMERCIAL_OFFERS = [
    {
        "code": "PILOT",
        "name": "付费验证版",
        "price_cny": 1980,
        "billing": "90 天",
        "purpose": "限前 5 个真实客户，验证安装、留存和售后成本",
        "includes": ["单账号私有部署", "初始化与迁移", "90 天更新", "工作时段支持"],
        "not_included": ["代运营", "账号托管", "收益保证", "风控绕过"],
    },
    {
        "code": "STANDARD",
        "name": "私有部署标准版",
        "price_cny": 6980,
        "billing": "首年；续费 3980 元/年",
        "purpose": "产品完成付费验证后销售的主力套餐",
        "includes": ["单账号私有部署", "版本更新", "季度健康检查", "标准故障支持"],
        "not_included": ["硬件与模型费用", "代运营", "账号托管", "收益保证"],
    },
    {
        "code": "PRO",
        "name": "私有部署专业版",
        "price_cny": 12800,
        "billing": "首年",
        "purpose": "需要流程配置与经营复盘的小型经营者",
        "includes": ["标准版全部能力", "供应链模板配置", "月度经营复盘", "优先故障支持"],
        "not_included": ["多账号群控", "登录态托管", "代收款", "法律与财税代理"],
    },
]

PERMANENT_BOUNDARIES = [
    "仅支持客户自有设备、自有闲鱼账号和合法供应链",
    "不托管 Cookie、密码、验证码、人脸或支付凭证",
    "不提供多账号群控、刷量、虚假交易或风控绕过",
    "不承诺曝光、订单、收入或利润结果",
    "退款、投诉、支付、法律争议和平台介入必须人工处理",
]


def ensure_commercial_deployment(db):
    item = db.scalar(select(CommercialDeployment).order_by(CommercialDeployment.id))
    if item is None:
        item = CommercialDeployment()
        db.add(item)
        db.flush()
    return item


def serialize_deployment(item):
    return {
        "id": item.id,
        "edition": item.edition,
        "installation_name": item.installation_name,
        "customer_name": item.customer_name,
        "customer_type": item.customer_type,
        "plan": item.plan,
        "deployment_mode": item.deployment_mode,
        "acceptance": {
            "account_owned_by_customer": item.account_owned_by_customer,
            "data_stays_customer_controlled": item.data_stays_customer_controlled,
            "no_credential_custody": item.no_credential_custody,
            "no_revenue_guarantee_acknowledged": item.no_revenue_guarantee_acknowledged,
            "prohibited_automation_acknowledged": item.prohibited_automation_acknowledged,
            "regulatory_obligations_acknowledged": item.regulatory_obligations_acknowledged,
            "terms_version": item.terms_version,
            "privacy_version": item.privacy_version,
            "compliance_version": item.compliance_version,
            "accepted_at": item.accepted_at,
        },
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def update_commercial_profile(item, payload):
    before = serialize_deployment(item)
    item.installation_name = payload.installation_name
    item.customer_name = payload.customer_name
    item.customer_type = payload.customer_type
    item.plan = payload.plan
    return before


def accept_commercial_boundaries(item, *, user_id):
    before = serialize_deployment(item)
    item.account_owned_by_customer = True
    item.data_stays_customer_controlled = True
    item.no_credential_custody = True
    item.no_revenue_guarantee_acknowledged = True
    item.prohibited_automation_acknowledged = True
    item.regulatory_obligations_acknowledged = True
    item.terms_version = TERMS_VERSION
    item.privacy_version = PRIVACY_VERSION
    item.compliance_version = COMPLIANCE_VERSION
    item.accepted_by_user_id = user_id
    item.accepted_at = datetime.now(UTC)
    return before


def upsert_commercial_evidence(db, payload, *, user_id):
    current_month = datetime.now(ZoneInfo("Asia/Shanghai")).date().replace(day=1)
    if payload.period_start > current_month:
        raise ValueError("不能录入未来月份的商业证据")
    item = db.scalar(
        select(CommercialEvidencePeriod).where(
            CommercialEvidencePeriod.period_start == payload.period_start
        )
    )
    before = serialize_evidence(item) if item else {}
    created = item is None
    if item is None:
        item = CommercialEvidencePeriod(
            period_start=payload.period_start,
            recorded_by_user_id=user_id,
        )
        db.add(item)
    for field in (
        "qualified_leads",
        "product_demos",
        "paid_new_customers",
        "active_customers",
        "retained_30d_customers",
        "refunded_customers",
        "revenue_cny",
        "delivery_hours",
        "support_hours",
        "notes",
    ):
        setattr(item, field, getattr(payload, field))
    item.recorded_by_user_id = user_id
    db.flush()
    return item, before, created


def serialize_evidence(item):
    return {
        "id": item.id,
        "period_start": item.period_start,
        "qualified_leads": item.qualified_leads,
        "product_demos": item.product_demos,
        "paid_new_customers": item.paid_new_customers,
        "active_customers": item.active_customers,
        "retained_30d_customers": item.retained_30d_customers,
        "refunded_customers": item.refunded_customers,
        "revenue_cny": item.revenue_cny,
        "delivery_hours": item.delivery_hours,
        "support_hours": item.support_hours,
        "notes": item.notes,
        "updated_at": item.updated_at,
    }


def _acceptance_current(item):
    flags = (
        item.account_owned_by_customer,
        item.data_stays_customer_controlled,
        item.no_credential_custody,
        item.no_revenue_guarantee_acknowledged,
        item.prohibited_automation_acknowledged,
        item.regulatory_obligations_acknowledged,
    )
    versions = (
        item.terms_version == TERMS_VERSION,
        item.privacy_version == PRIVACY_VERSION,
        item.compliance_version == COMPLIANCE_VERSION,
    )
    return bool(item.accepted_at and all(flags) and all(versions))


def build_commercial_overview(db):
    deployment = ensure_commercial_deployment(db)
    periods = db.scalars(
        select(CommercialEvidencePeriod).order_by(CommercialEvidencePeriod.period_start.desc())
    ).all()

    total_leads = sum(item.qualified_leads for item in periods)
    total_demos = sum(item.product_demos for item in periods)
    total_paid = sum(item.paid_new_customers for item in periods)
    total_refunds = sum(item.refunded_customers for item in periods)
    total_revenue = sum((item.revenue_cny for item in periods), Decimal("0"))
    total_delivery = sum((item.delivery_hours for item in periods), Decimal("0"))
    total_support = sum((item.support_hours for item in periods), Decimal("0"))
    total_active_months = sum(item.active_customers for item in periods)
    latest = periods[0] if periods else None

    delivery_per_customer = total_delivery / total_paid if total_paid else None
    support_per_active = total_support / total_active_months if total_active_months else None
    retention_rate = (
        Decimal(latest.retained_30d_customers) / Decimal(latest.active_customers)
        if latest and latest.active_customers
        else None
    )
    acceptance_ready = _acceptance_current(deployment)
    first_sale = total_paid >= 1 and total_revenue > 0
    repeat_demand = total_paid >= 3
    retention_ready = bool(
        latest
        and latest.active_customers >= 3
        and retention_rate is not None
        and retention_rate >= Decimal("0.67")
    )
    delivery_ready = bool(
        total_paid >= 3
        and delivery_per_customer is not None
        and delivery_per_customer <= Decimal("4")
        and support_per_active is not None
        and support_per_active <= Decimal("0.5")
    )

    stages = [
        {
            "key": "boundary",
            "label": "产品边界",
            "ready": True,
            "detail": "客户自有单租户，不托管账号、不承诺收益",
        },
        {
            "key": "acceptance",
            "label": "交付验收",
            "ready": acceptance_ready,
            "detail": "当前版本商业条款、隐私和合规责任已确认"
            if acceptance_ready
            else "待确认客户自有化与合规边界",
        },
        {
            "key": "first_sale",
            "label": "首次付费",
            "ready": first_sale,
            "detail": f"累计付费客户 {total_paid}，收入 ¥{total_revenue:.2f}",
        },
        {
            "key": "repeat_demand",
            "label": "重复需求",
            "ready": repeat_demand,
            "detail": "至少 3 个独立付费客户"
            if repeat_demand
            else "正式销售前至少完成 3 个独立付费客户",
        },
        {
            "key": "retention",
            "label": "30 天留存",
            "ready": retention_ready,
            "detail": f"最近一期留存 {retention_rate:.0%}"
            if retention_rate is not None
            else "尚无可用留存数据",
        },
        {
            "key": "delivery",
            "label": "标准化交付",
            "ready": delivery_ready,
            "detail": "单客交付不超过 4 小时、每活跃客户月支持不超过 0.5 小时",
        },
    ]
    score_weights = {
        "boundary": 20,
        "acceptance": 25,
        "first_sale": 15,
        "repeat_demand": 15,
        "retention": 10,
        "delivery": 15,
    }
    score = sum(score_weights[stage["key"]] for stage in stages if stage["ready"])
    blockers = [stage["detail"] for stage in stages if not stage["ready"]]
    release_ready = all(stage["ready"] for stage in stages)
    if release_ready:
        status = "PRIVATE_RELEASE_READY"
        status_label = "可小规模正式销售"
    elif acceptance_ready:
        status = "PAID_PILOT"
        status_label = "仅限付费试点"
    else:
        status = "INTERNAL"
        status_label = "内部验证"

    if total_paid == 0:
        growth_level = 1
        growth_task = "验证至少一个外部客户愿意付钱"
    elif total_paid < 3:
        growth_level = 2
        growth_task = "把首次付费复制到 3 个独立客户"
    else:
        growth_level = 3
        growth_task = "建立可重复获客与标准化交付"

    result = {
        "version": "1.0",
        "status": status,
        "status_label": status_label,
        "release_ready": release_ready,
        "readiness_score": score,
        "growth_level": growth_level,
        "growth_task": growth_task,
        "deployment": serialize_deployment(deployment),
        "stages": stages,
        "blockers": blockers,
        "boundaries": PERMANENT_BOUNDARIES,
        "offers": COMMERCIAL_OFFERS,
        "evidence_summary": {
            "qualified_leads": total_leads,
            "product_demos": total_demos,
            "paid_customers": total_paid,
            "refunded_customers": total_refunds,
            "revenue_cny": total_revenue,
            "delivery_hours_per_paid_customer": delivery_per_customer,
            "support_hours_per_active_customer": support_per_active,
            "latest_retention_rate": retention_rate,
        },
        "periods": [serialize_evidence(item) for item in periods[:12]],
        "versions": {
            "terms": TERMS_VERSION,
            "privacy": PRIVACY_VERSION,
            "compliance": COMPLIANCE_VERSION,
        },
    }
    return jsonable_encoder(result)
