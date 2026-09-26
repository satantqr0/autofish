import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal

WEIGHTS = {
    "profit_space": Decimal("25"),
    "after_sales_safety": Decimal("20"),
    "fault_safety": Decimal("15"),
    "compatibility_safety": Decimal("10"),
    "transport_safety": Decimal("10"),
    "stock_stability": Decimal("10"),
    "price_stability": Decimal("5"),
    "verticality": Decimal("5"),
}

EXCLUDED_TERMS = {
    "女装",
    "男装",
    "鞋",
    "食品",
    "化妆品",
    "手机",
    "显卡",
    "主板",
    "ssd",
    "内存",
    "电池",
    "充电宝",
    "移动电源",
    "显示器",
    "裸屏",
}


@dataclass(frozen=True)
class ScoreInput:
    profit_space: Decimal
    after_sales_safety: Decimal
    fault_safety: Decimal
    compatibility_safety: Decimal
    transport_safety: Decimal
    stock_stability: Decimal
    price_stability: Decimal
    verticality: Decimal


@dataclass(frozen=True)
class ScoreResult:
    total: Decimal
    breakdown: dict
    weights: dict
    input_hash: str


def excluded_reason(title, category):
    haystack = f"{title} {category}".casefold()
    for term in sorted(EXCLUDED_TERMS):
        if term.casefold() in haystack:
            return f"命中第一阶段排除类目：{term}"
    return None


def profit_score(expected_profit, recommended_price):
    if recommended_price <= 0:
        return Decimal("0")
    margin_percent = Decimal(expected_profit) / Decimal(recommended_price) * Decimal("100")
    return min(Decimal("100"), max(Decimal("0"), margin_percent * Decimal("2")))


def calculate_score(data):
    raw = asdict(data)
    normalized = {}
    for key, value in raw.items():
        decimal_value = Decimal(value)
        if decimal_value < 0 or decimal_value > 100:
            raise ValueError(f"{key} must be between 0 and 100")
        normalized[key] = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    weighted = sum(normalized[key] * WEIGHTS[key] for key in WEIGHTS) / Decimal("100")
    total = weighted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    serializable = {key: str(value) for key, value in normalized.items()}
    input_hash = hashlib.sha256(
        json.dumps(serializable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ScoreResult(
        total=total,
        breakdown=normalized,
        weights={key: str(value) for key, value in WEIGHTS.items()},
        input_hash=input_hash,
    )
