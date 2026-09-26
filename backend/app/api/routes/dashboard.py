from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models import (
    AuditLog,
    AutomationControl,
    AutomationJob,
    AutomationScope,
    Conversation,
    Lifecycle,
    ManualTask,
    Message,
    Order,
    OrderStatus,
    Product,
    ProductSKU,
    User,
    XianyuDraft,
    XianyuProductDailyMetric,
)
from app.services.runtime_readiness import build_runtime_readiness

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/readiness")
def runtime_readiness(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return build_runtime_readiness(db)


@router.get("")
def dashboard(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    lifecycle_rows = db.execute(
        select(Product.lifecycle, func.count(Product.id)).group_by(Product.lifecycle)
    ).all()
    lifecycle = {state.value: count for state, count in lifecycle_rows}
    total_products = db.scalar(select(func.count(Product.id))) or 0
    low_stock = db.scalar(select(func.count(ProductSKU.id)).where(ProductSKU.stock < 50)) or 0
    controls = db.scalars(select(AutomationControl)).all()
    control_map = {item.scope.value: item.enabled for item in controls}
    recent = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(6)).all()
    recent_jobs = db.scalars(
        select(AutomationJob).order_by(AutomationJob.created_at.desc()).limit(6)
    ).all()
    open_tasks = db.scalars(
        select(ManualTask).where(ManualTask.status == "OPEN").order_by(ManualTask.priority.desc())
    ).all()
    risk_counts = {"PRICE_REVIEW": 0, "STOCK_REVIEW": 0, "AFTERSALES": 0, "OTHER": 0}
    for item in open_tasks:
        key = item.type if item.type in risk_counts else "OTHER"
        risk_counts[key] += 1

    demo_business = {
        "gmv": "1389.00",
        "orders": 8,
        "gross_profit": "426.00",
        "expected_net_profit": "351.00",
        "actual_net_profit": "0.00",
        "refunds": 0,
    }
    live_orders = db.scalars(select(Order)).all()
    today_orders = [
        item
        for item in live_orders
        if item.created_at
        and item.created_at.replace(tzinfo=item.created_at.tzinfo or UTC)
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .date()
        == today
    ]
    pending_drafts = int(
        db.scalar(
            select(func.count(XianyuDraft.id)).where(XianyuDraft.status == "REVIEW_READY")
        )
        or 0
    )
    gmv = sum((item.revenue for item in live_orders), Decimal("0"))
    expected_profit = sum((item.expected_profit for item in live_orders), Decimal("0"))
    actual_profit = sum(
        (item.actual_profit for item in live_orders if item.actual_profit is not None),
        Decimal("0"),
    )
    live_business = {
        "gmv": str(gmv),
        "orders": len(live_orders),
        "gross_profit": str(actual_profit if actual_profit else expected_profit),
        "expected_net_profit": str(expected_profit),
        "actual_net_profit": str(actual_profit),
        "refunds": sum(
            item.status in {OrderStatus.REFUND_PENDING, OrderStatus.AFTERSALES}
            for item in live_orders
        ),
    }
    demo_chart = [
        {"date": "8/9", "revenue": 1128, "profit": 278},
        {"date": "8/10", "revenue": 1345, "profit": 312},
        {"date": "8/11", "revenue": 1602, "profit": 389},
        {"date": "8/12", "revenue": 1210, "profit": 265},
        {"date": "8/13", "revenue": 1789, "profit": 421},
        {"date": "8/14", "revenue": 1256, "profit": 308},
        {"date": "8/15", "revenue": 1389, "profit": 351},
    ]
    live_chart = []
    for offset in range(6, -1, -1):
        date = today - timedelta(days=offset)
        rows = [
            item
            for item in live_orders
            if item.paid_at
            and item.paid_at.replace(tzinfo=item.paid_at.tzinfo or UTC)
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .date()
            == date
        ]
        live_chart.append(
            {
                "date": date.strftime("%-m/%-d"),
                "revenue": float(sum((item.revenue for item in rows), Decimal("0"))),
                "profit": float(
                    sum(
                        (
                            item.actual_profit
                            if item.actual_profit is not None
                            else item.expected_profit
                            for item in rows
                        ),
                        Decimal("0"),
                    )
                ),
            }
        )
    message_count = int(db.scalar(select(func.count(Message.id))) or 0)
    auto_reply_count = int(
        db.scalar(select(func.count(Message.id)).where(Message.sent_by_ai.is_(True))) or 0
    )
    manual_takeovers = int(
        db.scalar(select(func.count(Conversation.id)).where(Conversation.manual_mode.is_(True)))
        or 0
    )
    traffic_start = today - timedelta(days=6)
    traffic_rows = db.scalars(
        select(XianyuProductDailyMetric).where(
            XianyuProductDailyMetric.metric_date >= traffic_start,
            XianyuProductDailyMetric.metric_date <= today,
        )
    ).all()
    live_traffic = {
        "exposures": sum(item.exposures for item in traffic_rows),
        "views": sum(item.views for item in traffic_rows),
        "consultations": sum(item.inquiries for item in traffic_rows),
        "favorites": 0,
        "wants": 0,
    }

    return {
        "data_mode": settings.data_mode,
        "metrics_mode": "DEMO_FIXTURE" if settings.seed_demo else "LIVE_FACTS",
        "date": today.isoformat(),
        "automation": {
            "running": bool(control_map.get(AutomationScope.GLOBAL.value, False)),
            "controls": control_map,
        },
        "business": demo_business if settings.seed_demo else live_business,
        "traffic": {
            "exposures": 3250,
            "views": 612,
            "consultations": 48,
            "favorites": 86,
            "wants": 129,
        }
        if settings.seed_demo
        else live_traffic,
        "ai_service": {
            "messages": 48,
            "auto_replies": 45,
            "auto_rate": "93.75",
            "manual_takeovers": 3,
            "average_response_seconds": "4.2",
        }
        if settings.seed_demo
        else {
            "messages": message_count,
            "auto_replies": auto_reply_count,
            "auto_rate": (
                f"{auto_reply_count * 100 / message_count:.2f}" if message_count else "0.00"
            ),
            "manual_takeovers": manual_takeovers,
            "average_response_seconds": "0.0",
        },
        "products": {
            "total": total_products,
            "active": lifecycle.get(Lifecycle.ACTIVE.value, 0),
            "testing": lifecycle.get(Lifecycle.TESTING.value, 0),
            "winner": lifecycle.get(Lifecycle.WINNER.value, 0),
            "low_stock": low_stock,
            "pending_removal": lifecycle.get(Lifecycle.DECLINING.value, 0),
        },
        "operation_summary": {
            "products": total_products,
            "pending_drafts": pending_drafts,
            "today_orders": len(today_orders),
            "manual_tasks": len(open_tasks),
        },
        "risks": {
            "price": risk_counts["PRICE_REVIEW"],
            "stock": risk_counts["STOCK_REVIEW"],
            "aftersales": risk_counts["AFTERSALES"],
            "other": risk_counts["OTHER"],
        },
        "chart": demo_chart if settings.seed_demo else live_chart,
        "recent_activity": [
            {
                "id": item.id,
                "time": item.created_at,
                "type": item.entity_type,
                "action": item.action,
                "entity_id": item.entity_id,
                "actor": item.actor_type,
            }
            for item in recent
        ],
        "recent_tasks": [
            {
                "id": item.id,
                "type": item.type.value,
                "status": item.status.value,
                "updated_at": item.updated_at,
            }
            for item in recent_jobs
        ],
        "manual_items": [
            {
                "id": item.id,
                "title": item.title,
                "reason": item.reason,
                "priority": item.priority,
                "created_at": item.created_at,
            }
            for item in open_tasks[:5]
        ],
    }
