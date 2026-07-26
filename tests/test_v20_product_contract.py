"""验证 v2.0 邀请制演示工作区、订单任务和重置核心契约。"""

import re
from pathlib import Path

import pytest

from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.auth import AuthDomainError
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.l2_memory import (
    confirm_preference,
    list_preferences,
    open_sqlite_memory_store,
)
from commerce_resolve.l2_models import MemoryProposal
from commerce_resolve.portfolio_demo import PortfolioDemoService
from tests.conftest import PASSWORD, WebHarness


def _portfolio_orders(web_harness: WebHarness) -> list[dict[str, object]]:
    """读取当前注册账号的三笔版本化演示订单。"""

    response = web_harness.client.get("/api/support/orders")
    assert response.status_code == 200
    return response.json()["orders"]


def test_anonymous_access_has_no_cookie_business_or_model_calls(
    web_harness: WebHarness,
) -> None:
    """验证匿名访问只有公开状态，不能建立业务身份或调用模型。"""

    session = web_harness.client.get("/api/session")
    support = web_harness.client.get("/api/support/overview")
    conversation = web_harness.client.post(
        "/api/conversations",
        headers={"Origin": "http://testserver"},
        json={"related_order_id": "CR-2345-6789"},
    )

    assert session.json()["mode"] == "anonymous"
    assert session.json()["csrf_token"] is None
    assert web_harness.services.settings.cookie_name not in web_harness.client.cookies
    assert support.status_code == conversation.status_code == 401
    assert web_harness.factory.calls == 0


def test_registration_atomically_creates_isolated_portfolio_workspace(
    web_harness: WebHarness,
) -> None:
    """验证邀请注册后每个账号拥有三笔不透明且隔离的基准订单。"""

    first = web_harness.register_and_login("portfolio.one")
    first_orders = _portfolio_orders(web_harness)
    web_harness.client.post(
        "/api/auth/logout",
        headers=web_harness.mutation_headers(str(first["csrf_token"])),
    )
    web_harness.register_and_login("portfolio.two")
    second_orders = _portfolio_orders(web_harness)

    first_ids = {str(item["order_id"]) for item in first_orders}
    second_ids = {str(item["order_id"]) for item in second_orders}
    assert len(first_ids) == len(second_ids) == 3
    assert all(
        re.fullmatch(
            r"CR-[23456789A-HJ-NP-Z]{4}-[23456789A-HJ-NP-Z]{4}",
            value,
        )
        for value in first_ids | second_ids
    )
    assert first_ids.isdisjoint(second_ids)
    assert (
        web_harness.client.get(
            f"/api/support/orders/{next(iter(first_ids))}"
        ).status_code
        == 404
    )


def test_registration_seed_failure_rolls_back_invitation_and_account(
    tmp_path: Path,
) -> None:
    """验证演示数据不可用时账号、工作区和邀请码消费全部回滚。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    repository = SqliteBusinessRepository(
        engine,
        portfolio_service=PortfolioDemoService(project_root=tmp_path),
    )
    invitation = repository.create_invitation()

    with pytest.raises(AuthDomainError, match="registration_initialization_failed"):
        repository.register(
            username="broken.portfolio",
            password=PASSWORD,
            invitation_code=invitation.code,
        )

    assert repository.count_users() == 0
    assert repository.invitation_usage(invitation.id) == 0
    engine.dispose()


def test_order_conversation_is_reused_and_cannot_mix_orders(
    web_harness: WebHarness,
) -> None:
    """验证同订单恢复唯一活动任务，另一个订单使用独立 Thread。"""

    session = web_harness.register_and_login("thread.owner")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    order_ids = [str(item["order_id"]) for item in _portfolio_orders(web_harness)]

    first = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": order_ids[0]},
    )
    resumed = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": order_ids[0]},
    )
    other = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": order_ids[1]},
    )

    assert first.status_code == resumed.status_code == other.status_code == 201
    assert first.json()["created"] is True
    assert resumed.json()["created"] is False
    assert first.json()["thread_id"] == resumed.json()["thread_id"]
    assert other.json()["thread_id"] != first.json()["thread_id"]

    mismatch = web_harness.client.post(
        f"/api/conversations/{first.json()['thread_id']}/messages",
        headers=headers,
        json={
            "client_message_id": "v20-order-mismatch",
            "message": f"请查询 {order_ids[1]}",
        },
    )
    assert mismatch.status_code == 202
    messages = web_harness.client.get(
        f"/api/conversations/{first.json()['thread_id']}/messages"
    ).json()["messages"]
    assert "当前对话已绑定其他订单" in messages[-1]["content"]


def test_workspace_reset_preserves_order_ids_and_clears_derived_state(
    web_harness: WebHarness,
) -> None:
    """验证完整重置保留公开订单号，并清除会话、Checkpoint 与长期偏好。"""

    session = web_harness.register_and_login("reset.owner")
    headers = web_harness.mutation_headers(str(session["csrf_token"]))
    target = next(
        item
        for item in SqliteAdminRepository(
            web_harness.repository.engine
        ).list_customers()
        if item.username == "reset.owner"
    )
    order_ids = tuple(
        sorted(str(item["order_id"]) for item in _portfolio_orders(web_harness))
    )
    conversation = web_harness.client.post(
        "/api/conversations",
        headers=headers,
        json={"related_order_id": order_ids[0]},
    ).json()
    thread_id = str(conversation["thread_id"])
    chat = web_harness.client.post(
        "/api/chat/messages",
        headers=headers,
        json={"thread_id": thread_id, "message": "请查询当前订单物流"},
    )
    assert chat.status_code == 200
    with open_sqlite_memory_store(
        web_harness.services.settings.memory_db_path
    ) as store:
        confirm_preference(
            store,
            user_id=target.user_id,
            workspace_id=target.workspace_id,
            proposal=MemoryProposal(
                proposal_id="reset-memory",
                case_id="reset-case",
                memory_type="preferred_language",
                value="zh-CN",
                purpose="后续客服使用该语言回复",
            ),
        )

    first = web_harness.client.post(
        "/api/demo-workspace/reset",
        headers=headers,
        json={
            "client_request_id": "workspace-reset-contract",
            "confirmation": "RESET",
        },
    )
    repeated = web_harness.client.post(
        "/api/demo-workspace/reset",
        headers=headers,
        json={
            "client_request_id": "workspace-reset-contract",
            "confirmation": "RESET",
        },
    )

    assert first.status_code == repeated.status_code == 200
    assert tuple(sorted(first.json()["order_ids"])) == order_ids
    assert repeated.json()["already_completed"] is True
    assert web_harness.client.get(f"/api/conversations/{thread_id}").status_code == 404
    with open_sqlite_checkpointer(
        web_harness.services.settings.checkpoint_db_path
    ) as checkpointer:
        assert (
            checkpointer.get_tuple({"configurable": {"thread_id": thread_id}}) is None
        )
    with open_sqlite_memory_store(
        web_harness.services.settings.memory_db_path
    ) as store:
        assert (
            list_preferences(
                store,
                user_id=target.user_id,
                workspace_id=target.workspace_id,
            )
            == ()
        )
