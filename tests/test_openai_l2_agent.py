"""验证 OpenAI-compatible L2 Adapter 的结构化输出和安全失败。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from openai import OpenAI, OpenAIError

from commerce_resolve.adapters.openai_l2_agent import (
    API_KEY_ENV_NAME,
    BASE_URL_ENV_NAME,
    L2_INSTRUCTIONS,
    L2_MAX_OUTPUT_TOKENS,
    L2_TIMEOUT_SECONDS,
    MODEL_ENV_NAME,
    OpenAIL2Agent,
)
from commerce_resolve.l2_gateways import (
    L2ModelOutputInvalidError,
    L2ModelUnavailableError,
)
from commerce_resolve.l2_models import L2ContextPack, L2ModelRequest


def _request() -> L2ModelRequest:
    """返回不含身份和内部对象的最小模型请求。"""

    return L2ModelRequest(
        case_id="case-001",
        step_id="step-001",
        context_policy_version="v0.7.0",
        context=L2ContextPack(
            issue_summary="订单物流状态不一致",
            related_order_id="ORD-001",
            allowed_tools=("get_order", "get_shipment"),
            remaining_steps=8,
            remaining_model_calls=6,
            remaining_tool_calls=8,
            remaining_estimated_tokens=30_000,
        ),
    )


def test_openai_l2_agent_returns_validated_decision_and_provider_usage() -> None:
    """验证 Chat JSON Output 会被解析为严格决策并保留 Provider 用量。"""

    content = '{"kind":"tool_call","call":{"tool":"get_order","order_id":"ORD-001"}}'
    create = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=12),
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    agent = OpenAIL2Agent(client, "test-model")

    result = agent.decide(_request())

    assert result.decision.kind == "tool_call"
    assert result.usage.total_tokens == 52
    assert result.usage.estimated is False
    call = create.call_args.kwargs
    assert call["messages"][0] == {"role": "system", "content": L2_INSTRUCTIONS}
    assert call["max_tokens"] == L2_MAX_OUTPUT_TOKENS
    assert "user_id" not in call["messages"][1]["content"]
    assert "禁止增加 parameters" in L2_INSTRUCTIONS
    assert "不得重复调用该工具" in L2_INSTRUCTIONS
    assert '"kind":"answer","answer"' in L2_INSTRUCTIONS
    assert '"aspects":["conditions"]' in L2_INSTRUCTIONS
    assert "禁止输出 eligibility 等其他值" in L2_INSTRUCTIONS
    assert "“普通商品”\n  映射为 general" in L2_INSTRUCTIONS


@pytest.mark.parametrize(
    "content",
    [
        '{"kind":"tool_call","call":{"tool":"run_sql"}}',
        '{"kind":"answer","answer":"完成","approved":true}',
        "not-json",
    ],
)
def test_openai_l2_agent_rejects_invalid_or_unknown_actions(content: str) -> None:
    """验证未知工具、额外授权字段和非法 JSON 都不能进入 Graph。"""

    create = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=None,
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )

    with pytest.raises(L2ModelOutputInvalidError, match="output is invalid"):
        OpenAIL2Agent(client, "test-model").decide(_request())


def test_openai_l2_agent_hides_sdk_error_details() -> None:
    """验证 Provider 异常不会把上游内部信息暴露给用户。"""

    create = Mock(side_effect=OpenAIError("private provider detail"))
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )

    with pytest.raises(L2ModelUnavailableError) as error:
        OpenAIL2Agent(client, "test-model").decide(_request())

    assert "private provider detail" not in str(error.value)


def test_openai_l2_agent_uses_shared_environment_configuration(monkeypatch) -> None:
    """验证 L2 Adapter 复用通用模型环境变量且关闭 SDK 自动重试。"""

    from commerce_resolve.adapters import openai_l2_agent as module

    client = cast(OpenAI, SimpleNamespace())
    factory = Mock(return_value=client)
    monkeypatch.setenv(API_KEY_ENV_NAME, "test-key")
    monkeypatch.setenv(MODEL_ENV_NAME, "test-model")
    monkeypatch.setenv(BASE_URL_ENV_NAME, "https://llm.example.com")
    monkeypatch.setattr(module, "OpenAI", factory)

    adapter = OpenAIL2Agent.from_env()

    assert adapter.model_name == "test-model"
    factory.assert_called_once_with(
        api_key="test-key",
        base_url="https://llm.example.com",
        timeout=L2_TIMEOUT_SECONDS,
        max_retries=0,
    )
