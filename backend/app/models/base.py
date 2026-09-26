from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Lifecycle(StrEnum):
    CANDIDATE = "CANDIDATE"
    TESTING = "TESTING"
    ACTIVE = "ACTIVE"
    WINNER = "WINNER"
    DECLINING = "DECLINING"
    PAUSED = "PAUSED"
    REMOVED = "REMOVED"


class XianyuStatus(StrEnum):
    UNPUBLISHED = "UNPUBLISHED"
    REVIEW_PENDING = "REVIEW_PENDING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REMOVED = "REMOVED"


class OrderStatus(StrEnum):
    NEW = "NEW"
    PAID = "PAID"
    SUPPLIER_CHECK = "SUPPLIER_CHECK"
    PURCHASE_PENDING = "PURCHASE_PENDING"
    PURCHASED = "PURCHASED"
    SUPPLIER_SHIPPED = "SUPPLIER_SHIPPED"
    XIANYU_SHIPPED = "XIANYU_SHIPPED"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRICE_CHANGED = "PRICE_CHANGED"
    PURCHASE_FAILED = "PURCHASE_FAILED"
    ADDRESS_ERROR = "ADDRESS_ERROR"
    LOGISTICS_ERROR = "LOGISTICS_ERROR"
    AFTERSALES = "AFTERSALES"
    REFUND_PENDING = "REFUND_PENDING"
    DISPUTE = "DISPUTE"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    CANCELLED = "CANCELLED"


class JobType(StrEnum):
    SYNC_SUPPLIER_PRICE = "SYNC_SUPPLIER_PRICE"
    SYNC_SUPPLIER_STOCK = "SYNC_SUPPLIER_STOCK"
    GENERATE_PRODUCT = "GENERATE_PRODUCT"
    PUBLISH_PRODUCT = "PUBLISH_PRODUCT"
    SYNC_MESSAGES = "SYNC_MESSAGES"
    REPLY_MESSAGE = "REPLY_MESSAGE"
    SYNC_ORDERS = "SYNC_ORDERS"
    CREATE_PURCHASE = "CREATE_PURCHASE"
    SYNC_LOGISTICS = "SYNC_LOGISTICS"
    ANALYZE_SKU = "ANALYZE_SKU"


class AutomationScope(StrEnum):
    GLOBAL = "GLOBAL"
    PUBLISH = "PUBLISH"
    CUSTOMER_SERVICE = "CUSTOMER_SERVICE"
    PURCHASE = "PURCHASE"
    REPRICING = "REPRICING"
    LOGISTICS = "LOGISTICS"
