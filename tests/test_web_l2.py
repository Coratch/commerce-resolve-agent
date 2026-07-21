"""验证 v0.5-v0.7 Web 升级、Agent Loop、公开 Trace 与退款桥接。"""

from fastapi.testclient import TestClient

from commerce_resolve.adapters.fake_l2_agent import ScriptedL2Agent
from commerce_resolve.l2_models import (
    AnswerDecision,
    AskUserDecision,
    GetOrderCall,
    GetShipmentCall,
    ProposeMemoryDecision,
    ProposeRefundDecision,
    StopDecision,
    ToolCallDecision,
)
from commerce_resolve.models import RefundReason
from commerce_resolve.web.app import create_app
from tests.conftest import ORIGIN, PASSWORD, WebHarness


def _private_order(
    harness: WebHarness,
    headers: dict[str, str],
    order_id: str,
    *,
    with_payment: bool = False,
) -> None:
    """创建当前账号的私有订单，并按需增加可退款 Mock 支付。"""

    created = harness.client.post(
        "/api/orders",
        headers=headers,
        json={
            "order_id": order_id,
            "status": "processing" if with_payment else "shipped",
            "shipment": {
                "status": "preparing" if with_payment else "in_transit",
                "last_event": "等待揽收" if with_payment else "到达本地转运中心",
            },
        },
    )
    assert created.status_code == 201
    if with_payment:
        payment = harness.client.put(
            f"/api/orders/{order_id}/payment",
            headers=headers,
            json={
                "amount": "88.00",
                "currency": "CNY",
                "channel": "mock_card",
                "status": "settled",
            },
        )
        assert payment.status_code == 200


def _conversation(harness: WebHarness, headers: dict[str, str]) -> str:
    """创建当前账号有权访问的新 conversation。"""

    response = harness.client.post("/api/conversations", headers=headers)
    assert response.status_code == 201
    return str(response.json()["thread_id"])


def _request_upgrade(
    harness: WebHarness,
    headers: dict[str, str],
    thread_id: str,
    order_id: str,
) -> dict[str, object]:
    """通过一线 Interpreter 请求 L2 升级并返回公开预览。"""

    response = harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={
            "thread_id": thread_id,
            "message": f"请升级二线客服处理 {order_id} 的复杂售后问题",
        },
    )
    assert response.status_code == 200
    assert response.json()["public_status"] == "l2_awaiting_confirmation"
    return response.json()["l2_upgrade_preview"]


def _confirm_upgrade(
    harness: WebHarness,
    headers: dict[str, str],
    thread_id: str,
    preview_id: str,
):
    """提交升级确认并返回 202 Run 与后台完成后的公开响应。"""

    accepted = harness.client.post(
        f"/api/conversations/{thread_id}/l2-upgrade-decision",
        headers=headers,
        json={"preview_id": preview_id, "decision": "confirm"},
    )
    assert accepted.status_code == 202
    return accepted, harness.latest_public_response(thread_id)


def test_upgrade_cancel_has_zero_case_and_model_side_effects(
    web_harness: WebHarness,
) -> None:
    """验证升级前披露 AI 身份，取消不会创建 Case 或调用 L2 模型。"""

    session = web_harness.register_and_login("l2.cancel")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    thread_id = _conversation(web_harness, headers)
    preview = _request_upgrade(web_harness, headers, thread_id, "ORD-NOT-NEEDED")
    repository = web_harness.services.require_l2_repository()

    pending = web_harness.client.get(f"/api/conversations/{thread_id}/pending-l2")
    cancelled = web_harness.client.post(
        f"/api/conversations/{thread_id}/l2-upgrade-decision",
        headers=headers,
        json={"preview_id": preview["preview_id"], "decision": "cancel"},
    )
    result = web_harness.latest_public_response(thread_id)

    assert preview["agent_identity"] == "AI 二线客服，并非真人"
    assert pending.json()["pending_action"] == "upgrade_confirmation"
    assert repository.count_cases() == repository.count_model_calls() == 0
    assert cancelled.status_code == 202
    assert result["public_status"] == "l2_cancelled"
    assert repository.count_cases() == repository.count_model_calls() == 0


def test_confirmed_loop_uses_two_tools_and_exposes_authorized_trace(
    web_harness: WebHarness,
) -> None:
    """验证确认后使用订单和物流两类证据，并通过 Case API 查看轨迹。"""

    session = web_harness.register_and_login("l2.trace")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    order_id = "ORD-L2-TRACE"
    _private_order(web_harness, headers, order_id)
    thread_id = _conversation(web_harness, headers)
    agent = ScriptedL2Agent(
        (
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id=order_id),
            ),
            ToolCallDecision(
                kind="tool_call",
                call=GetShipmentCall(tool="get_shipment", order_id=order_id),
            ),
            AnswerDecision(
                kind="answer",
                answer="订单正在处理，物流仍在运输中。",
                evidence_ids=(
                    f"order:{order_id}:shipped",
                    f"shipment:{order_id}:in_transit",
                ),
            ),
        )
    )
    web_harness.services.l2_agent_factory = lambda: agent
    preview = _request_upgrade(web_harness, headers, thread_id, order_id)

    accepted, result = _confirm_upgrade(
        web_harness,
        headers,
        thread_id,
        str(preview["preview_id"]),
    )
    case_id = result["l2_case_summary"]["case_id"]
    cases = web_harness.client.get(f"/api/l2-cases?thread_id={thread_id}")
    detail = web_harness.client.get(f"/api/l2-cases/{case_id}")
    first_page = web_harness.client.get(
        f"/api/l2-cases/{case_id}/trace?after_sequence=0&limit=2"
    )
    first_payload = first_page.json()
    second_page = web_harness.client.get(
        f"/api/l2-cases/{case_id}/trace"
        f"?after_sequence={first_payload['next_after_sequence']}&limit=100"
    )
    repeated_page = web_harness.client.get(
        f"/api/l2-cases/{case_id}/trace?after_sequence=0&limit=2"
    )
    repository = web_harness.services.require_l2_repository()
    counts_before_cross_read = (
        repository.count_cases(),
        repository.count_events(),
        repository.count_model_calls(),
        repository.count_manifests(),
    )

    relogin_client = TestClient(
        create_app(services=web_harness.services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    try:
        guest = relogin_client.get("/api/session").json()
        relogin = relogin_client.post(
            "/api/auth/login",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": guest["csrf_token"],
            },
            json={"username": "l2.trace", "password": PASSWORD},
        )
        relogin_page = relogin_client.get(
            f"/api/l2-cases/{case_id}/trace?after_sequence=0&limit=2"
        )
    finally:
        relogin_client.close()

    client_b = TestClient(
        create_app(services=web_harness.services, mount_spa=False),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    original_client = web_harness.client
    web_harness.client = client_b
    try:
        web_harness.register_and_login("l2.trace.attacker")
        cross_account = client_b.get(f"/api/l2-cases/{case_id}/trace")
    finally:
        web_harness.client = original_client
        client_b.close()

    combined_events = first_payload["events"] + second_page.json()["events"]
    sequences = [event["sequence_no"] for event in combined_events]

    assert accepted.status_code == 202
    assert result["public_status"] == "l2_resolved"
    assert {event.get("tool_category") for event in result["l2_trace_events"]} >= {
        "get_order",
        "get_shipment",
    }
    assert cases.status_code == 200
    assert cases.json()["cases"][0]["case_id"] == case_id
    assert detail.json()["case"]["status"] == "l2_resolved"
    assert detail.json()["case"]["trace_state"] == "complete"
    assert detail.json()["metrics"]["candidate_count"] >= 1
    assert first_page.status_code == second_page.status_code == 200
    assert first_payload["has_more"] is True
    assert sequences == sorted(set(sequences))
    assert repeated_page.json() == first_payload
    assert relogin.status_code == 200
    assert relogin_page.json() == first_payload
    assert cross_account.status_code == 404
    assert counts_before_cross_read == (
        repository.count_cases(),
        repository.count_events(),
        repository.count_model_calls(),
        repository.count_manifests(),
    )
    assert "prompt" not in detail.text.lower()
    assert "content" not in detail.text.lower()


def test_waiting_user_message_resumes_l2_without_one_line_interpreter(
    web_harness: WebHarness,
) -> None:
    """验证补充消息直接恢复 L2 interrupt，不重复一线意图识别。"""

    session = web_harness.register_and_login("l2.ask")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    order_id = "ORD-L2-ASK"
    _private_order(web_harness, headers, order_id)
    thread_id = _conversation(web_harness, headers)
    agent = ScriptedL2Agent(
        (
            AskUserDecision(
                kind="ask_user",
                question="请确认订单号。",
                expected_field="order_id",
            ),
            ToolCallDecision(
                kind="tool_call",
                call=GetOrderCall(tool="get_order", order_id=order_id),
            ),
            AnswerDecision(
                kind="answer",
                answer="已核对订单状态。",
                evidence_ids=(f"order:{order_id}:shipped",),
            ),
        )
    )
    web_harness.services.l2_agent_factory = lambda: agent
    preview = _request_upgrade(web_harness, headers, thread_id, order_id)
    _, waiting = _confirm_upgrade(
        web_harness,
        headers,
        thread_id,
        str(preview["preview_id"]),
    )
    calls_before = len(web_harness.interpreter.calls)

    resumed = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": f"订单号是 {order_id}"},
    )

    assert waiting["l2_pending_action"] == "user_input"
    assert resumed.status_code == 200
    assert resumed.json()["public_status"] == "l2_resolved"
    assert len(web_harness.interpreter.calls) == calls_before


def test_memory_requires_confirmation_and_supports_scoped_crud(
    web_harness: WebHarness,
) -> None:
    """验证偏好未经确认不写入，确认后可查看、纠正和删除。"""

    session = web_harness.register_and_login("l2.memory")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    thread_id = _conversation(web_harness, headers)
    agent = ScriptedL2Agent(
        (
            ProposeMemoryDecision(
                kind="propose_memory",
                memory_type="preferred_language",
                value="zh-CN",
                purpose="后续客服使用该语言回复",
            ),
            StopDecision(
                kind="stop",
                reason="unsupported",
                public_message="偏好确认已处理，本次 Case 结束。",
            ),
        )
    )
    web_harness.services.l2_agent_factory = lambda: agent
    preview = _request_upgrade(web_harness, headers, thread_id, "ORD-MEMORY")
    _, waiting = _confirm_upgrade(
        web_harness,
        headers,
        thread_id,
        str(preview["preview_id"]),
    )

    before = web_harness.client.get("/api/memories")
    proposal = waiting["memory_proposal"]
    confirmed = web_harness.client.post(
        f"/api/conversations/{thread_id}/l2-memory-decision",
        headers=headers,
        json={"proposal_id": proposal["proposal_id"], "decision": "confirm"},
    )
    listed = web_harness.client.get("/api/memories")
    memory_id = listed.json()["memories"][0]["memory_id"]
    corrected = web_harness.client.patch(
        f"/api/memories/{memory_id}",
        headers=headers,
        json={"value": "en"},
    )
    deleted = web_harness.client.delete(
        f"/api/memories/{memory_id}",
        headers=headers,
    )

    assert before.json()["memories"] == []
    assert waiting["l2_pending_action"] == "memory_confirmation"
    assert confirmed.status_code == 202
    assert listed.json()["memories"][0]["value"] == "zh-CN"
    assert corrected.json()["value"] == "en"
    assert deleted.json() == {"deleted": True}
    assert web_harness.client.get("/api/memories").json()["memories"] == []


def test_l2_refund_candidate_reuses_existing_approval_and_is_idempotent(
    web_harness: WebHarness,
) -> None:
    """验证 L2 只能提出退款候选，批准仍由 v0.4 链创建唯一 Mock 退款。"""

    session = web_harness.register_and_login("l2.refund")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    order_id = "ORD-L2-REFUND"
    _private_order(web_harness, headers, order_id, with_payment=True)
    thread_id = _conversation(web_harness, headers)
    agent = ScriptedL2Agent(
        (
            ProposeRefundDecision(
                kind="propose_refund",
                order_id=order_id,
                reason=RefundReason(code="quality_issue"),
            ),
            StopDecision(
                kind="stop",
                reason="unsupported",
                public_message="退款结果已记录，本次 Case 结束。",
            ),
        )
    )
    web_harness.services.l2_agent_factory = lambda: agent
    preview = _request_upgrade(web_harness, headers, thread_id, order_id)
    _, waiting = _confirm_upgrade(
        web_harness,
        headers,
        thread_id,
        str(preview["preview_id"]),
    )
    refund_preview = waiting["refund_preview"]

    approved = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json={
            "action_id": refund_preview["action_id"],
            "decision": "approve",
        },
    )
    repeated = web_harness.client.post(
        f"/api/conversations/{thread_id}/refund-approval",
        headers=headers,
        json={
            "action_id": refund_preview["action_id"],
            "decision": "approve",
        },
    )
    result = web_harness.latest_public_response(thread_id)

    assert waiting["l2_pending_action"] == "refund_approval"
    assert approved.status_code == repeated.status_code == 202
    assert result["public_status"] == "l2_unresolved"
    assert repeated.json()["reused"] is True
    assert web_harness.services.require_refund_repository().count_refunds() == 1
