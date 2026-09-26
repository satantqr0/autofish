from decimal import Decimal

import pytest

from app.services.pricing import PricingInput, calculate_pricing, validate_negotiated_price


def pricing_input(**overrides):
    values = {
        "supplier_cost": Decimal("42"),
        "supplier_shipping": Decimal("6"),
        "platform_fee": Decimal("2"),
        "after_sales_reserve": Decimal("4"),
        "minimum_profit": Decimal("30"),
        "target_profit": Decimal("36"),
        "negotiation_margin": Decimal("9"),
    }
    values.update(overrides)
    return PricingInput(**values)


def test_pricing_calculates_floor_target_and_list_price():
    result = calculate_pricing(pricing_input())

    assert result.final_cost == Decimal("54.00")
    assert result.minimum_sale_price == Decimal("84.00")
    assert result.target_sale_price == Decimal("90.00")
    assert result.recommended_price == Decimal("99.00")
    assert result.expected_profit == Decimal("45.00")


def test_target_profit_cannot_be_lower_than_minimum_profit():
    with pytest.raises(ValueError, match="target_profit"):
        calculate_pricing(pricing_input(target_profit=Decimal("29")))


def test_negotiation_floor_is_enforced_in_code():
    assert validate_negotiated_price("84", "84") == Decimal("84.00")
    with pytest.raises(ValueError, match="minimum sale price"):
        validate_negotiated_price("83.99", "84")
