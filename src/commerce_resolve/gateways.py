"""工作流与外部能力之间的窄接口契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol

from commerce_resolve.access import BusinessScope
from commerce_resolve.business_models import (
    MockRefundRecord,
    RefundActionRecord,
)
from commerce_resolve.models import (
    Interpretation,
    InterpretationContext,
    OrderView,
    PolicyFact,
    PolicyQuery,
    PolicySearchResult,
    RefundContext,
    RefundExecutionResult,
    RefundPreview,
    RefundVerification,
    ShipmentView,
    ToolResult,
)

if TYPE_CHECKING:
    from commerce_resolve.l2_gateways import L2Dependencies

INTERPRETER_UNAVAILABLE_MESSAGE = "意图识别服务暂时不可用，请稍后重试。"


class InterpreterUnavailableError(RuntimeError):
    """表示意图解释器暂时无法返回可信的结构化结果。"""


class InterpreterOutputInvalidError(InterpreterUnavailableError):
    """表示 Provider 已响应，但内容无法通过意图 Schema 校验。"""


class PolicyRepositoryUnavailableError(RuntimeError):
    """表示政策索引缺失、过期或暂时无法读取。"""


class QueryInterpreter(Protocol):
    """定义将用户文本转换为结构化意图的能力。"""

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """返回经过校验的意图、订单号或政策查询。"""


class OrderGateway(Protocol):
    """定义按可信用户与工作区作用域读取订单的能力。"""

    def get_order(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[OrderView]:
        """仅在订单属于指定用户和工作区时返回订单。"""


class LogisticsGateway(Protocol):
    """定义在完整业务作用域中读取订单物流状态的能力。"""

    def get_shipment(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[ShipmentView]:
        """重新验证用户与工作区后返回物流数据。"""


class PolicyRepository(Protocol):
    """定义只读检索和解析当前有效政策事实的能力。"""

    def search(
        self,
        question: str,
        query: PolicyQuery,
        as_of: date,
        *,
        limit: int = 6,
    ) -> PolicySearchResult:
        """按问题、结构化条件与日期返回候选证据引用。"""

    def resolve_fact(
        self,
        fact_id: str,
        expected_hash: str,
    ) -> PolicyFact | None:
        """按事实标识和内容哈希重新解析当前政策事实。"""


class RefundGateway(Protocol):
    """定义退款上下文查询、动作保留、审批和 Mock 执行的窄契约。"""

    def get_refund_context(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[RefundContext]:
        """按可信作用域返回最新退款业务事实。"""

    def list_refunds(
        self,
        scope: BusinessScope,
        order_id: str,
    ) -> ToolResult[tuple[MockRefundRecord, ...]]:
        """按可信作用域返回指定订单的 Mock 退款只读结果。"""

    def reserve_preview(
        self,
        scope: BusinessScope,
        preview: RefundPreview,
    ) -> RefundActionRecord:
        """幂等保存待审批预览，不创建退款或修改支付。"""

    def get_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
    ) -> RefundActionRecord | None:
        """读取与当前任务和作用域绑定的退款动作。"""

    def get_refund_by_action(
        self,
        scope: BusinessScope,
        action_id: str,
    ) -> MockRefundRecord | None:
        """读取指定动作对应的 Mock 退款结果。"""

    def reject_action(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """幂等拒绝仍在等待审批且预览一致的动作。"""

    def mark_stale(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        preview_hash: str,
    ) -> RefundActionRecord:
        """把事实已变化的待审批动作标记为 stale。"""

    def execute_refund(
        self,
        scope: BusinessScope,
        task_id: str,
        action_id: str,
        expected_fingerprint: str,
    ) -> RefundExecutionResult:
        """按稳定动作和业务指纹幂等写入一笔 Mock 退款。"""

    def verify_refund(
        self,
        scope: BusinessScope,
        action_id: str,
    ) -> RefundVerification:
        """从业务事实重新读取并验证退款关联、金额和支付状态。"""


@dataclass(frozen=True)
class Dependencies:
    """集中保存构建工作流所需的外部能力。"""

    interpreter: QueryInterpreter
    order_gateway: OrderGateway
    logistics_gateway: LogisticsGateway
    policy_repository: PolicyRepository | None = None
    refund_gateway: RefundGateway | None = None
    l2: L2Dependencies | None = None
