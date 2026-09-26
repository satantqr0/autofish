from decimal import Decimal

import pytest

from app.services.scoring import ScoreInput, calculate_score, excluded_reason, profit_score


def test_weighted_score_and_hash_are_deterministic():
    payload = ScoreInput(
        profit_space=Decimal("80"),
        after_sales_safety=Decimal("90"),
        fault_safety=Decimal("70"),
        compatibility_safety=Decimal("60"),
        transport_safety=Decimal("100"),
        stock_stability=Decimal("50"),
        price_stability=Decimal("80"),
        verticality=Decimal("90"),
    )

    first = calculate_score(payload)
    second = calculate_score(payload)

    assert first.total == Decimal("78.00")
    assert first.input_hash == second.input_hash
    assert sum(Decimal(value) for value in first.weights.values()) == Decimal("100")


def test_score_rejects_out_of_range_inputs():
    with pytest.raises(ValueError, match="profit_space"):
        calculate_score(
            ScoreInput(
                profit_space=Decimal("101"),
                after_sales_safety=Decimal("90"),
                fault_safety=Decimal("90"),
                compatibility_safety=Decimal("90"),
                transport_safety=Decimal("90"),
                stock_stability=Decimal("90"),
                price_stability=Decimal("90"),
                verticality=Decimal("90"),
            )
        )


def test_first_phase_exclusion_and_profit_normalization():
    assert "手机" in excluded_reason("旗舰手机保护壳", "数码")
    assert excluded_reason("Mini PC VESA 支架", "被动配件") is None
    assert profit_score(Decimal("30"), Decimal("100")) == Decimal("60")
