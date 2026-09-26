from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.services.runtime_readiness as readiness_service
from app.models import (
    AIProviderConfig,
    AIRuntimeSetting,
    AutomationControl,
    AutomationScope,
    Base,
    BrowserBridgeAgent,
    SourcingCandidate,
)
from app.services.runtime_readiness import _version_at_least, build_runtime_readiness


def _candidate() -> SourcingCandidate:
    return SourcingCandidate(
        adapter_name="1688-product-find-cli",
        adapter_version="1.0",
        external_product_id="readiness-product",
        title="已核验桌面收纳盒",
        url="https://detail.1688.com/offer/readiness-product.html",
        image_url="https://cbu01.alicdn.com/img/ibank/readiness.jpg",
        category="收纳盒",
        supplier_name="测试供应商",
        minimum_price=Decimal("7.01"),
        maximum_price=Decimal("7.01"),
        sku_count=1,
        stock=29997,
        status="DISCOVERED",
        normalized_data={
            "skus": [
                {
                    "external_sku_id": "readiness-sku",
                    "price": {"amount": "7.01", "currency": "CNY"},
                    "shipping": {"amount": "0", "currency": "CNY"},
                    "stock": 29997,
                }
            ],
            "stats": {"detail_verified": True},
            "adapter_verification": {"method": "AUTHORIZED_ADAPTER"},
        },
        last_fetched_at=datetime.now(UTC),
    )


def test_version_comparison_handles_patch_versions():
    assert _version_at_least("0.5.0", (0, 5, 0))
    assert _version_at_least("1.0.0", (0, 5, 0))
    assert not _version_at_least("0.4.9", (0, 5, 0))
    assert not _version_at_least("unknown", (0, 5, 0))


def test_readiness_only_reports_ready_when_model_browser_and_candidate_are_ready(
    monkeypatch,
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        readiness_service,
        "get_settings",
        lambda: SimpleNamespace(
            adapters_enabled=True,
            automation_scheduler_enabled=True,
            xianyu_browser_publish_enabled=True,
        ),
    )
    monkeypatch.setattr(
        readiness_service,
        "configured_bridge_token",
        lambda: "test-bridge-token",
    )

    with Session(engine) as db:
        db.add(
            AIProviderConfig(
                provider="qwen",
                display_name="阿里云百炼",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                text_model="qwen-flash",
                vision_model="qwen3-vl-flash",
                image_model="qwen-image-2.0-pro",
                api_key_encrypted="encrypted",
                is_configured=True,
                last_test_status="SUCCESS",
                capabilities={"text": True, "vision": True, "image_generation": True},
            )
        )
        db.add(
            AIRuntimeSetting(
                id=1,
                enabled=True,
                primary_provider="qwen",
                fallback_provider=None,
                task_routing={
                    "product_copy": "qwen",
                    "customer_service": "qwen",
                    "vision_quality": "qwen",
                    "image_generation": "qwen",
                },
            )
        )
        db.add_all(
            [
                AutomationControl(
                    scope=AutomationScope.GLOBAL,
                    enabled=True,
                    mode="AUTOMATIC",
                ),
                AutomationControl(
                    scope=AutomationScope.PUBLISH,
                    enabled=True,
                    mode="AUTOMATIC",
                ),
            ]
        )
        db.add(
            BrowserBridgeAgent(
                id="chrome-local",
                name="Local Chrome",
                version="0.5.1",
                status="ONLINE",
                capabilities=["PUBLISH_PRODUCT"],
                last_seen_at=datetime.now(UTC),
            )
        )
        db.add(_candidate())
        db.commit()

        result = build_runtime_readiness(db)

        assert result["status"] == "READY"
        assert result["listing_ready"] is True
        assert result["blockers"] == []
        assert result["candidate_pool"]["eligible"] == 1
        assert all(route["ready"] for route in result["ai"]["routes"])
        assert result["browser"]["online"] is True

        db.query(SourcingCandidate).delete()
        db.commit()
        blocked = build_runtime_readiness(db)

        assert blocked["status"] == "BLOCKED"
        assert blocked["listing_ready"] is False
        assert "没有通过门禁的待选货源" in blocked["blockers"]


def test_wan_image_model_and_customer_service_route_are_readiness_compatible():
    provider = SimpleNamespace(
        provider="qwen",
        is_configured=True,
        api_key_encrypted="encrypted",
        last_test_status="SUCCESS",
        capabilities={"text": True, "image_generation": True},
        text_model="qwen-flash",
        vision_model=None,
        image_model="wan2.7-image",
    )
    runtime = SimpleNamespace(
        enabled=True,
        primary_provider="qwen",
        task_routing={"customer_service": "qwen", "image_generation": "qwen"},
    )

    assert readiness_service._provider_issue(
        {"qwen": provider}, runtime, "customer_service", "text"
    ) == ("qwen", None)
    assert readiness_service._provider_issue(
        {"qwen": provider}, runtime, "image_generation", "image_generation"
    ) == ("qwen", None)
