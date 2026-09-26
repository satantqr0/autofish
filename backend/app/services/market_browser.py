"""Read-only browser queue. Leases cannot publish, chat, or access credentials."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from sqlalchemy import select

from app.models.market import MarketPriceRun
from app.services.audit import write_audit
from app.services.market_prices import CATEGORIES, clean_samples, slot_at


def claim(db, bridge_id, now=None):
    now = now or datetime.now(UTC)
    run = db.scalar(
        select(MarketPriceRun)
        .where(
            MarketPriceRun.slot == slot_at(now),
            MarketPriceRun.status.in_(["WAITING_BROWSER", "BROWSER_RUNNING"]),
        )
        .with_for_update()
    )
    if not run:
        return None
    state = dict(run.bridge_state or {})
    if state.get("expires_at") and datetime.fromisoformat(state["expires_at"]) > now:
        return None
    completed = {r["category"] for r in run.results}
    category = next((c for c in CATEGORIES if c not in completed), None)
    if not category:
        return None
    attempts = dict(state.get("attempts", {}))
    attempts[category] = attempts.get(category, 0) + 1
    if attempts[category] > 3:
        run.status = "PARTIAL" if run.results else "FAILED"
        run.message = f"{category} 浏览器任务连续三次超时；停止本轮。"
        run.finished_at = now
        db.commit()
        return None
    token = secrets.token_urlsafe(32)
    expiry = now + timedelta(seconds=90)
    run.bridge_state = {
        "owner": bridge_id,
        "category": category,
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": expiry.isoformat(),
        "attempts": attempts,
    }
    run.status = "BROWSER_RUNNING"
    run.message = f"普通浏览器正在采集 {category}（{len(completed)}/{len(CATEGORIES)} 类已回传）。"
    write_audit(
        db,
        action="MARKET_BROWSER_CLAIM",
        entity_type="MARKET_RUN",
        entity_id=run.id,
        after_data={"bridge_id": bridge_id, "category": category, "attempt": attempts[category]},
    )
    db.commit()
    return {
        "run_id": run.id,
        "query": category,
        "lease_token": token,
        "expires_at": expiry.isoformat(),
    }


def complete(db, payload, *, supervised=False, now=None):
    now = now or datetime.now(UTC)
    run = db.scalar(
        select(MarketPriceRun).where(MarketPriceRun.id == payload.run_id).with_for_update()
    )
    if not run:
        raise HTTPException(404, "采集任务不存在")
    state = dict(run.bridge_state or {})
    digest = hashlib.sha256(payload.lease_token.encode()).hexdigest()
    # Exact replay is harmless; never accept a changed report under the same token.
    report_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    if state.get("last_token_hash") == digest and state.get("last_report_hash") == report_hash:
        return {"status": run.status, "duplicate": True}
    if (
        run.status != "BROWSER_RUNNING"
        or state.get("owner") != payload.bridge_id
        or not secrets.compare_digest(state.get("token_hash", ""), digest)
        or state.get("category") != payload.query
        or datetime.fromisoformat(state["expires_at"]) < now
    ):
        raise HTTPException(409, "采集租约已过期或结果不匹配")
    captured = payload.captured_at
    if captured.tzinfo is None or abs((now - captured).total_seconds()) > 120:
        raise HTTPException(422, "只接受两分钟内的带时区实采结果")
    url = urlparse(payload.page_url)
    if (
        url.scheme != "https"
        or url.netloc != "www.goofish.com"
        or url.path != "/search"
        or parse_qs(url.query).get("q") != [payload.query]
    ):
        raise HTTPException(422, "搜索页地址与任务不符")
    if payload.status == "OK":
        for item in payload.items:
            item_url = urlparse(item.url)
            if (
                item_url.scheme != "https"
                or item_url.netloc != "www.goofish.com"
                or item_url.path != "/item"
                or parse_qs(item_url.query).get("id") != [item.item_id]
            ):
                raise HTTPException(422, "商品链接与编号不符")
        samples = clean_samples([x.model_dump() for x in payload.items])
        run.results = [
            *run.results,
            {
                "category": payload.query,
                "status": "OK",
                "source": "codex-supervised-browser" if supervised else "goofish-chrome-bridge",
                "sampled_at": captured.isoformat(),
                "raw_count": len(payload.items),
                "evidence_sha256": report_hash,
                "samples": samples,
            },
        ]
        run.status = "COMPLETED" if len(run.results) == len(CATEGORIES) else "WAITING_BROWSER"
        run.message = f"已回传 {len(run.results)}/{len(CATEGORIES)} 类真实挂牌样本。"
        if run.status == "COMPLETED":
            run.finished_at = now
    else:
        run.status = "PARTIAL" if run.results else "BLOCKED"
        run.finished_at = now
        run.message = (
            f"{payload.query}：{payload.status}，本轮已停止；请在普通浏览器检查登录或验证。"
        )
    run.bridge_state = {
        "attempts": state.get("attempts", {}),
        "last_token_hash": digest,
        "last_report_hash": report_hash,
        "last_seen_at": now.isoformat(),
        "owner": payload.bridge_id,
        "last_error": None if payload.status == "OK" else payload.status,
    }
    write_audit(
        db,
        action="MARKET_BROWSER_RESULT",
        entity_type="MARKET_RUN",
        entity_id=run.id,
        after_data={
            "category": payload.query,
            "status": payload.status,
            "raw_count": len(payload.items),
            "source": "supervised" if supervised else "bridge",
            "evidence_sha256": report_hash,
        },
    )
    db.commit()
    return {"status": run.status, "categories_received": len(run.results)}
