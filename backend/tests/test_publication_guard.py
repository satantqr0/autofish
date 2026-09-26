from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.routes.products import queue_publication
from app.models import Base, PublicationTask, User
from app.schemas.catalog import ProductCreate, PublicationQueueRequest
from app.services.catalog import create_catalog_product
from app.services.publication_guard import (
    SELLER_SERVICE_COPY,
    publication_copy_blockers,
    sanitize_publication_source_text,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_demo_product_cannot_enter_publication_queue(db):
    user = User(username="operator", password_hash="not-used", role="operator", is_active=True)
    db.add(user)
    db.flush()
    product = create_catalog_product(
        db,
        ProductCreate(
            supplier_code="1688-DEMO",
            supplier_name="1688 演示供应商",
            external_product_id="DEMO-P-QUEUE",
            supplier_url="https://detail.1688.example/offer/2.html",
            external_sku_id="DEMO-SKU-QUEUE",
            internal_code="AF-DEMO-QUEUE",
            sku_code="AF-DEMO-SKU-QUEUE",
            title="演示待发布商品",
            category="电脑配件",
            description="演示数据，仅用于功能验证。",
            supplier_price="20",
            shipping_cost="5",
            minimum_profit="10",
            target_profit="20",
            stock=50,
        ),
        actor_user_id=user.id,
        correlation_id="create-demo-queue-product",
    )
    db.commit()
    request = SimpleNamespace(state=SimpleNamespace(correlation_id="queue-demo"))

    with pytest.raises(HTTPException) as exc_info:
        queue_publication(
            product_id=product.id,
            payload=PublicationQueueRequest(note="should be blocked"),
            request=request,
            user=user,
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert "商品发布来源校验失败" in exc_info.value.detail
    assert db.scalar(select(func.count(PublicationTask.id))) == 0


def test_publication_copy_removes_and_blocks_self_fulfilment_wording():
    source = "商品由本人负责销售、发货安排与售后处理；下单前可先确认库存和规格。"
    cleaned = sanitize_publication_source_text(source)

    assert "本人" not in cleaned
    assert "发货安排" not in cleaned
    assert publication_copy_blockers("收纳盒", f"商品规格真实。\n\n{SELLER_SERVICE_COPY}") == []
    blockers = publication_copy_blockers("收纳盒", source)
    assert any("本人发货说明" in value for value in blockers)
    assert any("中性的库存、规格与售后沟通提示" in value for value in blockers)
