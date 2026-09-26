import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import (
    Lifecycle,
    Product,
    ProductPriceHistory,
    ProductScore,
    ProductSKU,
    ProductStockHistory,
    ProductSupplierLink,
    Supplier,
    SupplierProduct,
    SupplierSKU,
)
from app.services.audit import write_audit
from app.services.pricing import PricingInput, calculate_pricing
from app.services.scoring import (
    ScoreInput,
    calculate_score,
    excluded_reason,
    profit_score,
)


class DuplicateCatalogItem(ValueError):
    pass


def _snapshot_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def create_catalog_product(db, data, actor_user_id=None, correlation_id=None):
    reason = excluded_reason(data.title, data.category)
    if reason:
        raise ValueError(reason)

    if db.scalar(select(Product).where(Product.internal_code == data.internal_code)):
        raise DuplicateCatalogItem(f"internal_code already exists: {data.internal_code}")
    if db.scalar(select(ProductSKU).where(ProductSKU.sku_code == data.sku_code)):
        raise DuplicateCatalogItem(f"sku_code already exists: {data.sku_code}")

    supplier = db.scalar(select(Supplier).where(Supplier.code == data.supplier_code))
    if supplier is None:
        supplier = Supplier(
            code=data.supplier_code,
            name=data.supplier_name,
            source_type=data.supplier_source_type,
            status="ACTIVE",
            metadata_json={"created_via": "catalog_import"},
        )
        db.add(supplier)
        db.flush()

    supplier_product = db.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.external_product_id == data.external_product_id,
        )
    )
    raw_snapshot = {
        "external_product_id": data.external_product_id,
        "external_supplier_id": data.external_supplier_id,
        "title": data.title,
        "category": data.category,
        "url": data.supplier_url,
        "source": data.supplier_source_type,
    }
    if supplier_product is None:
        supplier_product = SupplierProduct(
            supplier_id=supplier.id,
            external_product_id=data.external_product_id,
            title=data.title,
            url=data.supplier_url,
            category=data.category,
            supplier_name=data.supplier_name,
            external_supplier_id=data.external_supplier_id,
            raw_snapshot=raw_snapshot,
            snapshot_hash=_snapshot_hash(raw_snapshot),
            status="ACTIVE",
            last_checked_at=datetime.now(UTC),
        )
        db.add(supplier_product)
        db.flush()
    elif data.external_supplier_id and not supplier_product.external_supplier_id:
        supplier_product.external_supplier_id = data.external_supplier_id

    supplier_sku = db.scalar(
        select(SupplierSKU).where(
            SupplierSKU.supplier_product_id == supplier_product.id,
            SupplierSKU.external_sku_id == data.external_sku_id,
        )
    )
    if supplier_sku is None:
        supplier_sku = SupplierSKU(
            supplier_product_id=supplier_product.id,
            external_sku_id=data.external_sku_id,
            spec=data.spec,
            unit_price=data.supplier_price,
            shipping_cost=data.shipping_cost,
            stock=data.stock,
            stock_status="IN_STOCK" if data.stock > 0 else "OUT_OF_STOCK",
            last_checked_at=datetime.now(UTC),
        )
        db.add(supplier_sku)
        db.flush()

    pricing = calculate_pricing(
        PricingInput(
            supplier_cost=data.supplier_price,
            supplier_shipping=data.shipping_cost,
            platform_fee=data.platform_fee,
            after_sales_reserve=data.after_sales_reserve,
            minimum_profit=data.minimum_profit,
            target_profit=data.target_profit,
            negotiation_margin=data.negotiation_margin,
        )
    )
    score = calculate_score(
        ScoreInput(
            profit_space=profit_score(pricing.expected_profit, pricing.recommended_price),
            after_sales_safety=data.after_sales_safety,
            fault_safety=data.fault_safety,
            compatibility_safety=data.compatibility_safety,
            transport_safety=data.transport_safety,
            stock_stability=data.stock_stability,
            price_stability=data.price_stability,
            verticality=data.verticality,
        )
    )

    product = Product(
        internal_code=data.internal_code,
        title=data.title,
        category=data.category,
        description=data.description,
        images=data.images,
        compatibility=data.compatibility,
        lifecycle=Lifecycle(data.lifecycle),
    )
    db.add(product)
    db.flush()

    product_sku = ProductSKU(
        product_id=product.id,
        sku_code=data.sku_code,
        spec=data.spec,
        supplier_cost=data.supplier_price,
        supplier_shipping=data.shipping_cost,
        platform_fee=data.platform_fee,
        after_sales_reserve=data.after_sales_reserve,
        minimum_profit=data.minimum_profit,
        target_profit=data.target_profit,
        negotiation_margin=data.negotiation_margin,
        final_cost=pricing.final_cost,
        minimum_sale_price=pricing.minimum_sale_price,
        recommended_price=pricing.recommended_price,
        current_sale_price=None,
        expected_profit=pricing.expected_profit,
        stock=data.stock,
        score=score.total,
        last_checked_at=datetime.now(UTC),
    )
    db.add(product_sku)
    db.flush()

    db.add(ProductSupplierLink(product_sku_id=product_sku.id, supplier_sku_id=supplier_sku.id))
    db.add(
        ProductPriceHistory(
            product_sku_id=product_sku.id,
            supplier_sku_id=supplier_sku.id,
            supplier_cost=data.supplier_price,
            shipping_cost=data.shipping_cost,
            recommended_price=pricing.recommended_price,
            source="MANUAL",
            snapshot_hash=supplier_product.snapshot_hash,
        )
    )
    db.add(
        ProductStockHistory(
            product_sku_id=product_sku.id,
            supplier_sku_id=supplier_sku.id,
            stock=data.stock,
            stock_status="IN_STOCK" if data.stock > 0 else "OUT_OF_STOCK",
            source="MANUAL",
            snapshot_hash=supplier_product.snapshot_hash,
        )
    )
    db.add(
        ProductScore(
            product_id=product.id,
            profit_space=score.breakdown["profit_space"],
            after_sales_safety=score.breakdown["after_sales_safety"],
            fault_safety=score.breakdown["fault_safety"],
            compatibility_safety=score.breakdown["compatibility_safety"],
            transport_safety=score.breakdown["transport_safety"],
            stock_stability=score.breakdown["stock_stability"],
            price_stability=score.breakdown["price_stability"],
            verticality=score.breakdown["verticality"],
            total_score=score.total,
            weights=score.weights,
            input_hash=score.input_hash,
        )
    )
    write_audit(
        db,
        action="PRODUCT_CREATED",
        entity_type="PRODUCT",
        entity_id=product.id,
        actor_user_id=actor_user_id,
        actor_type="USER" if actor_user_id else "SYSTEM",
        after_data={"internal_code": product.internal_code, "score": str(score.total)},
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    return product


def product_query_options():
    return (
        selectinload(Product.skus)
        .selectinload(ProductSKU.supplier_links)
        .selectinload(ProductSupplierLink.supplier_sku)
        .selectinload(SupplierSKU.supplier_product)
        .selectinload(SupplierProduct.supplier),
        selectinload(Product.scores),
    )


def mask_identifier(value):
    if not value:
        return "—"
    if len(value) <= 4:
        return "****"
    return f"{value[:3]}****{value[-3:]}"


def serialize_product(product):
    sku = product.skus[0] if product.skus else None
    link = sku.supplier_links[0] if sku and sku.supplier_links else None
    supplier_sku = link.supplier_sku if link else None
    supplier_product = supplier_sku.supplier_product if supplier_sku else None
    supplier = supplier_product.supplier if supplier_product else None
    latest_score = max(product.scores, key=lambda item: item.created_at) if product.scores else None
    breakdown = {}
    if latest_score:
        breakdown = {
            "profit_space": str(latest_score.profit_space),
            "after_sales_safety": str(latest_score.after_sales_safety),
            "fault_safety": str(latest_score.fault_safety),
            "compatibility_safety": str(latest_score.compatibility_safety),
            "transport_safety": str(latest_score.transport_safety),
            "stock_stability": str(latest_score.stock_stability),
            "price_stability": str(latest_score.price_stability),
            "verticality": str(latest_score.verticality),
        }
    return {
        "id": product.id,
        "internal_code": product.internal_code,
        "title": product.title,
        "category": product.category,
        "description": product.description,
        "lifecycle": product.lifecycle.value,
        "xianyu_status": product.xianyu_status.value,
        "created_at": product.created_at,
        "supplier": {
            "name": supplier.name if supplier else "—",
            "code": supplier.code if supplier else "—",
            "masked_external_id": mask_identifier(
                supplier_product.external_product_id if supplier_product else None
            ),
            "url": supplier_product.url if supplier_product else None,
        },
        "sku": {
            "id": sku.id if sku else None,
            "sku_code": sku.sku_code if sku else "—",
            "supplier_cost": str(sku.supplier_cost) if sku else "0.00",
            "supplier_shipping": str(sku.supplier_shipping) if sku else "0.00",
            "platform_fee": str(sku.platform_fee) if sku else "0.00",
            "after_sales_reserve": str(sku.after_sales_reserve) if sku else "0.00",
            "minimum_profit": str(sku.minimum_profit) if sku else "0.00",
            "target_profit": str(sku.target_profit) if sku else "0.00",
            "final_cost": str(sku.final_cost) if sku else "0.00",
            "minimum_sale_price": str(sku.minimum_sale_price) if sku else "0.00",
            "target_sale_price": (str(sku.final_cost + sku.target_profit) if sku else "0.00"),
            "recommended_price": str(sku.recommended_price) if sku else "0.00",
            "expected_profit": str(sku.expected_profit) if sku else "0.00",
            "stock": sku.stock if sku else 0,
            "score": str(sku.score) if sku else "0.00",
            "last_checked_at": sku.last_checked_at if sku else None,
        },
        "score_breakdown": breakdown,
    }
