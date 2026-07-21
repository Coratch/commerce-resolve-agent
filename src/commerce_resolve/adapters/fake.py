"""供本地开发和自动化测试使用的确定性适配器。"""

import re
from collections.abc import Mapping
from datetime import date

from commerce_resolve.access import BusinessScope
from commerce_resolve.gateways import Dependencies, PolicyRepository
from commerce_resolve.models import (
    Interpretation,
    InterpretationContext,
    OrderView,
    PolicyAspect,
    PolicyQuery,
    RefundReason,
    ShipmentView,
    ToolResult,
)

ORDER_ID_PATTERN = re.compile(r"\bORD-[A-Z0-9-]{3,32}\b", re.IGNORECASE)
ORDER_INQUIRY_TERMS = ("订单", "物流", "快递", "order", "shipment")
L2_SUPPORT_TERMS = (
    "人工客服",
    "二线客服",
    "高级客服",
    "升级处理",
    "进一步处理",
    "复杂售后",
    "转人工",
    "转接人工",
)
UNSUPPORTED_WRITE_PATTERNS = (
    re.compile(r"(?:请|帮我|我要|立即|给我)(?:发起|申请|办理)?(?:退款|退货|换货)"),
    re.compile(r"(?:退款|退货|换货)\s*ORD-\d{3}", re.IGNORECASE),
)
REFUND_REQUEST_TERMS = ("退款", "退钱", "refund")
UNSUPPORTED_ORDER_WRITE_TERMS = ("取消", "修改地址", "改地址", "cancel")
POLICY_QUESTION_MARKERS = (
    "政策",
    "条件",
    "要求",
    "期限",
    "几天",
    "多久",
    "到账",
    "怎么",
    "如何",
    "流程",
    "方式",
    "例外",
    "哪些",
    "不能退",
    "谁承担",
    "能否",
    "能不能",
    "可以",
    "是否",
    "吗",
    "怎么办",
    "需要什么",
    "退到",
    "哪里",
)
POLICY_TOPIC_TERMS = {
    "refund": ("退款", "到账", "原路退回", "refund"),
    "exchange": ("换货", "换新", "exchange"),
    "return": ("退货", "退回商品", "能退", "退吗", "return"),
}


def _extract_policy_topic(text: str) -> str | None:
    """从用户文本中按受控词表提取售后政策主题。"""

    for topic, terms in POLICY_TOPIC_TERMS.items():
        if any(term in text for term in terms):
            return topic
    return None


def _extract_policy_aspects(text: str, topic: str) -> tuple[PolicyAspect, ...]:
    """从固定关键词提取一个或多个政策方面，并提供最小默认方面。"""

    aspects: list[PolicyAspect] = []
    mappings: tuple[tuple[PolicyAspect, tuple[str, ...]], ...] = (
        ("window", ("期限", "几天", "多少天", "超过期限")),
        ("shipping_fee", ("运费", "邮费", "谁承担")),
        ("exception", ("例外", "不能退", "不支持", "特殊", "怎么办")),
        ("process", ("怎么", "如何", "流程", "申请", "办理")),
        ("timing", ("到账", "工作日", "退款时间")),
        ("method", ("原路", "退到", "退款方式", "渠道")),
        (
            "conditions",
            ("条件", "要求", "能否", "能不能", "是否", "拆封", "激活"),
        ),
    )
    for aspect, terms in mappings:
        if any(term in text for term in terms):
            if aspect == "timing" and topic != "refund":
                continue
            if aspect == "method" and topic != "refund":
                continue
            aspects.append(aspect)
    if not aspects and any(term in text for term in ("可以", "吗")):
        aspects.append("conditions")
    if not aspects:
        aspects.append("conditions")
    return tuple(dict.fromkeys(aspects))


def _extract_product_category(text: str) -> str | None:
    """从受控演示枚举中识别商品类别。"""

    if any(term in text for term in ("卫生用品", "内衣", "贴身用品")):
        return "hygiene"
    if any(term in text for term in ("数字商品", "数字内容", "兑换码")):
        return "digital"
    if any(term in text for term in ("服饰", "衣服", "鞋服")):
        return "apparel"
    if any(term in text for term in ("普通商品", "普通")):
        return "general"
    return None


def _extract_opened(text: str) -> bool | None:
    """识别商品是否已拆封或激活，未明确时保持未知。"""

    if any(term in text for term in ("未拆封", "没拆封", "未激活", "没激活")):
        return False
    if any(term in text for term in ("已拆封", "拆封", "已激活", "激活")):
        return True
    return None


def _is_policy_question(text: str, context: InterpretationContext | None) -> bool:
    """判断文本是通用政策咨询或上一轮政策问题的条件补充。"""

    has_topic = _extract_policy_topic(text) is not None
    has_question_marker = any(marker in text for marker in POLICY_QUESTION_MARKERS)
    if has_topic and has_question_marker:
        return True
    if has_topic and text.strip().endswith("政策"):
        return True
    if context is None or context.previous_policy_query is None:
        return False
    return bool(
        has_question_marker
        or _extract_product_category(text) is not None
        or _extract_opened(text) is not None
        or "海外" in text
    )


def _is_unsupported_write(text: str) -> bool:
    """识别明确要求执行的售后或订单写操作。"""

    if any(term in text for term in UNSUPPORTED_ORDER_WRITE_TERMS):
        return True
    return any(
        pattern.search(text) is not None for pattern in UNSUPPORTED_WRITE_PATTERNS
    )


def _extract_refund_reason(text: str) -> RefundReason | None:
    """从受控关键词提取退款原因，不生成金额或资格结论。"""

    if any(term in text for term in ("不想要", "不需要", "买错", "拍错")):
        return RefundReason(code="no_longer_needed")
    if any(term in text for term in ("质量", "损坏", "瑕疵", "坏了")):
        return RefundReason(code="quality_issue")
    if any(term in text for term in ("物流", "延误", "没收到", "丢件")):
        return RefundReason(code="delivery_issue")
    return None


def _is_refund_request(text: str, context: InterpretationContext | None) -> bool:
    """识别明确退款动作或上一轮退款信息补充。"""

    if any(term in text for term in REFUND_REQUEST_TERMS):
        return not _is_policy_question(text, None)
    return bool(context is not None and context.pending_refund_request)


def _build_policy_query(
    text: str,
    context: InterpretationContext | None,
    *,
    has_order_id: bool,
) -> PolicyQuery:
    """将当前文本与最小上一轮上下文合并为受限政策查询。"""

    previous = context.previous_policy_query if context is not None else None
    topic = _extract_policy_topic(text) or (previous.topic if previous else None)
    if topic is None:
        raise ValueError("政策补充缺少上一轮主题")
    explicit_topic = _extract_policy_topic(text)
    aspects = (
        _extract_policy_aspects(text, topic)
        if explicit_topic is not None
        or any(marker in text for marker in POLICY_QUESTION_MARKERS)
        else previous.aspects
        if previous is not None
        else ("conditions",)
    )
    product_category = _extract_product_category(text)
    opened = _extract_opened(text)
    return PolicyQuery(
        topic=topic,
        aspects=aspects,
        search_terms=(),
        product_category=(
            product_category
            if product_category is not None
            else previous.product_category
            if previous is not None
            else None
        ),
        opened=(
            opened
            if opened is not None
            else previous.opened
            if previous is not None
            else None
        ),
        region=(
            "overseas"
            if "海外" in text
            else previous.region
            if previous is not None
            else "CN"
        ),
        specific_order_eligibility=(
            has_order_id
            or (previous.specific_order_eligibility if previous is not None else False)
        ),
    )


class FakeQueryInterpreter:
    """使用固定规则解析订单与政策查询，并记录调用参数。"""

    def __init__(self) -> None:
        """初始化调用记录。"""

        self.calls: list[str] = []
        self.contexts: list[InterpretationContext | None] = []

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """根据受控规则提取写操作、政策或订单查询意图。"""

        self.calls.append(text)
        self.contexts.append(context)
        normalized_text = text.lower()
        match = ORDER_ID_PATTERN.search(text)
        order_id = match.group(0).upper() if match is not None else None
        if any(term in normalized_text for term in L2_SUPPORT_TERMS):
            return Interpretation(
                intent="l2_support_request",
                order_id=order_id,
                l2_issue_summary=text.strip()[:500],
            )
        if _is_refund_request(normalized_text, context):
            return Interpretation(
                intent="refund_request",
                order_id=order_id,
                refund_reason=_extract_refund_reason(normalized_text),
            )
        if _is_policy_question(normalized_text, context):
            return Interpretation(
                intent="policy_inquiry",
                order_id=order_id,
                policy_query=_build_policy_query(
                    normalized_text,
                    context,
                    has_order_id=order_id is not None,
                ),
            )
        if _is_unsupported_write(normalized_text):
            return Interpretation(intent="unsupported_write", order_id=order_id)
        if match is not None:
            return Interpretation(
                intent="order_inquiry",
                order_id=order_id,
            )
        if any(term in normalized_text for term in ORDER_INQUIRY_TERMS):
            return Interpretation(intent="order_inquiry")
        return Interpretation(intent="unknown")


class FakeOrderGateway:
    """从内存数据中查询属于指定用户的订单。"""

    def __init__(
        self,
        orders: Mapping[tuple[str, str], OrderView],
        *,
        temporarily_failed: bool = False,
    ) -> None:
        """复制订单数据，并配置是否模拟服务暂时失败。"""

        self._orders = dict(orders)
        self._temporarily_failed = temporarily_failed
        self.calls: list[tuple[str, str]] = []

    def get_order(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[OrderView]:
        """按可信作用域中的用户与订单号联合查询。"""

        self.calls.append((scope.user_id, order_id))
        if self._temporarily_failed:
            return ToolResult[OrderView](
                outcome="temporarily_failed",
                error_code="order_service_unavailable",
            )
        order = self._orders.get((scope.user_id, order_id))
        if order is None:
            return ToolResult[OrderView](
                outcome="unavailable",
                error_code="order_unavailable",
            )
        return ToolResult[OrderView](outcome="found", value=order)


class FakeLogisticsGateway:
    """从内存数据中查询已授权订单的物流信息。"""

    def __init__(
        self,
        shipments: Mapping[str, ShipmentView],
        *,
        temporarily_failed: bool = False,
    ) -> None:
        """复制物流数据，并配置是否模拟服务暂时失败。"""

        self._shipments = dict(shipments)
        self._temporarily_failed = temporarily_failed
        self.calls: list[str] = []

    def get_shipment(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[ShipmentView]:
        """按可信作用域和订单号返回物流结果并记录调用参数。"""

        del scope
        self.calls.append(order_id)
        if self._temporarily_failed:
            return ToolResult[ShipmentView](
                outcome="temporarily_failed",
                error_code="logistics_service_unavailable",
            )
        shipment = self._shipments.get(order_id)
        if shipment is None:
            return ToolResult[ShipmentView](
                outcome="unavailable",
                error_code="shipment_unavailable",
            )
        return ToolResult[ShipmentView](outcome="found", value=shipment)


def build_fake_dependencies(
    *,
    policy_repository: PolicyRepository | None = None,
) -> Dependencies:
    """构造订单、物流及可选政策查询所需的确定性演示依赖。"""

    order = OrderView(
        order_id="ORD-001",
        user_id="user-001",
        status="shipped",
    )
    shipment = ShipmentView(
        order_id="ORD-001",
        status="in_transit",
        last_event="包裹已离开上海转运中心",
        estimated_delivery_at=date(2026, 7, 18),
    )
    return Dependencies(
        interpreter=FakeQueryInterpreter(),
        order_gateway=FakeOrderGateway({("user-001", "ORD-001"): order}),
        logistics_gateway=FakeLogisticsGateway({"ORD-001": shipment}),
        policy_repository=policy_repository,
    )
