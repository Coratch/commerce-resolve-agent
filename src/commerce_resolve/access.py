"""定义 Web 身份、工作区作用域和模型访问决策。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

AccessMode = Literal["cli", "guest", "registered"]


class BusinessScope(BaseModel):
    """携带由服务端生成的业务数据访问作用域。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    workspace_id: str
    access_mode: AccessMode


class AccessPrincipal(BaseModel):
    """表示从服务端 Session 解析出的可信调用身份。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str
    user_id: str | None
    workspace_id: str
    mode: Literal["guest", "registered"]
    llm_allowed: bool
    role: Literal["customer", "admin"] | None = None

    def to_business_scope(self) -> BusinessScope:
        """转换为 Gateway 使用且不含认证凭证的业务作用域。"""

        return BusinessScope(
            user_id=self.actor_id,
            workspace_id=self.workspace_id,
            access_mode=self.mode,
        )


class LlmAccessDecision(BaseModel):
    """保存确定性模型访问判断及公开拒绝原因。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    error_code: str | None = None


class LlmAccessPolicy:
    """根据可信身份、配置和配额决定是否允许模型调用。"""

    def decide(
        self,
        principal: AccessPrincipal,
        *,
        feature_enabled: bool,
        model_configured: bool,
        quota_available: bool,
    ) -> LlmAccessDecision:
        """按固定优先级返回模型授权结果，不接受客户端自报权限。"""

        if principal.mode != "registered" or principal.user_id is None:
            return LlmAccessDecision(allowed=False, error_code="llm_not_authorized")
        if not feature_enabled:
            return LlmAccessDecision(allowed=False, error_code="llm_disabled")
        if not model_configured:
            return LlmAccessDecision(allowed=False, error_code="llm_not_configured")
        if not quota_available:
            return LlmAccessDecision(allowed=False, error_code="llm_quota_exceeded")
        return LlmAccessDecision(allowed=True)
