from datetime import date, datetime
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Account, Base, Product, User, XianyuProduct, XianyuProductDailyMetric
from app.services.xianyu_metrics import (
    EXPECTED_HEADERS,
    diagnose_product,
    import_seller_workbench_xlsx,
    metrics_overview,
    parse_seller_workbench_xlsx,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_user_and_account(db):
    user = User(username="operator", password_hash="unused", role="operator", is_active=True)
    db.add(user)
    db.flush()
    account = Account(
        platform="XIANYU",
        external_account_id="manual-browser:metrics",
        nickname="流量测试账号",
        owner_user_id=user.id,
        status="MANUAL_READ_ONLY",
    )
    db.add(account)
    db.flush()
    catalog_product = Product(
        internal_code="AF-METRIC-1",
        title="透明旋转收纳架",
        category="桌面收纳",
    )
    db.add(catalog_product)
    db.flush()
    product = XianyuProduct(
        account_id=account.id,
        product_id=catalog_product.id,
        external_product_id="1075744605568",
        status="ACTIVE",
        published_title="透明旋转收纳架",
    )
    db.add(product)
    db.flush()
    return user, account, product


def report_bytes(*, product_id="1075744605568", exposures=53, views=0, formula=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "商品明细表"
    sheet.append(EXPECTED_HEADERS)
    row = [
        product_id,
        "透明旋转收纳架",
        "https://example.invalid/product.jpg",
        "家居",
        "收纳",
        "桌面收纳",
        "置物架",
        "其他",
        39.9,
        1,
        datetime(2026, 8, 17),
        exposures,
        exposures,
        views,
        views,
        0,
        0,
        0,
        0,
        "-",
        0,
        0,
        0,
        0,
        0,
        0,
    ]
    if formula:
        row[11] = "=50+3"
    sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_metrics_report_import_is_idempotent_and_links_managed_product(db):
    user, account, product = make_user_and_account(db)
    content = report_bytes()
    first = import_seller_workbench_xlsx(
        db,
        content=content,
        metric_date=date(2026, 8, 17),
        user=user,
        correlation_id="metrics-1",
    )
    second = import_seller_workbench_xlsx(
        db,
        content=content,
        metric_date=date(2026, 8, 17),
        user=user,
        correlation_id="metrics-2",
    )

    assert first["created"] == 1
    assert first["managed_products"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1
    assert db.scalar(select(func.count(XianyuProductDailyMetric.id))) == 1
    metric = db.scalar(select(XianyuProductDailyMetric))
    assert metric.account_id == account.id
    assert metric.xianyu_product_id == product.id
    assert metric.exposures == 53
    assert metric.views == 0


def test_metrics_overview_returns_real_ratios_and_collection_gate(db):
    user, _, _ = make_user_and_account(db)
    import_seller_workbench_xlsx(
        db,
        content=report_bytes(exposures=53, views=1),
        metric_date=date(2026, 8, 17),
        user=user,
        correlation_id="metrics-overview",
    )

    result = metrics_overview(db, user=user, days=7)

    assert result["summary"]["exposures"] == 53
    assert result["summary"]["views"] == 1
    assert result["summary"]["ctr"] == "0.0189"
    assert result["summary"]["managed_ctr"] == "0.0189"
    assert result["products"][0]["diagnosis"]["code"] == "COLLECTING_DATA"
    assert result["strategy"]["observation_days"] == 3


def test_metrics_parser_rejects_formula_cells():
    with pytest.raises(ValueError, match="包含公式"):
        parse_seller_workbench_xlsx(report_bytes(formula=True))


def test_diagnosis_changes_only_after_evidence_thresholds():
    low_click = diagnose_product(
        data_days=3,
        listing_days=4,
        metrics={
            "exposures": 100,
            "views": 1,
            "inquiries": 0,
            "paid_orders": 0,
            "refund_success_orders": 0,
        },
    )
    high_refund = diagnose_product(
        data_days=7,
        listing_days=20,
        metrics={
            "exposures": 500,
            "views": 80,
            "inquiries": 10,
            "paid_orders": 5,
            "refund_success_orders": 1,
        },
    )

    assert low_click["code"] == "LOW_CLICK"
    assert low_click["actions"][0] == "优先更换首图并保留原价"
    assert high_refund["code"] == "HIGH_REFUND"
    assert high_refund["severity"] == "critical"
