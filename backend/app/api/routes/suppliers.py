from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import Supplier, SupplierProduct, User

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Supplier, func.count(SupplierProduct.id))
        .outerjoin(SupplierProduct, SupplierProduct.supplier_id == Supplier.id)
        .group_by(Supplier.id)
        .order_by(Supplier.name)
    ).all()
    return [
        {
            "id": supplier.id,
            "code": supplier.code,
            "name": supplier.name,
            "source_type": supplier.source_type,
            "status": supplier.status,
            "product_count": count,
        }
        for supplier, count in rows
    ]
