"""验证 OpenAI 意图解释器的结构化输出和安全失败契约。"""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from openai import OpenAI, OpenAIError

from commerce_resolve.adapters.openai_interpreter import (
    API_KEY_ENV_NAME,
    BASE_URL_ENV_NAME,
    INTERPRETER_INSTRUCTIONS,
    MAX_OUTPUT_TOKENS,
    MODEL_ENV_NAME,
    OPENAI_MAX_RETRIES,
    OPENAI_TIMEOUT_SECONDS,
    OpenAIQueryInterpreter,
)
from commerce_resolve.gateways import (
    InterpreterOutputInvalidError,
    InterpreterUnavailableError,
)
from commerce_resolve.models import Interpretation, InterpretationContext, PolicyQuery


def test_openai_interpreter_returns_validated_structured_output() -> None:
    """验证 Chat JSON Output 最终仍由 Pydantic Schema 校验。"""

    expected = Interpretation(intent="order_inquiry", order_id="ORD-001")
    create = Mock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=expected.model_dump_json())
                )
            ]
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    actual = interpreter.interpret("查询订单 ORD-001 的物流")

    assert actual == expected
    create.assert_called_once_with(
        model="test-model",
        messages=[
            {"role": "system", "content": INTERPRETER_INSTRUCTIONS},
            {
                "role": "user",
                "content": (
                    '{"text":"查询订单 ORD-001 的物流",'
                    '"previous_policy_query":null,'
                    '"pending_refund_request":false,'
                    '"pending_intent_clarification":false}'
                ),
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=MAX_OUTPUT_TOKENS,
        stream=False,
    )
    assert "未收到货、物流丢失或配送异常时使用 delivery_issue" in (
        INTERPRETER_INSTRUCTIONS
    )
    assert "“普通商品”映射为\n  general" in INTERPRETER_INSTRUCTIONS
    assert "退款资格问题必须在 policy_query.aspects 中包含 conditions" in (
        INTERPRETER_INSTRUCTIONS
    )
    assert "“退库”等可能同时指向退货或退款" in INTERPRETER_INSTRUCTIONS


def test_openai_interpreter_handles_explicit_l2_command_without_provider() -> None:
    """验证明确转接命令不受模型波动影响且只生成 AI 二线候选意图。"""

    create = Mock()
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    actual = interpreter.interpret("转人工。")

    assert actual.intent == "l2_support_request"
    assert actual.order_id is None
    assert actual.l2_issue_summary == (
        "用户请求进入 AI 深度处理，尚未提供具体售后问题。"
    )
    create.assert_not_called()


def test_ambiguous_after_sales_action_returns_unknown_without_provider() -> None:
    """验证已知歧义售后词直接返回 unknown，不让模型猜测退款或退货。"""

    create = Mock()
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    actual = interpreter.interpret("商品质量不行，我要退库呐")

    assert actual == Interpretation(intent="unknown")
    create.assert_not_called()


def test_openai_interpreter_returns_a_restricted_policy_query() -> None:
    """验证真实解释器只能返回受 Pydantic 枚举约束的政策查询。"""

    expected = Interpretation(
        intent="policy_inquiry",
        policy_query=PolicyQuery(
            topic="return",
            aspects=("conditions",),
            search_terms=("拆封退货",),
            opened=True,
        ),
    )
    create = Mock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=expected.model_dump_json())
                )
            ]
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    actual = interpreter.interpret("拆封后还能退货吗？")

    assert actual == expected
    assert actual.policy_query is not None
    assert actual.policy_query.topic == "return"
    assert actual.policy_query.opened is True


def test_openai_interpreter_sends_only_minimal_pending_policy_context() -> None:
    """验证补充轮只发送结构化 PolicyQuery，不发送消息历史或隐藏 State。"""

    previous_query = PolicyQuery(
        topic="return",
        aspects=("conditions",),
        opened=True,
    )
    expected = Interpretation(
        intent="policy_inquiry",
        policy_query=previous_query.model_copy(update={"product_category": "apparel"}),
    )
    create = Mock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=expected.model_dump_json())
                )
            ]
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    actual = interpreter.interpret(
        "普通服饰",
        InterpretationContext(
            previous_policy_query=previous_query,
            pending_intent_clarification=True,
        ),
    )

    payload = json.loads(create.call_args.kwargs["messages"][1]["content"])
    assert actual == expected
    assert payload == {
        "text": "普通服饰",
        "previous_policy_query": previous_query.model_dump(mode="json"),
        "pending_refund_request": False,
        "pending_intent_clarification": True,
    }
    assert "messages" not in payload
    assert "owner_user_id" not in payload


def test_openai_interpreter_rejects_missing_parsed_output() -> None:
    """验证 Chat 空响应不会进入工作流 State。"""

    create = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    with pytest.raises(InterpreterUnavailableError, match="暂时不可用"):
        interpreter.interpret("查询订单 ORD-001")


def test_openai_interpreter_rejects_json_that_violates_schema() -> None:
    """验证有效 JSON 但无效意图仍被 Pydantic 拒绝。"""

    create = Mock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"intent":"invented","order_id":"ORD-001"}'
                    )
                )
            ]
        )
    )
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    with pytest.raises(InterpreterOutputInvalidError, match="暂时不可用"):
        interpreter.interpret("查询订单 ORD-001")


def test_openai_interpreter_hides_sdk_error_details() -> None:
    """验证 SDK 异常被转换为不包含上游细节的领域错误。"""

    create = Mock(side_effect=OpenAIError("private upstream detail"))
    client = cast(
        OpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    interpreter = OpenAIQueryInterpreter(client=client, model="test-model")

    with pytest.raises(InterpreterUnavailableError) as error:
        interpreter.interpret("查询订单 ORD-001")

    assert "暂时不可用" in str(error.value)
    assert "private upstream detail" not in str(error.value)


def test_openai_interpreter_configures_chat_client_from_environment(
    monkeypatch,
) -> None:
    """验证通用环境变量被用于创建 OpenAI-compatible Chat 客户端。"""

    from commerce_resolve.adapters import openai_interpreter as module

    client = cast(OpenAI, SimpleNamespace())
    client_factory = Mock(return_value=client)
    monkeypatch.setenv(API_KEY_ENV_NAME, "test-key")
    monkeypatch.setenv(MODEL_ENV_NAME, "test-model")
    monkeypatch.setenv(BASE_URL_ENV_NAME, "https://llm.example.com")
    monkeypatch.setattr(module, "OpenAI", client_factory)

    interpreter = OpenAIQueryInterpreter.from_env()

    assert isinstance(interpreter, OpenAIQueryInterpreter)
    client_factory.assert_called_once_with(
        api_key="test-key",
        base_url="https://llm.example.com",
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=OPENAI_MAX_RETRIES,
    )


@pytest.mark.parametrize(
    "missing_name",
    [API_KEY_ENV_NAME, MODEL_ENV_NAME, BASE_URL_ENV_NAME],
)
def test_openai_interpreter_requires_explicit_environment_config(
    monkeypatch,
    missing_name: str,
) -> None:
    """验证真实解释器不会使用代码内密钥或默认模型。"""

    monkeypatch.setenv(API_KEY_ENV_NAME, "test-key")
    monkeypatch.setenv(MODEL_ENV_NAME, "test-model")
    monkeypatch.setenv(BASE_URL_ENV_NAME, "https://llm.example.com")
    monkeypatch.delenv(missing_name)

    with pytest.raises(ValueError, match=missing_name):
        OpenAIQueryInterpreter.from_env()
