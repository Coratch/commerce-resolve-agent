"""提供会话订单上下文使用的确定性订单号解析。"""

import re

from commerce_resolve.business_models import ORDER_ID_PATTERN_TEXT

ORDER_ID_PATTERN = re.compile(
    ORDER_ID_PATTERN_TEXT.removeprefix("^").removesuffix("$"),
    re.IGNORECASE,
)


def extract_explicit_order_id(text: str) -> str | None:
    """从用户原文提取首个显式订单号并规范为大写。"""

    match = ORDER_ID_PATTERN.search(text)
    return match.group(0).upper() if match is not None else None
