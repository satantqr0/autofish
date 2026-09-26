import re
from urllib.parse import urlsplit

from app.models import Product

DEMO_SUPPLIER_CODES = {"1688-DEMO", "DEMO"}
DEMO_DESCRIPTION_MARKERS = ("演示数据", "仅用于演示")
SELLER_SERVICE_COPY = "下单前请先确认库存和规格，商品细节及售后问题可随时沟通。"
SELLER_IDENTITY_COPY_REPLACEMENTS = (
    ("商品由本人负责销售、发货安排与售后处理；下单前可先确认库存和规格。", ""),
    ("商品由本人负责销售、发货安排与售后处理", ""),
    ("商品由本人负责销售", ""),
    ("本人负责销售", ""),
    ("本人负责发货", ""),
    ("由本人发货", ""),
    ("本人发货", ""),
    ("发货安排", ""),
)
SUPPLIER_COPY_REPLACEMENTS = (
    ("代发包邮", "包邮"),
    ("支持一件代发货", ""),
    ("支持一件代发", ""),
    ("支持一件发货", ""),
    ("可一件代发", ""),
    ("可代发货", ""),
    ("可代发", ""),
    ("一件代发货", ""),
    ("一件代发", ""),
    ("一键代发", ""),
    ("支持代发货", ""),
    ("支持代发", ""),
    ("代发货", ""),
    ("厂家直发", ""),
    ("工厂直发", ""),
    ("供应商直发", ""),
    ("供应链直发", ""),
    ("货源直发", ""),
    ("一件发货", ""),
    ("一件起发", ""),
    ("一件起批", ""),
    ("只发一个", ""),
    ("掉落包赔", ""),
    ("无规格", ""),
    ("一键铺货", ""),
    ("批量铺货", ""),
    ("无需囤货", ""),
    ("零库存开店", ""),
    ("招代理", ""),
    ("代理加盟", ""),
    ("分销加盟", ""),
    ("分销货源", ""),
    ("供应商", ""),
    ("供应链", ""),
    ("源头货源", ""),
    ("厂家货源", ""),
    ("无需备货", ""),
    ("零库存", ""),
    ("批发价", ""),
    ("批发", ""),
    ("拿货价", ""),
    ("代销", ""),
    ("分销", ""),
    ("铺货", ""),
    ("批发代发", ""),
    ("货源代发", ""),
    ("货源", ""),
    ("代发", ""),
)
FORBIDDEN_PUBLICATION_PHRASES = tuple(source for source, _replacement in SUPPLIER_COPY_REPLACEMENTS)
FORBIDDEN_SELLER_IDENTITY_PHRASES = tuple(
    source for source, _replacement in SELLER_IDENTITY_COPY_REPLACEMENTS
)
COPY_OBFUSCATION_SEPARATOR = r"[\s·•._/|—–-]*"


def _is_example_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        hostname = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return hostname == "example" or hostname.endswith(".example")


def sanitize_publication_source_text(value: str | None) -> str:
    """Remove supplier-facing sales language before it enters a Xianyu draft."""

    text = str(value or "").strip()
    text = text.replace("发货与库存以确认时的供应链实况为准", "")
    text = text.replace("发货与库存以供应链实况为准", "")
    for source, replacement in (
        *SELLER_IDENTITY_COPY_REPLACEMENTS,
        *SUPPLIER_COPY_REPLACEMENTS,
    ):
        pattern = COPY_OBFUSCATION_SEPARATOR.join(re.escape(char) for char in source)
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = "\n".join(
        line.strip(" ，、；;|/·") for line in text.splitlines() if line.strip(" ，、；;|/·")
    )
    while "，，" in text or "；；" in text or "、、" in text:
        text = text.replace("，，", "，").replace("；；", "；").replace("、、", "、")
    return text.strip(" ，、；;|/·")


def publication_copy_blockers(title: str | None, description: str | None) -> list[str]:
    """Reject supplier/self-fulfilment wording and require neutral buyer guidance."""

    combined = "\n".join(str(value or "") for value in (title, description))
    normalized = re.sub(COPY_OBFUSCATION_SEPARATOR, "", combined).lower()
    hits = [phrase for phrase in FORBIDDEN_PUBLICATION_PHRASES if phrase.lower() in normalized]
    blockers: list[str] = []
    if hits:
        blockers.append("发布文案包含代发或供应链话术：" + "、".join(dict.fromkeys(hits)))
    identity_hits = [
        phrase
        for phrase in FORBIDDEN_SELLER_IDENTITY_PHRASES
        if re.sub(COPY_OBFUSCATION_SEPARATOR, "", phrase).lower() in normalized
    ]
    if identity_hits:
        blockers.append(
            "发布文案包含卖家身份或本人发货说明："
            + "、".join(dict.fromkeys(identity_hits))
        )
    if SELLER_SERVICE_COPY not in str(description or ""):
        blockers.append("发布文案缺少中性的库存、规格与售后沟通提示")
    return blockers


def publication_provenance_blockers(product: Product) -> list[str]:
    """Return deterministic reasons that make a catalog product unsafe to publish.

    Callers must load the product with ``product_query_options()`` so the complete
    enabled supplier chain is evaluated before an external write is considered.
    """

    blockers: list[str] = []
    description = (product.description or "").strip()
    if any(marker in description for marker in DEMO_DESCRIPTION_MARKERS):
        blockers.append("商品说明明确标记为演示数据")

    enabled_links = [link for sku in product.skus for link in sku.supplier_links if link.is_enabled]
    if not enabled_links:
        blockers.append("商品缺少已启用且可追溯的供应商 SKU")

    for link in enabled_links:
        supplier_sku = link.supplier_sku
        supplier_product = supplier_sku.supplier_product if supplier_sku else None
        supplier = supplier_product.supplier if supplier_product else None
        if supplier is None or supplier_product is None:
            blockers.append("商品供应商链路不完整")
            continue

        supplier_code = (supplier.code or "").strip().upper()
        supplier_name = (supplier.name or "").strip()
        source_type = (supplier.source_type or "").strip().upper()
        if supplier_code in DEMO_SUPPLIER_CODES or supplier_code.startswith("DEMO-"):
            blockers.append("供应商代码标记为演示来源")
        if "演示供应商" in supplier_name:
            blockers.append("供应商名称标记为演示来源")
        if source_type == "DEMO":
            blockers.append("供应商来源类型为 DEMO")
        if _is_example_url(supplier.base_url) or _is_example_url(supplier_product.url):
            blockers.append("供应商链接使用保留的 .example 演示域名")

    return list(dict.fromkeys(blockers))


def assert_product_publishable(product: Product) -> None:
    blockers = publication_provenance_blockers(product)
    if blockers:
        raise ValueError("商品发布来源校验失败：" + "；".join(blockers))
