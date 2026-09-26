from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PricingInput:
    supplier_cost: Decimal
    supplier_shipping: Decimal
    platform_fee: Decimal
    after_sales_reserve: Decimal
    minimum_profit: Decimal
    target_profit: Decimal
    negotiation_margin: Decimal


@dataclass(frozen=True)
class PricingResult:
    final_cost: Decimal
    minimum_sale_price: Decimal
    target_sale_price: Decimal
    recommended_price: Decimal
    expected_profit: Decimal
    expected_margin_rate: Decimal


def calculate_pricing(data):
    values = [
        data.supplier_cost,
        data.supplier_shipping,
        data.platform_fee,
        data.after_sales_reserve,
        data.minimum_profit,
        data.target_profit,
        data.negotiation_margin,
    ]
    if any(Decimal(value) < 0 for value in values):
        raise ValueError("pricing values cannot be negative")
    if Decimal(data.minimum_profit) <= 0:
        raise ValueError("minimum_profit must be positive")
    if Decimal(data.target_profit) < Decimal(data.minimum_profit):
        raise ValueError("target_profit cannot be lower than minimum_profit")

    final_cost = money(
        data.supplier_cost + data.supplier_shipping + data.platform_fee + data.after_sales_reserve
    )
    minimum_sale_price = money(final_cost + data.minimum_profit)
    target_sale_price = money(final_cost + data.target_profit)
    recommended_price = money(target_sale_price + data.negotiation_margin)
    expected_profit = money(recommended_price - final_cost)
    expected_margin_rate = (
        (expected_profit / recommended_price).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if recommended_price > 0
        else Decimal("0")
    )
    return PricingResult(
        final_cost=final_cost,
        minimum_sale_price=minimum_sale_price,
        target_sale_price=target_sale_price,
        recommended_price=recommended_price,
        expected_profit=expected_profit,
        expected_margin_rate=expected_margin_rate,
    )


def validate_negotiated_price(candidate, minimum_sale_price):
    candidate_money = money(candidate)
    floor = money(minimum_sale_price)
    if candidate_money < floor:
        raise ValueError("negotiated price cannot be lower than minimum sale price")
    return candidate_money
