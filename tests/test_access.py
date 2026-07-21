"""验证服务端身份作用域和模型访问策略。"""

import pytest
from pydantic import ValidationError

from commerce_resolve.access import AccessPrincipal, LlmAccessPolicy


def test_principal_creates_a_server_controlled_business_scope() -> None:
    """验证 Gateway 作用域只来自已解析 Principal。"""

    principal = AccessPrincipal(
        actor_id="user-001",
        user_id="user-001",
        workspace_id="workspace-001",
        mode="registered",
        llm_allowed=True,
    )

    scope = principal.to_business_scope()

    assert scope.user_id == "user-001"
    assert scope.workspace_id == "workspace-001"
    assert scope.access_mode == "registered"


def test_principal_rejects_untrusted_extra_permission_fields() -> None:
    """验证客户端不能向 Principal 注入额外权限字段。"""

    with pytest.raises(ValidationError):
        AccessPrincipal.model_validate(
            {
                "actor_id": "guest",
                "user_id": None,
                "workspace_id": "demo",
                "mode": "guest",
                "llm_allowed": False,
                "interpreter": "openai",
            }
        )


@pytest.mark.parametrize(
    ("principal", "enabled", "configured", "quota", "error_code"),
    [
        (
            AccessPrincipal(
                actor_id="guest",
                user_id=None,
                workspace_id="demo",
                mode="guest",
                llm_allowed=False,
            ),
            True,
            True,
            True,
            "llm_not_authorized",
        ),
        (
            AccessPrincipal(
                actor_id="user-001",
                user_id="user-001",
                workspace_id="workspace-001",
                mode="registered",
                llm_allowed=False,
            ),
            False,
            True,
            True,
            "llm_disabled",
        ),
        (
            AccessPrincipal(
                actor_id="user-001",
                user_id="user-001",
                workspace_id="workspace-001",
                mode="registered",
                llm_allowed=False,
            ),
            True,
            False,
            True,
            "llm_not_configured",
        ),
        (
            AccessPrincipal(
                actor_id="user-001",
                user_id="user-001",
                workspace_id="workspace-001",
                mode="registered",
                llm_allowed=False,
            ),
            True,
            True,
            False,
            "llm_quota_exceeded",
        ),
    ],
)
def test_llm_policy_rejects_each_untrusted_or_unavailable_condition(
    principal: AccessPrincipal,
    enabled: bool,
    configured: bool,
    quota: bool,
    error_code: str,
) -> None:
    """验证模型权限按确定性优先级拒绝不满足条件的请求。"""

    decision = LlmAccessPolicy().decide(
        principal,
        feature_enabled=enabled,
        model_configured=configured,
        quota_available=quota,
    )

    assert decision.allowed is False
    assert decision.error_code == error_code


def test_llm_policy_allows_a_fully_authorized_registered_user() -> None:
    """验证注册身份、配置和配额全部有效时允许模型调用。"""

    principal = AccessPrincipal(
        actor_id="user-001",
        user_id="user-001",
        workspace_id="workspace-001",
        mode="registered",
        llm_allowed=True,
    )

    decision = LlmAccessPolicy().decide(
        principal,
        feature_enabled=True,
        model_configured=True,
        quota_available=True,
    )

    assert decision.allowed is True
    assert decision.error_code is None
