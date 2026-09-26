import asyncio
import hashlib
import hmac

from adapters.base import Money
from adapters.supplier.port import SourcingQuery
from adapters.supplier.product_find_1688 import ProductFind1688Adapter
from adapters.supplier.shopkeeper_1688 import Shopkeeper1688Adapter
from adapters.xianyu.goofish_cli import GoofishCliAdapter
from adapters.xianyu.port import PublishRequest, ShipOrderRequest
from adapters.xianyu.top_api import TopXianyuAdapter, sign_top_params
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, ExternalSnapshot, SourcingCandidate
from app.services.adapter_factory import MAX_SECRET_BYTES, _read_secret
from app.services.integrations import (
    enrich_product_find_candidate,
    sanitize_payload,
    save_snapshot,
    serialize_candidate,
    start_sync_run,
    upsert_sourcing_candidate,
)


class FakeRunner:
    available = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def run(self, *arguments):
        self.calls.append(arguments)
        return self.payload


def test_mounted_supplier_secret_is_preferred(tmp_path):
    secret_file = tmp_path / "1688-api-key"
    secret_file.write_text("mounted-secret\n", encoding="utf-8")

    assert _read_secret(str(secret_file), "environment-fallback") == "mounted-secret"


def test_missing_or_oversized_supplier_secret_uses_fallback(tmp_path):
    missing = tmp_path / "missing"
    oversized = tmp_path / "oversized"
    oversized.write_text("x" * (MAX_SECRET_BYTES + 1), encoding="utf-8")

    assert _read_secret(str(missing), "fallback") == "fallback"
    assert _read_secret(str(oversized), "fallback") == "fallback"


def test_external_snapshot_is_sanitized_and_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = start_sync_run(
            db,
            platform="1688",
            adapter_name="fixture",
            adapter_version="1",
            operation="TEST",
            requested_by_user_id=None,
            correlation_id="test-correlation",
        )
        raw = {"id": "P1", "token": "secret", "nested": {"phone": "13800000000"}}
        first, first_created = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="PRODUCT",
            external_id="P1",
            adapter_name="fixture",
            adapter_version="1",
            normalized_data={"external_product_id": "P1"},
            raw_payload=raw,
        )
        second, second_created = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="PRODUCT",
            external_id="P1",
            adapter_name="fixture",
            adapter_version="1",
            normalized_data={"external_product_id": "P1"},
            raw_payload=raw,
        )
        db.commit()

        assert first.id == second.id
        assert first_created is True
        assert second_created is False
        assert first.raw_payload["token"] == "[REDACTED]"
        assert first.raw_payload["nested"]["phone"] == "[REDACTED]"
        assert db.scalar(select(func.count(ExternalSnapshot.id))) == 1


def test_canonical_candidate_is_import_ready_only_with_supplier_and_skus():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = start_sync_run(
            db,
            platform="1688",
            adapter_name="fixture",
            adapter_version="1",
            operation="TEST",
            requested_by_user_id=None,
            correlation_id="test-candidate",
        )
        normalized = {
            "external_product_id": "P2",
            "title": "铝合金笔记本支架",
            "category": "被动配件",
            "external_supplier_id": "SUP-2",
            "supplier_name": "测试工厂",
            "score_inputs": {
                "profit_space": "80",
                "after_sales_safety": "90",
                "fault_safety": "90",
                "compatibility_safety": "85",
                "transport_safety": "90",
                "stock_stability": "80",
                "price_stability": "75",
                "verticality": "95",
            },
            "skus": [
                {
                    "external_sku_id": "SKU-2",
                    "price": "22.50",
                    "shipping": "5",
                    "stock": 80,
                }
            ],
        }
        snapshot, _ = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="PRODUCT",
            external_id="P2",
            adapter_name="fixture",
            adapter_version="1",
            normalized_data=normalized,
            raw_payload=normalized,
        )
        candidate = upsert_sourcing_candidate(
            db,
            snapshot=snapshot,
            adapter_name="fixture",
            adapter_version="1",
            data=normalized,
        )
        db.commit()

        loaded = db.scalar(select(SourcingCandidate).where(SourcingCandidate.id == candidate.id))
        assert str(loaded.minimum_price) == "22.50"
        assert loaded.sku_count == 1
        assert loaded.stock == 80
        assert str(loaded.score) == "85.50"
        assert loaded.normalized_data["external_supplier_id"] == "SUP-2"


def test_excluded_category_cannot_be_marked_import_ready():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = start_sync_run(
            db,
            platform="1688",
            adapter_name="fixture",
            adapter_version="1",
            operation="TEST",
            requested_by_user_id=None,
            correlation_id="test-excluded",
        )
        normalized = {
            "external_product_id": "P3",
            "title": "高容量充电宝",
            "category": "数码配件",
            "external_supplier_id": "SUP-3",
            "supplier_name": "测试工厂",
            "skus": [{"external_sku_id": "SKU-3", "price": "30", "stock": 10}],
        }
        snapshot, _ = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="PRODUCT",
            external_id="P3",
            adapter_name="fixture",
            adapter_version="1",
            normalized_data=normalized,
            raw_payload=normalized,
        )
        candidate = upsert_sourcing_candidate(
            db,
            snapshot=snapshot,
            adapter_name="fixture",
            adapter_version="1",
            data=normalized,
        )

        assert candidate.status == "EXCLUDED"
        assert "充电宝" in candidate.normalized_data["excluded_reason"]


def test_shopkeeper_search_normalizes_structured_candidates():
    adapter = Shopkeeper1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "data": {
                "products": [
                    {
                        "id": "1688-P1",
                        "title": "桌面理线器",
                        "price": "9.80",
                        "url": "https://detail.1688.com/offer/1688-P1.html",
                        "stats": {"categoryListName": "办公/收纳"},
                    }
                ]
            },
        }
    )

    result = asyncio.run(adapter.search_products(SourcingQuery(query="理线器")))

    assert result.status.value == "OK"
    assert result.data[0].external_product_id == "1688-P1"
    assert result.data[0].price.amount == "9.80"
    assert result.data[0].category == "办公/收纳"


def test_shopkeeper_health_rejects_ak_that_upstream_does_not_accept():
    adapter = Shopkeeper1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": False,
            "markdown": "签名无效或已过期（401）",
            "data": {"total": 0, "valid_count": 0},
        }
    )

    result = asyncio.run(adapter.health())

    assert adapter.runner.calls == [("shops",)]
    assert result.status.value == "AUTH_REQUIRED"
    assert result.data.authenticated is False
    assert result.safe_message == "1688 AK 无效或已过期"


def test_shopkeeper_health_requires_a_real_signed_read_to_succeed():
    adapter = Shopkeeper1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "data": {"total": 2, "valid_count": 1, "expired_count": 1},
        }
    )

    result = asyncio.run(adapter.health())

    assert result.status.value == "OK"
    assert result.data.authenticated is True
    assert result.data.details["bound_shops"] == 2
    assert result.data.details["valid_shops"] == 1


def test_shopkeeper_trend_uses_documented_read_only_command():
    adapter = Shopkeeper1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "markdown": "趋势结果",
            "data": {"trends": [{"keyword": "桌面收纳", "heat": 88}]},
        }
    )

    result = asyncio.run(adapter.get_trends("桌面收纳"))

    assert adapter.runner.calls == [("trend", "--query", "桌面收纳")]
    assert result.status.value == "OK"
    assert result.data.kind == "TREND"
    assert result.data.items[0]["heat"] == 88


def test_shopkeeper_opportunities_preserves_raw_evidence():
    adapter = Shopkeeper1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "data": {"opportunities": [{"category": "电脑配件", "score": 91}]},
        }
    )

    result = asyncio.run(adapter.get_opportunities("电脑配件"))

    assert adapter.runner.calls == [("opportunities", "--category", "电脑配件")]
    assert result.data.raw_payload["opportunities"][0]["score"] == 91


def test_product_find_search_preserves_documented_supplier_sku_and_stock_fields():
    adapter = ProductFind1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "data": {
                "similar_products": [
                    {
                        "product_id": "PF-1",
                        "title": "铝合金桌面理线架",
                        "detail_url": "https://detail.1688.com/offer/PF-1.html",
                        "image_url": "https://example.invalid/PF-1.png",
                        "price": "12.80",
                        "sku_id": "PF-SKU-1",
                        "sku_title": "银色 / 40cm",
                        "category_name": "办公/收纳",
                        "supplier_id": "SUP-1688-1",
                        "supplier": "示例工厂",
                        "stock_amount": 320,
                        "quantity_begin": 1,
                        "sold_count": 680,
                        "service_infos": [{"value": "48小时发货"}],
                        "selling_points": [{"value": "一件代发"}],
                    }
                ]
            },
        }
    )

    result = asyncio.run(adapter.search_products(SourcingQuery(query="桌面理线架")))

    assert adapter.runner.calls == [
        ("text_search", "--query", "桌面理线架", "--limit", "20")
    ]
    assert result.status.value == "OK"
    item = result.data[0]
    assert item.external_product_id == "PF-1"
    assert item.external_supplier_id == "SUP-1688-1"
    assert item.supplier_name == "示例工厂"
    assert item.skus[0].external_sku_id == "PF-SKU-1"
    assert item.skus[0].price.amount == "12.80"
    assert item.skus[0].stock == 320
    assert item.stats["quantity_begin"] == 1


def test_product_find_image_and_link_search_are_closed_by_safe_runtime():
    adapter = ProductFind1688Adapter()
    adapter.runner = FakeRunner({"success": True})

    image = asyncio.run(adapter.search_image("https://img.example.com/item.jpg", 5))
    link = asyncio.run(adapter.search_link("https://item.example.com/100", 5))

    assert adapter.runner.calls == []
    assert image.status.value == "UNSUPPORTED"
    assert link.status.value == "UNSUPPORTED"


def test_product_find_does_not_invent_missing_supplier_or_sku_identifiers():
    adapter = ProductFind1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "data": {
                "similar_products": [
                    {
                        "product_id": "PF-2",
                        "title": "简约收纳盒",
                        "price": "8.50-12.00",
                        "supplier": "只有名称的供应商",
                        "stock_amount": "未知",
                    }
                ]
            },
        }
    )

    result = asyncio.run(adapter.search_products(SourcingQuery(query="收纳盒")))

    item = result.data[0]
    assert item.price is None
    assert item.external_supplier_id is None
    assert item.skus == []


def test_product_find_candidate_is_import_ready_only_when_all_real_fields_exist():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    adapter = ProductFind1688Adapter()
    adapter.runner = FakeRunner(
        {
            "success": True,
            "data": {
                "similar_products": [
                    {
                        "product_id": "PF-3",
                        "title": "桌面文件架",
                        "price": "19.90",
                        "sku_id": "PF-SKU-3",
                        "sku_title": "黑色",
                        "category_name": "办公/收纳",
                        "supplier_id": "PF-SUP-3",
                        "supplier": "文件架工厂",
                        "stock_amount": 50,
                        "shipping": "5.00",
                    }
                ]
            },
        }
    )
    result = asyncio.run(adapter.search_products(SourcingQuery(query="文件架")))
    with Session(engine) as db:
        run = start_sync_run(
            db,
            platform="1688",
            adapter_name=adapter.source,
            adapter_version=adapter.source_version,
            operation="TEST",
            requested_by_user_id=None,
            correlation_id="test-product-find",
        )
        normalized = result.data[0].model_dump(mode="json")
        snapshot, _ = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="SUPPLIER_PRODUCT_CANDIDATE",
            external_id="PF-3",
            adapter_name=adapter.source,
            adapter_version=adapter.source_version,
            normalized_data=normalized,
            raw_payload=result.data[0].raw_payload,
        )
        candidate = upsert_sourcing_candidate(
            db,
            snapshot=snapshot,
            adapter_name=adapter.source,
            adapter_version=adapter.source_version,
            data=normalized,
        )
        db.flush()

        assert candidate.sku_count == 1
        assert candidate.stock == 50
        assert candidate.external_supplier_id == "PF-SUP-3"
        assert candidate.category == "办公/收纳"
        assert candidate.normalized_data["skus"][0]["price"]["amount"] == "19.90"
        assert candidate.normalized_data["skus"][0]["shipping"]["amount"] == "5.00"
        assert serialize_candidate(candidate)["import_ready"] is True


def test_authorized_detail_enrichment_adds_only_evidenced_shipping_and_category():
    normalized = {
        "external_product_id": "972222937029",
        "title": "可叠加【新人专享包邮】抽屉分隔内置小型收纳盒桌面分格塑料收纳",
        "supplier_name": "义乌市尽悲贸易有限公司",
        "image_url": "https://cbu01.alicdn.com/item.jpg",
        "skus": [
            {
                "external_sku_id": "SKU-1",
                "spec": {"title": "【米白色】1个装 约29*11.8*7.3cm"},
                "price": {"amount": "6.01", "currency": "CNY"},
                "shipping": None,
                "stock": 100,
            }
        ],
    }
    detail = """# 商品标题
可叠加【新人专享包邮】抽屉分隔内置小型收纳盒桌面分格塑料收纳

# 商品价格
7.01元

# 商品类目
|类目级别|类目名称|
|--|--|
|一级类目|收纳清洁用具|
|二级类目|收纳防尘|
|三级类目|收纳盒|
"""

    enriched = enrich_product_find_candidate(normalized, detail)

    assert enriched["category"] == "收纳清洁用具/收纳防尘/收纳盒"
    assert enriched["skus"][0]["price"]["amount"] == "7.01"
    assert enriched["skus"][0]["shipping"]["amount"] == "0"
    assert enriched["skus"][0]["spec"]["尺寸"] == "29*11.8*7.3cm"
    assert enriched["stats"]["shipping_condition"] == "NEW_BUYER_ONLY"
    assert enriched["stats"]["requires_preorder_recheck"] is True
    assert enriched["stats"]["recommended_operational_stock"] == 1


def test_authorized_detail_enrichment_never_guesses_missing_shipping():
    normalized = {
        "external_product_id": "1",
        "title": "普通桌面收纳盒",
        "skus": [
            {
                "external_sku_id": "SKU-1",
                "price": {"amount": "8", "currency": "CNY"},
                "shipping": None,
                "stock": 10,
            }
        ],
    }
    detail = """# 商品标题
普通桌面收纳盒

# 商品价格
8元

# 商品类目
|一级类目|收纳清洁用具|
"""

    enriched = enrich_product_find_candidate(normalized, detail)

    assert enriched["skus"][0]["shipping"] is None
    assert "shipping_evidence" not in enriched["stats"]
    assert enriched["stats"]["requires_preorder_recheck"] is False


def test_authorized_detail_enrichment_accepts_exact_structured_free_shipping_tag():
    normalized = {
        "external_product_id": "2",
        "title": "普通桌面收纳架",
        "stats": {"promotion_tags": ["满150减10", "包邮"]},
        "skus": [
            {
                "external_sku_id": "SKU-2",
                "price": {"amount": "10.39", "currency": "CNY"},
                "shipping": None,
                "stock": 500,
            }
        ],
    }
    detail = """# 商品标题
普通桌面收纳架

# 商品价格
10.39元

# 商品类目
|一级类目|收纳清洁用具|
"""

    enriched = enrich_product_find_candidate(normalized, detail)

    assert enriched["skus"][0]["shipping"]["amount"] == "0"
    assert enriched["stats"]["shipping_evidence"] == "1688结构化促销标签明确标注包邮"
    assert enriched["stats"]["requires_preorder_recheck"] is False
    assert enriched["adapter_verification"]["free_shipping_asserted_by_title"] is False
    assert enriched["adapter_verification"]["free_shipping_asserted_by_promotion_tag"] is True


def test_authorized_detail_enrichment_rejects_conditional_promotion_tag_as_free_shipping():
    normalized = {
        "external_product_id": "3",
        "title": "普通桌面收纳架",
        "stats": {"promotion_tags": ["新人包邮", "满99包邮"]},
        "skus": [
            {
                "external_sku_id": "SKU-3",
                "price": {"amount": "9", "currency": "CNY"},
                "shipping": None,
                "stock": 100,
            }
        ],
    }
    detail = """# 商品标题
普通桌面收纳架

# 商品价格
9元
"""

    enriched = enrich_product_find_candidate(normalized, detail)

    assert enriched["skus"][0]["shipping"] is None
    assert "shipping_evidence" not in enriched["stats"]
    assert enriched["adapter_verification"]["free_shipping_asserted_by_promotion_tag"] is False


def test_goofish_product_list_normalizes_read_only_rows():
    adapter = GoofishCliAdapter()
    adapter.runner = FakeRunner(
        {
            "items": [
                {
                    "item_id": "XY-1",
                    "title": "VESA 支架",
                    "price": "¥89.00",
                    "status": "在售",
                    "image_url": "https://example.invalid/item.png",
                }
            ]
        }
    )

    result = asyncio.run(adapter.list_products())

    assert result.status.value == "OK"
    assert result.data[0].external_product_id == "XY-1"
    assert result.data[0].price.amount == "89.00"
    assert sanitize_payload(result.data[0].raw_payload) == result.data[0].raw_payload


def test_top_signature_uses_sorted_hmac_md5_contract():
    params = {"method": "alibaba.idle.user.permit.query", "app_key": "10001", "v": "2.0"}
    canonical = "".join(f"{key}{params[key]}" for key in sorted(params))
    expected = hmac.new(b"top-secret", canonical.encode(), hashlib.md5).hexdigest().upper()

    assert sign_top_params(params, "top-secret") == expected


def test_top_health_does_not_treat_string_false_as_authorized():
    adapter = TopXianyuAdapter("app", "secret", "session")

    async def fake_call(_method, **_params):
        return {"success": "false"}

    adapter.client.call = fake_call
    result = asyncio.run(adapter.health())

    assert result.status.value == "AUTH_REQUIRED"
    assert result.data.authenticated is False


def test_top_shipping_uses_documented_order_ship_method():
    adapter = TopXianyuAdapter("app", "secret", "session")
    calls = []

    async def fake_call(method, **params):
        calls.append((method, params))
        return {"result": {"data": True}}

    adapter.client.call = fake_call
    result = asyncio.run(
        adapter.ship_order(
            ShipOrderRequest(
                external_order_id="ORDER-1",
                carrier="顺丰",
                tracking_number_ref="secret://tracking/ORDER-1",
                logistics_code="SF",
                sender_name="发货人",
                sender_phone="13800000000",
                sender_address="已脱敏地址",
                sender_division_id=440100,
                idempotency_key="ship-order-method-test",
                platform_payload={"ship_mail_no": "SF0000001"},
            )
        )
    )

    assert result.status.value == "OK"
    assert calls[0][0] == "alibaba.idle.isv.order.ship"
    assert calls[0][1]["biz_order_id"] == "ORDER-1"


def test_goofish_publish_without_confirmed_item_id_requires_manual_review():
    adapter = GoofishCliAdapter()
    adapter.runner = FakeRunner({"ok": True})
    request = PublishRequest(
        internal_product_id=1,
        title="桌面支架",
        description="测试描述",
        price=Money(amount="88.00"),
        image_refs=["/tmp/item.png"],
        idempotency_key="publish-confirmation-test",
    )

    result = asyncio.run(adapter.publish_product(request))

    assert result.status.value == "MANUAL_REQUIRED"
    assert result.error_code == "PUBLISH_UNCONFIRMED"


def test_goofish_publish_refuses_remote_images_before_cli_write():
    adapter = GoofishCliAdapter()
    runner = FakeRunner({"ok": True, "item_id": "123"})
    adapter.runner = runner
    request = PublishRequest(
        internal_product_id=1,
        title="桌面支架",
        description="测试描述",
        price=Money(amount="88.00"),
        image_refs=["https://img.example.com/item.png"],
        idempotency_key="publish-remote-image-test",
    )

    result = asyncio.run(adapter.publish_product(request))

    assert result.status.value == "MANUAL_REQUIRED"
    assert result.error_code == "REMOTE_IMAGES_REQUIRE_GATEWAY"
    assert runner.calls == []
