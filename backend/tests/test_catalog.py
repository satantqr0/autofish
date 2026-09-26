from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Product, ProductPriceHistory, ProductScore, ProductStockHistory
from app.schemas.catalog import ProductCreate
from app.services.catalog import create_catalog_product, serialize_product


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def payload(**overrides):
    values = {
        "supplier_code": "MANUAL-001",
        "supplier_name": "测试供应商",
        "external_product_id": "P-001",
        "external_sku_id": "S-001",
        "internal_code": "AF-P001",
        "sku_code": "AF-S001",
        "title": "Mini PC VESA 支架",
        "category": "被动配件",
        "supplier_price": Decimal("42"),
        "shipping_cost": Decimal("6"),
        "platform_fee": Decimal("2"),
        "after_sales_reserve": Decimal("4"),
        "minimum_profit": Decimal("20"),
        "target_profit": Decimal("28"),
        "negotiation_margin": Decimal("5"),
        "stock": 80,
    }
    values.update(overrides)
    return ProductCreate(**values)


def test_manual_import_builds_traceable_product_graph(db):
    product = create_catalog_product(db, payload())
    db.commit()
    db.refresh(product)

    loaded = db.scalar(select(Product).where(Product.id == product.id))
    result = serialize_product(loaded)

    assert result["supplier"]["code"] == "MANUAL-001"
    assert result["sku"]["minimum_sale_price"] == "74.00"
    assert result["sku"]["target_sale_price"] == "82.00"
    assert result["sku"]["recommended_price"] == "87.00"
    assert db.scalar(select(func.count(ProductPriceHistory.id))) == 1
    assert db.scalar(select(func.count(ProductStockHistory.id))) == 1
    assert db.scalar(select(func.count(ProductScore.id))) == 1


def test_catalog_preserves_external_supplier_id_and_source_image(db):
    image = "https://cbu01.alicdn.com/img/ibank/source.jpg"
    product = create_catalog_product(
        db,
        payload(
            external_supplier_id="1688-SUP-1",
            images=[image],
            supplier_source_type="1688-product-find-cli",
        ),
    )
    db.commit()

    assert product.images == [image]
    supplier_product = product.skus[0].supplier_links[0].supplier_sku.supplier_product
    assert supplier_product.external_supplier_id == "1688-SUP-1"
    assert supplier_product.raw_snapshot["source"] == "1688-product-find-cli"


def test_excluded_category_is_rejected_before_write(db):
    with pytest.raises(ValueError, match="排除类目"):
        create_catalog_product(db, payload(title="手机电池"))
    assert db.scalar(select(func.count(Product.id))) == 0
