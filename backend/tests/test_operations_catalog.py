from app.api.routes.operations import operations_guide
from app.services.operations_catalog import PRICE_CHECKED_AT, get_operations_guide


def test_operations_guide_covers_the_full_operating_chain():
    guide = get_operations_guide()
    ids = {workflow["id"] for workflow in guide["workflows"]}

    assert guide["workflow_count"] == 14
    assert PRICE_CHECKED_AT == "2026-08-17"
    assert {
        "source-discovery",
        "premium-images",
        "owner-copy",
        "xianyu-publish",
        "customer-service",
        "purchase",
        "logistics",
        "aftersales",
        "adversarial-testing",
        "nas-operations",
    }.issubset(ids)
    assert all(item["steps"] and item["evidence"] for item in guide["workflows"])


def test_model_snapshot_is_traceable_and_does_not_mutate():
    first = get_operations_guide()
    first["workflows"][0]["name"] = "mutated"
    second = get_operations_guide()

    assert second["workflows"][0]["name"] == "货源发现与初筛"
    assert all(item["pricing_url"].startswith("https://") for item in second["model_providers"])
    assert second["model_strategy"]["single_provider"]["primary"] == "GPT-5.6 Luna"


def test_route_returns_same_read_only_guide_for_authenticated_dependency():
    response = operations_guide(_user=object())

    assert response["version"] == "2026.08.17-1"
    assert response["guardrails"]
