from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.database import get_db
from app.models import (
    Lifecycle,
    Product,
    ProductSKU,
    PublicationTask,
    User,
    XianyuStatus,
)
from app.schemas.catalog import ProductCreate, ProductImportRequest, PublicationQueueRequest
from app.services.audit import write_audit
from app.services.catalog import (
    DuplicateCatalogItem,
    create_catalog_product,
    product_query_options,
    serialize_product,
)
from app.services.publication_guard import assert_product_publishable

router = APIRouter(prefix="/products", tags=["products"])


def _load_products(db, search_text=None, lifecycle=None):
    stmt = select(Product).options(*product_query_options())
    if search_text:
        pattern = f"%{search_text.strip()}%"
        stmt = stmt.join(Product.skus).where(
            or_(
                Product.title.ilike(pattern),
                Product.internal_code.ilike(pattern),
                ProductSKU.sku_code.ilike(pattern),
            )
        )
    if lifecycle:
        try:
            stmt = stmt.where(Product.lifecycle == Lifecycle(lifecycle))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid lifecycle") from exc
    return list(db.scalars(stmt).unique().all())


@router.get("")
def list_products(
    q: str | None = Query(default=None, max_length=200),
    lifecycle: str | None = None,
    supplier: str | None = Query(default=None, max_length=100),
    stock_status: str | None = None,
    sort: str = "score_desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    products = _load_products(db, q, lifecycle)
    items = [serialize_product(product) for product in products]
    if supplier:
        items = [item for item in items if item["supplier"]["code"] == supplier]
    if stock_status == "in_stock":
        items = [item for item in items if item["sku"]["stock"] > 0]
    elif stock_status == "low_stock":
        items = [item for item in items if 0 < item["sku"]["stock"] < 50]
    elif stock_status == "out_of_stock":
        items = [item for item in items if item["sku"]["stock"] == 0]
    if sort == "score_desc":
        items.sort(key=lambda item: float(item["sku"]["score"]), reverse=True)
    elif sort == "stock_asc":
        items.sort(key=lambda item: item["sku"]["stock"])
    elif sort == "updated_desc":
        items.sort(
            key=lambda item: item["sku"]["last_checked_at"] or item["created_at"], reverse=True
        )

    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/meta/suppliers")
def product_suppliers(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models import Supplier

    rows = db.execute(select(Supplier.code, Supplier.name).order_by(Supplier.name)).all()
    return [{"code": code, "name": name} for code, name in rows]


@router.get("/{product_id}")
def get_product(
    product_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    product = db.scalar(
        select(Product).where(Product.id == product_id).options(*product_query_options())
    )
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return serialize_product(product)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        product = create_catalog_product(
            db, payload, actor_user_id=user.id, correlation_id=request.state.correlation_id
        )
        db.commit()
    except DuplicateCatalogItem as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    product = db.scalar(
        select(Product).where(Product.id == product.id).options(*product_query_options())
    )
    return serialize_product(product)


@router.post("/import")
def import_products(
    payload: ProductImportRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    created = []
    skipped = []
    for item in payload.items:
        try:
            product = create_catalog_product(
                db, item, actor_user_id=user.id, correlation_id=request.state.correlation_id
            )
            created.append({"id": product.id, "internal_code": product.internal_code})
        except DuplicateCatalogItem as exc:
            skipped.append({"internal_code": item.internal_code, "reason": str(exc)})
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return {"created": created, "skipped": skipped}


@router.post("/{product_id}/queue-publication", status_code=status.HTTP_201_CREATED)
def queue_publication(
    product_id: int,
    payload: PublicationQueueRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    product = db.scalar(
        select(Product).where(Product.id == product_id).options(*product_query_options())
    )
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    try:
        assert_product_publishable(product)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    existing = db.scalar(
        select(PublicationTask).where(
            PublicationTask.product_id == product_id,
            PublicationTask.status == "MANUAL_REVIEW",
        )
    )
    if existing:
        return {"id": existing.id, "status": existing.status, "idempotent": True}
    task = PublicationTask(
        product_id=product_id,
        status="MANUAL_REVIEW",
        idempotency_key=f"publish-review:{product_id}",
        requested_by_user_id=user.id,
        review_note=payload.note,
    )
    product.xianyu_status = XianyuStatus.REVIEW_PENDING
    db.add(task)
    db.flush()
    write_audit(
        db,
        action="PUBLICATION_REVIEW_QUEUED",
        entity_type="PUBLICATION_TASK",
        entity_id=task.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"product_id": product_id, "status": task.status},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return {"id": task.id, "status": task.status, "idempotent": False}
