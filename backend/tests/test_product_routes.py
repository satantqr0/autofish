from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.products import _load_products
from app.models import Base
from app.schemas.catalog import ProductCreate
from app.services.catalog import create_catalog_product


def test_product_list_query_deduplicates_eager_loaded_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        create_catalog_product(
            db,
            ProductCreate(
                supplier_code="MANUAL-ROUTE",
                supplier_name="路由测试供应商",
                external_product_id="P-ROUTE",
                external_sku_id="S-ROUTE",
                internal_code="AF-ROUTE-P",
                sku_code="AF-ROUTE-S",
                title="VESA 被动支架",
                category="被动配件",
                supplier_price=Decimal("10"),
                minimum_profit=Decimal("5"),
                target_profit=Decimal("8"),
                stock=20,
            ),
        )
        db.commit()

        products = _load_products(db)

        assert len(products) == 1
        assert products[0].internal_code == "AF-ROUTE-P"
