from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AIProviderConfig,
    AIRuntimeSetting,
    AutomationControl,
    AutomationScope,
    BrowserBridgeAgent,
    SourcingCandidate,
)
from app.services.autonomous_launch import score_candidate
from app.services.xianyu_browser_bridge import configured_bridge_token

REQUIRED_AI_TASKS = (
    ("product_copy", "text", "文案"),
    ("customer_service", "text", "AI 客服"),
    ("vision_quality", "vision", "视觉质检"),
    ("image_generation", "image_generation", "精品图"),
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _version_at_least(value: str, minimum: tuple[int, ...]) -> bool:
    try:
        parts = tuple(int(part) for part in value.split(".")[: len(minimum)])
    except (TypeError, ValueError):
        return False
    return parts + (0,) * (len(minimum) - len(parts)) >= minimum


def _provider_issue(
    providers: dict[str, AIProviderConfig],
    runtime: AIRuntimeSetting | None,
    task: str,
    capability: str,
) -> tuple[str | None, str | None]:
    if runtime is None or not runtime.enabled:
        return None, "大模型总开关未启用"
    provider_name = (runtime.task_routing or {}).get(task) or runtime.primary_provider
    if provider_name == "human":
        return None, f"{task} 被设置为人工处理"
    provider = providers.get(provider_name)
    if provider is None or not provider.is_configured or not provider.api_key_encrypted:
        return provider_name, f"{provider_name} 尚未保存 API Key"
    if provider.last_test_status != "SUCCESS":
        return provider_name, f"{provider_name} 尚未通过连接测试"
    if not (provider.capabilities or {}).get(capability):
        return provider_name, f"{provider_name} 不支持 {capability}"
    model = {
        "text": provider.text_model,
        "vision": provider.vision_model,
        "image_generation": provider.image_model,
    }[capability]
    if not model:
        return provider_name, f"{provider_name} 未配置 {capability} 模型"
    if capability == "image_generation" and (
        provider.provider != "qwen"
        or not model.startswith(("qwen-image-2.0", "qwen-image-edit", "wan2.7-image"))
    ):
        return provider_name, "当前自主精品图链路要求 Qwen 参考图编辑模型"
    return provider_name, None


def build_runtime_readiness(db: Session, *, now: datetime | None = None) -> dict:
    reference = now or datetime.now(UTC)
    settings = get_settings()
    runtime = db.get(AIRuntimeSetting, 1)
    providers = {
        item.provider: item for item in db.scalars(select(AIProviderConfig)).all()
    }

    ai_routes: list[dict] = []
    ai_blockers: list[str] = []
    for task, capability, label in REQUIRED_AI_TASKS:
        provider_name, issue = _provider_issue(providers, runtime, task, capability)
        ai_routes.append(
            {
                "task": task,
                "label": label,
                "provider": provider_name,
                "ready": issue is None,
                "message": issue or "已就绪",
            }
        )
        if issue:
            ai_blockers.append(issue)

    controls = {
        item.scope: item for item in db.scalars(select(AutomationControl)).all()
    }
    automation_blockers: list[str] = []
    global_control = controls.get(AutomationScope.GLOBAL)
    publish_control = controls.get(AutomationScope.PUBLISH)
    if global_control is None or not global_control.enabled:
        automation_blockers.append("全局自动化未启用")
    if publish_control is None or not publish_control.enabled:
        automation_blockers.append("发布自动化未启用")
    elif publish_control.mode != "AUTOMATIC":
        automation_blockers.append("发布自动化不是 AUTOMATIC 模式")
    if not settings.automation_scheduler_enabled:
        automation_blockers.append("后台调度器未启用")
    if not settings.xianyu_browser_publish_enabled:
        automation_blockers.append("闲鱼浏览器发布未启用")
    if not configured_bridge_token():
        automation_blockers.append("浏览器桥接令牌未配置")

    agent = db.scalar(
        select(BrowserBridgeAgent).order_by(BrowserBridgeAgent.last_seen_at.desc())
    )
    agent_online = bool(
        agent and _utc(agent.last_seen_at) >= reference - timedelta(seconds=90)
    )
    browser_blockers: list[str] = []
    if not agent_online:
        browser_blockers.append("本机浏览器执行器离线")
    elif "PUBLISH_PRODUCT" not in (agent.capabilities or []):
        browser_blockers.append("浏览器扩展尚未加载自动发布能力")
    if agent and not _version_at_least(agent.version, (0, 5, 0)):
        browser_blockers.append("浏览器扩展需重新加载到 0.5.0 或更高版本")

    discovered = db.scalars(
        select(SourcingCandidate)
        .where(SourcingCandidate.status == "DISCOVERED")
        .order_by(SourcingCandidate.last_fetched_at.desc())
        .limit(100)
    ).all()
    eligible_count = sum(bool(score_candidate(item)["eligible"]) for item in discovered)
    selection_blockers = [] if eligible_count else ["没有通过门禁的待选货源"]

    stages = [
        {
            "key": "selection",
            "label": "选品",
            "ready": not selection_blockers,
            "detail": (
                f"{eligible_count} 个候选可自主选择"
                if eligible_count
                else selection_blockers[0]
            ),
            "route": "/sourcing",
        },
        {
            "key": "content",
            "label": "文案与图片",
            "ready": not ai_blockers,
            "detail": "模型 API 与结构化门禁已就绪" if not ai_blockers else ai_blockers[0],
            "route": "/model-settings",
        },
        {
            "key": "publish",
            "label": "闲鱼发布",
            "ready": not automation_blockers and not browser_blockers,
            "detail": (
                "本机浏览器执行器可自动发布"
                if not automation_blockers and not browser_blockers
                else (automation_blockers + browser_blockers)[0]
            ),
            "route": "/xianyu-workbench",
        },
        {
            "key": "fulfillment",
            "label": "订单与采购",
            "ready": bool(settings.adapters_enabled),
            "detail": (
                "订单同步已启用；采购仍受 Adapter 能力门禁"
                if settings.adapters_enabled
                else "平台 Adapter 未启用"
            ),
            "route": "/orders",
        },
    ]
    blockers = list(
        dict.fromkeys(selection_blockers + ai_blockers + automation_blockers + browser_blockers)
    )
    listing_ready = all(stage["ready"] for stage in stages[:3])
    return {
        "status": "READY" if listing_ready else "BLOCKED",
        "listing_ready": listing_ready,
        "summary": "自主上架可运行" if listing_ready else f"还有 {len(blockers)} 项需要处理",
        "blockers": blockers,
        "stages": stages,
        "ai": {
            "enabled": bool(runtime and runtime.enabled),
            "primary_provider": runtime.primary_provider if runtime else None,
            "routes": ai_routes,
        },
        "browser": {
            "online": agent_online,
            "bridge_id": agent.id if agent else None,
            "version": agent.version if agent else None,
            "capabilities": agent.capabilities if agent else [],
            "last_seen_at": agent.last_seen_at if agent else None,
        },
        "candidate_pool": {
            "discovered": len(discovered),
            "eligible": eligible_count,
        },
        "checked_at": reference,
    }
