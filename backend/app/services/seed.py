from decimal import Decimal

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import AutomationControl, AutomationScope, ManualTask, Product, User
from app.schemas.catalog import ProductCreate
from app.services.catalog import create_catalog_product


def seed_database(db):
    settings = get_settings()
    admin = db.scalar(select(User).where(User.username == settings.admin_username))
    if admin is None:
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.flush()

    for scope in AutomationScope:
        existing = db.scalar(select(AutomationControl).where(AutomationControl.scope == scope))
        if existing is None:
            defaults = {
                AutomationScope.GLOBAL: (20, 1),
                AutomationScope.PUBLISH: (10, 300),
                AutomationScope.CUSTOMER_SERVICE: (200, 5),
                AutomationScope.PURCHASE: (20, 60),
                AutomationScope.REPRICING: (20, 300),
                AutomationScope.LOGISTICS: (50, 30),
            }
            daily_limit, min_interval = defaults[scope]
            db.add(
                AutomationControl(
                    scope=scope,
                    enabled=False,
                    mode="REVIEW",
                    daily_limit=daily_limit,
                    min_interval_seconds=min_interval,
                    failure_threshold=3,
                    stopped_reason="真实平台 Adapter 尚未启用",
                    updated_by_user_id=admin.id,
                )
            )
    db.flush()

    if not settings.seed_demo or db.scalar(select(Product.id).limit(1)) is not None:
        db.commit()
        return

    rows = [
        (
            "Mini PC VESA支架",
            "AF-MPC-VESA01",
            "SP-100231",
            "SS-100231-1",
            "42",
            "6",
            "2",
            "4",
            "30",
            "30",
            "25",
            128,
            "WINNER",
            [92, 85, 90, 88, 90, 90, 86],
        ),
        (
            "NAS硬盘减震架",
            "AF-NAS-SSD01",
            "SP-100876",
            "SS-100876-1",
            "28",
            "5",
            "2",
            "3",
            "20",
            "22",
            "14",
            42,
            "ACTIVE",
            [88, 88, 90, 92, 84, 82, 82],
        ),
        (
            "2.5转3.5硬盘架",
            "AF-2535-01",
            "SP-100543",
            "SS-100543-1",
            "18",
            "5",
            "1",
            "2",
            "13",
            "15",
            "10",
            316,
            "WINNER",
            [86, 90, 94, 85, 88, 92, 88],
        ),
        (
            "NAS风扇支架",
            "AF-NAS-FAN01",
            "SP-100112",
            "SS-100112-1",
            "23",
            "5",
            "2",
            "3",
            "15",
            "17",
            "11",
            198,
            "ACTIVE",
            [84, 86, 88, 80, 86, 88, 84],
        ),
        (
            "GPIO铜柱套装",
            "AF-GPIO-STD01",
            "SP-100334",
            "SS-100334-1",
            "15",
            "4",
            "1",
            "2",
            "10",
            "12",
            "7",
            254,
            "TESTING",
            [82, 92, 96, 82, 92, 90, 86],
        ),
        (
            "DIN导轨支架",
            "AF-DIN-RAIL01",
            "SP-100665",
            "SS-100665-1",
            "26",
            "5",
            "2",
            "3",
            "15",
            "18",
            "8",
            96,
            "ACTIVE",
            [78, 84, 90, 76, 88, 78, 84],
        ),
        (
            "阿卡快装板",
            "AF-ARCA-01",
            "SP-100778",
            "SS-100778-1",
            "34",
            "6",
            "2",
            "3",
            "18",
            "22",
            "10",
            67,
            "ACTIVE",
            [76, 82, 90, 74, 88, 72, 78],
        ),
        (
            "机柜理线架",
            "AF-CM-01",
            "SP-100990",
            "SS-100990-1",
            "20",
            "6",
            "2",
            "3",
            "12",
            "15",
            "6",
            38,
            "TESTING",
            [74, 86, 94, 90, 82, 66, 86],
        ),
        (
            "摄影冷靴转接座",
            "AF-COLD-01",
            "SP-101008",
            "SS-101008-1",
            "19",
            "4",
            "2",
            "2",
            "12",
            "15",
            "8",
            156,
            "CANDIDATE",
            [80, 80, 88, 78, 90, 86, 75],
        ),
        (
            "RJ45模块防尘塞",
            "AF-RJ45-01",
            "SP-101125",
            "SS-101125-1",
            "8",
            "4",
            "1",
            "1",
            "8",
            "10",
            "6",
            520,
            "CANDIDATE",
            [88, 94, 98, 96, 94, 96, 90],
        ),
    ]

    for index, row in enumerate(rows, start=1):
        (
            title,
            sku_code,
            product_id,
            supplier_sku_id,
            price,
            shipping,
            fee,
            reserve,
            min_profit,
            target,
            margin,
            stock,
            lifecycle,
            safety,
        ) = row
        create_catalog_product(
            db,
            ProductCreate(
                supplier_code="1688-DEMO",
                supplier_name="1688 演示供应商",
                external_product_id=product_id,
                supplier_url=f"https://detail.1688.example/offer/{product_id}.html",
                external_sku_id=supplier_sku_id,
                internal_code=f"AF-P{index:03d}",
                sku_code=sku_code,
                title=title,
                category="NAS / Mini PC 被动配件" if index <= 4 else "数码与机柜被动配件",
                description=f"{title}，演示数据，仅用于 Phase 0–2 功能验证。",
                supplier_price=Decimal(price),
                shipping_cost=Decimal(shipping),
                platform_fee=Decimal(fee),
                after_sales_reserve=Decimal(reserve),
                minimum_profit=Decimal(min_profit),
                target_profit=Decimal(target),
                negotiation_margin=Decimal(margin),
                stock=stock,
                lifecycle=lifecycle,
                after_sales_safety=Decimal(safety[0]),
                fault_safety=Decimal(safety[1]),
                compatibility_safety=Decimal(safety[2]),
                transport_safety=Decimal(safety[3]),
                stock_stability=Decimal(safety[4]),
                price_stability=Decimal(safety[5]),
                verticality=Decimal(safety[6]),
            ),
        )

    db.add_all(
        [
            ManualTask(
                type="PRICE_REVIEW",
                title="价格异常待确认",
                reason="供应商价格变化超过阈值",
                priority=80,
                entity_type="PRODUCT",
                entity_id="7",
            ),
            ManualTask(
                type="STOCK_REVIEW",
                title="库存异常待确认",
                reason="两个 SKU 库存低于安全阈值",
                priority=70,
                entity_type="PRODUCT",
                entity_id="8",
            ),
            ManualTask(
                type="AFTERSALES",
                title="售后待人工",
                reason="售后事项禁止 AI 自动处理",
                priority=90,
                entity_type="ORDER",
                entity_id="DEMO",
            ),
        ]
    )
    db.commit()
