"""Audited search samples; never substitute store prices or model estimates."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from zoneinfo import ZoneInfo

from adapters.base import AdapterStatus
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.core.config import get_settings
from app.models import AuditLog, BrowserBridgeAgent
from app.models.market import MarketPriceRun
from app.services.adapter_factory import get_market_adapter
from app.services.audit import write_audit

CATEGORIES = (
    "手机",
    "平板电脑",
    "笔记本电脑",
    "台式电脑",
    "显卡",
    "处理器",
    "内存条",
    "固态硬盘",
    "显示器",
    "机械键盘",
    "鼠标",
    "路由器",
    "相机",
    "镜头",
    "无人机",
    "游戏机",
    "耳机",
    "音箱",
    "智能手表",
    "投影仪",
    "吸尘器",
    "扫地机器人",
    "咖啡机",
    "空气净化器",
    "电饭煲",
    "微波炉",
    "自行车",
    "帐篷",
    "鱼竿",
    "吉他",
)
MIN_MATCHED = 10
SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCLUDED = re.compile(
    r"求购|回收|租赁|出租|定金|订金|维修|故障|仅包装|空盒|配件机|仅售配件"
    r"|电池(?:坏了|不行|失效)|屏幕(?:碎|破裂)|触屏(?:功能)?失效|报废"
)


def slot_at(now):
    local = now.astimezone(SHANGHAI)
    if local.hour < 9:
        return f"{(local - timedelta(days=1)):%Y-%m-%d}T21"
    return f"{local:%Y-%m-%d}T{'09' if local.hour < 21 else '21'}"


def clean_samples(items):
    samples = {}
    for item in items[:50]:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        title = str(item.get("title") or "").strip()[:500]
        value = str(item.get("price") or "").strip().replace("¥", "").replace("￥", "")
        screening_title = re.sub(r"(?:无(?:任何)?|没有(?:任何)?|从未)(?:维修|故障)", "", title)
        if not item_id.isdigit() or not title or EXCLUDED.search(screening_title):
            continue
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
            continue
        try:
            price = Decimal(value)
        except InvalidOperation:
            continue
        if not Decimal("1") < price <= Decimal("1000000"):
            continue
        samples[item_id] = {
            "id": item_id,
            "title": title,
            "price": str(price),
            "url": f"https://www.goofish.com/item?id={item_id}",
            "platform_category_id": str(item.get("category_id") or ""),
        }
    return list(samples.values())


def compare_categories(current, baseline):
    previous = {row["category"]: row for row in baseline if row.get("status") == "OK"}
    rankings = []
    for row in current:
        old = previous.get(row["category"])
        if row.get("status") != "OK" or not old:
            continue
        if row.get("sampled_at") and old.get("sampled_at"):
            elapsed = (
                datetime.fromisoformat(row["sampled_at"])
                - datetime.fromisoformat(old["sampled_at"])
            ).total_seconds()
            if not 23 * 3600 <= elapsed <= 25 * 3600:
                continue  # A queued browser is not necessarily collecting on time.
        before = {x["id"]: x for x in old["samples"]}
        # Same ID AND title: reject listings whose product was replaced between samples.
        matched = [
            (x, before[x["id"]])
            for x in row["samples"]
            if x["id"] in before and x["title"] == before[x["id"]]["title"]
        ]
        if len(matched) < MIN_MATCHED:
            continue
        changes = [(Decimal(a["price"]) / Decimal(b["price"]) - 1) * 100 for a, b in matched]
        rankings.append(
            {
                "category": row["category"],
                "matched": len(matched),
                "sample_count": len(row["samples"]),
                "change_pct": str(median(changes).quantize(Decimal("0.01"))),
                "previous_price": str(median([Decimal(b["price"]) for _, b in matched])),
                "current_price": str(median([Decimal(a["price"]) for a, _ in matched])),
                "examples": [a for a, _ in matched[:3]],
            }
        )
    rising = sorted(
        (x for x in rankings if Decimal(x["change_pct"]) > 0),
        key=lambda x: (-Decimal(x["change_pct"]), x["category"]),
    )[:10]
    falling = sorted(
        (x for x in rankings if Decimal(x["change_pct"]) < 0),
        key=lambda x: (Decimal(x["change_pct"]), x["category"]),
    )[:10]
    return rising, falling, len(rankings)


async def collect(db, adapter=None, now=None, retry=False):
    now = now or datetime.now(UTC)
    slot = slot_at(now)
    for missed in db.scalars(
        select(MarketPriceRun)
        .where(
            MarketPriceRun.slot < slot,
            MarketPriceRun.status.in_(["WAITING_BROWSER", "BROWSER_RUNNING"]),
        )
        .with_for_update()
    ).all():
        missed.status = "PARTIAL" if missed.results else "MISSED"
        missed.finished_at = now
        missed.message = "浏览器未在本时段完成采集；保留已收到的真实样本。"
        write_audit(
            db,
            action="MARKET_BROWSER_SLOT_EXPIRED",
            entity_type="MARKET_RUN",
            entity_id=missed.id,
            result=missed.status,
        )
    db.commit()
    run = MarketPriceRun(slot=slot, started_at=now, status="RUNNING", results=[])
    db.add(run)
    try:
        db.commit()  # Unique slot prevents duplicate collection across workers/restarts.
    except IntegrityError:
        db.rollback()
        if not retry:
            return {"status": "ALREADY_SCHEDULED", "slot": slot}
        run = db.scalar(select(MarketPriceRun).where(MarketPriceRun.slot == slot).with_for_update())
        retries = db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "MARKET_COLLECTION_RETRY",
                AuditLog.entity_type == "MARKET_RUN",
                AuditLog.entity_id == str(run.id),
            )
        )
        if run.status not in {"BLOCKED", "FAILED"} or run.results or retries >= 2:
            db.rollback()
            return {"status": "RETRY_NOT_ALLOWED", "slot": slot}
        write_audit(
            db,
            action="MARKET_COLLECTION_RETRY",
            entity_type="MARKET_RUN",
            entity_id=run.id,
            before_data={
                "status": run.status,
                "message": run.message,
                "started_at": run.started_at.isoformat(),
            },
            after_data={"attempt": retries + 2},
        )
        run.status = "RUNNING"
        run.started_at = now
        run.finished_at = None
        run.message = None
        db.commit()
    if adapter is None and not get_settings().market_collector_url:
        run.status = "WAITING_BROWSER"
        run.message = "等待 AutoFish 桥接 0.6.0 以上版本；需保持普通 Chrome 打开并登录闲鱼。"
        run.bridge_state = {}
        db.commit()
        return {"status": run.status, "slot": slot}
    adapter = adapter or get_market_adapter()
    results = []
    try:
        search = getattr(adapter, "search_market", None)
        if search is None:
            run.status = "BLOCKED"
            run.message = "当前闲鱼 Adapter 未配置市场搜索能力，需接通已登录的只读搜索通道。"
        else:
            for category in CATEGORIES:
                response = await asyncio.wait_for(search(category), timeout=25)
                if response.status != AdapterStatus.OK:
                    run.status = "PARTIAL" if results else "BLOCKED"
                    run.message = (
                        "市场搜索受阻（"
                        + response.status.value
                        + "）；本轮停止采集，等待通道恢复。"
                    )
                    break
                results.append(
                    {
                        "category": category,
                        "status": "OK",
                        "source": response.source,
                        "sampled_at": datetime.now(UTC).isoformat(),
                        "samples": clean_samples(response.data or []),
                    }
                )
                run.results = list(results)
                db.commit()
                await asyncio.sleep(3)
            else:
                run.status = "COMPLETED"
                run.message = "采样完成；样本不足或无符合条件的涨跌时不补造榜单。"
    except Exception:
        db.rollback()
        run.status = "PARTIAL" if results else "FAILED"
        run.message = "采集超时或通道异常；本轮已终止，下个采集时段重新尝试。"
    run.results = results
    run.finished_at = datetime.now(UTC)
    db.commit()
    return {"status": run.status, "slot": slot}


def overview(db, now=None):
    now = now or datetime.now(UTC)
    bridges = db.scalars(
        select(BrowserBridgeAgent).order_by(BrowserBridgeAgent.last_seen_at.desc()).limit(10)
    ).all()
    latest = db.scalar(select(MarketPriceRun).order_by(MarketPriceRun.slot.desc()).limit(1))
    runs = db.scalars(
        select(MarketPriceRun)
        .options(defer(MarketPriceRun.results))
        .order_by(MarketPriceRun.slot.desc())
        .limit(14)
    ).all()
    baseline = None
    if latest:
        target = (datetime.strptime(latest.slot, "%Y-%m-%dT%H") - timedelta(days=1)).strftime(
            "%Y-%m-%dT%H"
        )
        baseline = db.scalar(select(MarketPriceRun).where(MarketPriceRun.slot == target))
        if (
            baseline
            and abs((latest.started_at - baseline.started_at).total_seconds() - 86400) > 3600
        ):
            baseline = None  # Late manual/bootstrap samples must not masquerade as 24h data.
    rising, falling, eligible = (
        compare_categories(latest.results, baseline.results)
        if latest
        and baseline
        and latest.status in {"PARTIAL", "COMPLETED"}
        and baseline.status in {"PARTIAL", "COMPLETED"}
        else ([], [], 0)
    )
    status = latest.status if latest else "WAITING"
    if (
        latest
        and status == "RUNNING"
        and now - latest.started_at.replace(tzinfo=UTC) > timedelta(minutes=16)
    ):
        status = "INTERRUPTED"
    local = now.astimezone(SHANGHAI)
    next_run = local.replace(hour=9, minute=0, second=0, microsecond=0)
    if local >= next_run:
        next_run = next_run.replace(hour=21)
    if local >= next_run:
        next_run = (next_run + timedelta(days=1)).replace(hour=9)
    return {
        "status": status,
        "schedule": "每天 09:00、21:00（北京时间）",
        "next_collection_at": next_run.isoformat(),
        "latest_slot": latest.slot if latest else None,
        "collected_at": (
            max((r.get("sampled_at", "") for r in latest.results), default="")
            or latest.started_at.isoformat()
        )
        if latest
        else None,
        "message": latest.message if latest else "等待首轮真实采集。",
        "baseline_slot": baseline.slot if baseline else None,
        "stale": bool(latest and now - latest.started_at.replace(tzinfo=UTC) > timedelta(hours=13)),
        "scope": "30 个监测品类的关键词搜索样本，非闲鱼全平台统计；仅挂牌价，非成交价。",
        "method": "同商品、同标题匹配，24 小时挂牌价涨跌幅中位数；每品类至少 10 个匹配样本。",
        "eligible_categories": eligible,
        "categories": list(CATEGORIES),
        "rising": rising,
        "falling": falling,
        "latest_samples": [
            {
                "category": r["category"],
                "count": len(r["samples"]),
                "source": r.get("source"),
                "sampled_at": r.get("sampled_at"),
                "raw_count": r.get("raw_count", len(r["samples"])),
                "examples": r["samples"][:3],
            }
            for r in latest.results
        ]
        if latest
        else [],
        "collection_mode": "gateway" if get_settings().market_collector_url else "chrome_bridge",
        "browser_required": not bool(get_settings().market_collector_url),
        "market_bridge_online": any(
            "CAPTURE_MARKET_PRICES" in (b.capabilities or [])
            and now - b.last_seen_at.replace(tzinfo=UTC) < timedelta(minutes=2)
            for b in bridges
        ),
        "bridge_last_seen_at": (latest.bridge_state or {}).get("last_seen_at") if latest else None,
        "history": [
            {
                "slot": r.slot,
                "status": r.status,
                "message": r.message,
                "started_at": r.started_at.isoformat(),
            }
            for r in runs[:14]
        ],
    }
