"""定义 L2 Agent Model 与 Harness 外部能力之间的窄接口。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Protocol

from commerce_resolve.access import BusinessScope
from commerce_resolve.l2_models import (
    L2CaseCreate,
    L2CaseMetrics,
    L2CaseRecord,
    L2CaseTransition,
    L2ContextManifest,
    L2ContextPublicMessage,
    L2ModelCallRecord,
    L2ModelCallStart,
    L2ModelRequest,
    L2ModelResult,
    L2Observation,
    L2ObservationRefreshResult,
    L2PublicTraceEvent,
    L2UsageSource,
)

if TYPE_CHECKING:
    from commerce_resolve.l2_tools import L2ToolRegistry

L2_MODEL_UNAVAILABLE_MESSAGE = "AI 深度处理暂时不可用，请稍后重试。"


class L2ModelUnavailableError(RuntimeError):
    """表示 L2 模型无法返回可信结构化决策。"""


class L2ModelOutputInvalidError(RuntimeError):
    """表示 Provider 已响应，但内容无法通过 L2Decision Schema。"""


class L2AgentModel(Protocol):
    """定义根据受限上下文产生一个结构化 L2 决策的能力。"""

    @property
    def model_name(self) -> str:
        """返回用于计量与公开审计的服务端模型名称。"""

    def decide(self, request: L2ModelRequest) -> L2ModelResult:
        """返回经过 Schema 校验的单步决策与模型用量。"""


class L2ConversationContextReader(Protocol):
    """定义在完整身份作用域内读取公开消息候选的能力。"""

    def list_authorized_context_messages(
        self,
        *,
        thread_id: str,
        subject_id: str,
        user_id: str,
        workspace_id: str,
        limit: int,
    ) -> tuple[L2ContextPublicMessage, ...]:
        """校验会话归属后返回最近的公开消息候选。"""


class L2FreshnessReader(Protocol):
    """定义在模型调用前重新验证既有 Observation 来源的能力。"""

    def refresh(
        self,
        observation: L2Observation,
        *,
        scope: BusinessScope,
        as_of: date,
        step_id: str,
        now: datetime,
    ) -> L2ObservationRefreshResult:
        """返回 fresh 替代 Observation，或明确 stale/unknown 状态。"""


class L2CaseRepository(Protocol):
    """定义 L2 图所需的 Case、公开轨迹和模型计量能力。"""

    def create_case_if_absent(self, data: L2CaseCreate) -> L2CaseRecord:
        """幂等创建与可信 thread 绑定的活动 Case。"""

    def get_active_case_for_thread(
        self,
        *,
        thread_id: str,
        subject_id: str,
        user_id: str,
        workspace_id: str,
    ) -> L2CaseRecord | None:
        """读取可信作用域中的活动或等待 Case。"""

    def transition_case(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
        transition: L2CaseTransition,
    ) -> L2CaseRecord:
        """按期望状态幂等迁移 Case 并保存预算。"""

    def append_event_once(
        self,
        *,
        user_id: str,
        workspace_id: str,
        event: L2PublicTraceEvent,
    ) -> L2PublicTraceEvent:
        """按稳定事件键幂等保存公开轨迹。"""

    def save_manifest_once(
        self,
        *,
        user_id: str,
        workspace_id: str,
        manifest: L2ContextManifest,
    ) -> L2ContextManifest:
        """在模型调用前幂等保存脱敏 Context Manifest。"""

    def begin_model_call(
        self,
        *,
        data: L2ModelCallStart,
        usage_date: date,
        daily_limit: int,
    ) -> L2ModelCallRecord | None:
        """原子占用每日额度和 Case 模型预算。"""

    def finish_model_call(
        self,
        *,
        call_id: str,
        user_id: str,
        case_id: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        usage_source: L2UsageSource = "unknown",
    ) -> L2ModelCallRecord | None:
        """幂等记录一次 Provider 尝试的最终状态和用量。"""

    def list_events(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[L2PublicTraceEvent, ...]:
        """按 Case 内单调序号只读分页公开 Trace。"""

    def get_case_metrics(
        self,
        *,
        case_id: str,
        user_id: str,
        workspace_id: str,
    ) -> L2CaseMetrics | None:
        """只读聚合当前账号 Case 的公开指标。"""


def utc_now() -> datetime:
    """返回可注入 L2 Harness 的当前 UTC 时间。"""

    return datetime.now(UTC)


@dataclass(frozen=True)
class L2Dependencies:
    """集中保存 L2 图使用且不会进入持久 State 的外部能力。"""

    agent_model: L2AgentModel
    case_repository: L2CaseRepository
    tool_registry: L2ToolRegistry
    conversation_reader: L2ConversationContextReader | None = None
    freshness_reader: L2FreshnessReader | None = None
    daily_call_limit: int = 20
    prompt_version: str = "v0.7.0"
    toolset_version: str = "v0.7.0"
    clock: Callable[[], datetime] = utc_now
