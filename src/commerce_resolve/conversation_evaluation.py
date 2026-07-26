"""运行 v0.6 会话生命周期、Run、SSE 与恢复的 32 条固定 Eval。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import sessionmaker

from commerce_resolve.adapters.fake import FakeQueryInterpreter
from commerce_resolve.adapters.sqlalchemy_models import ConversationRow
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.l2_memory import setup_memory_store
from commerce_resolve.web.app import create_app
from commerce_resolve.web.dependencies import WebServices
from commerce_resolve.web.settings import WebSettings

V06Category = Literal[
    "history_recovery",
    "lifecycle",
    "identity_isolation",
    "idempotency_concurrency",
    "pending_action",
    "sse_failure_recovery",
    "data_compatibility",
]

ORIGIN = "http://testserver"
PASSWORD = "v06 eval correct horse battery"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_SOURCE = PROJECT_ROOT / "data" / "policies"


class ConversationEvalScenario(BaseModel):
    """定义一条固定 v0.6 场景及预期类别。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: V06Category


class ConversationEvalResult(BaseModel):
    """保存单场景结果和脱敏失败类型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: V06Category
    passed: bool
    error_type: str | None = None


class ConversationEvalReport(BaseModel):
    """汇总 v0.6 固定场景和全部发布门槛。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    duplicate_messages: int
    duplicate_runs: int
    duplicate_events: int
    cross_identity_leaks: int
    public_data_leaks: int
    lost_messages: int
    category_counts: dict[str, int]
    passed: bool
    results: tuple[ConversationEvalResult, ...]


def _scenarios(
    category: V06Category,
    *names: str,
) -> tuple[ConversationEvalScenario, ...]:
    """把一组稳定名称转换为同类别不可变场景。"""

    return tuple(
        ConversationEvalScenario(scenario_id=name, category=category) for name in names
    )


SCENARIOS = (
    *_scenarios(
        "history_recovery",
        "history-three-turns",
        "history-policy-citations",
        "history-action-payload",
        "history-refresh",
        "history-pagination",
        "history-partial-marker",
    ),
    *_scenarios(
        "lifecycle",
        "lifecycle-create",
        "lifecycle-empty-reuse",
        "lifecycle-list",
        "lifecycle-detail",
        "lifecycle-archive-restore",
        "lifecycle-delete-tombstone",
    ),
    *_scenarios(
        "identity_isolation",
        "identity-registered-list",
        "identity-guest-rotation",
        "identity-cross-user-history",
        "identity-cross-user-run",
        "identity-deleted-url",
    ),
    *_scenarios(
        "idempotency_concurrency",
        "idempotency-client-request",
        "idempotency-payload-conflict",
        "idempotency-thread-busy",
        "idempotency-event-key",
        "idempotency-explicit-retry",
    ),
    *_scenarios(
        "pending_action",
        "pending-refund",
        "pending-l2-upgrade",
        "pending-user-input",
        "pending-memory",
    ),
    *_scenarios(
        "sse_failure_recovery",
        "sse-step-before-terminal",
        "sse-last-event-id",
        "failure-public-message",
        "recovery-interrupted-run",
    ),
    *_scenarios(
        "data_compatibility",
        "data-public-projection-minimal",
        "compatibility-legacy-sync-api",
    ),
)


class _Runtime:
    """为单场景创建隔离数据库、Fake 依赖和同源浏览器。"""

    def __init__(self, root: Path, policy_database: Path) -> None:
        """迁移隔离数据库并装配不访问网络的 Web 应用。"""

        self.business = root / "business.sqlite"
        self.checkpoint = root / "checkpoint.sqlite"
        self.memory = root / "memory.sqlite"
        upgrade_business_database(self.business)
        setup_memory_store(self.memory)
        self.engine = create_business_engine(self.business)
        self.repository = SqliteBusinessRepository(self.engine)
        self.settings = WebSettings(
            business_db_path=self.business,
            checkpoint_db_path=self.checkpoint,
            memory_db_path=self.memory,
            policy_source_path=POLICY_SOURCE,
            policy_index_db_path=policy_database,
            frontend_dist_path=root / "dist",
            allowed_origins=(ORIGIN,),
        )
        self.services = WebServices(
            settings=self.settings,
            repository=self.repository,
            policy_repository=SqlitePolicyRepository(
                policy_database,
                source_root=POLICY_SOURCE,
            ),
            registered_interpreter_factory=FakeQueryInterpreter,
            model_configured=True,
        )
        self.client = TestClient(
            create_app(services=self.services, mount_spa=False),
            base_url=ORIGIN,
            raise_server_exceptions=False,
        )
        self.register("v06.default")

    def close(self) -> None:
        """关闭测试客户端和业务 Engine。"""

        self.client.close()
        self.engine.dispose()

    def session(self, client: TestClient | None = None) -> dict[str, object]:
        """取得指定浏览器当前 Session 的公开数据。"""

        response = (client or self.client).get("/api/session")
        assert response.status_code == 200
        return response.json()

    def headers(self, csrf: str | None = None) -> dict[str, str]:
        """构造通过 Origin 和 CSRF 校验的写请求 Header。"""

        headers = {"Origin": ORIGIN}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        return headers

    def order_id(self, client: TestClient | None = None) -> str:
        """返回当前注册账号第一个可访问的预置订单号。"""

        response = (client or self.client).get("/api/support/orders")
        assert response.status_code == 200
        return str(response.json()["orders"][0]["order_id"])

    def conversation(
        self,
        csrf: str,
        client: TestClient | None = None,
    ) -> str:
        """为指定浏览器当前身份和预置订单创建或复用活动会话。"""

        selected = client or self.client
        response = selected.post(
            "/api/conversations",
            headers=self.headers(csrf),
            json={"related_order_id": self.order_id(selected)},
        )
        assert response.status_code in {200, 201}
        return str(response.json()["thread_id"])

    def register(
        self,
        username: str,
        client: TestClient | None = None,
    ) -> dict[str, object]:
        """使用隔离邀请码注册并登录一个测试账号。"""

        selected = client or self.client
        current = self.session(selected)
        if current["mode"] == "registered":
            selected.post(
                "/api/auth/logout",
                headers=self.headers(str(current["csrf_token"])),
            )
        invite = self.repository.create_invitation()
        assert (
            selected.post(
                "/api/auth/register",
                headers=self.headers(),
                json={
                    "username": username,
                    "password": PASSWORD,
                    "invitation_code": invite.code,
                },
            ).status_code
            == 201
        )
        response = selected.post(
            "/api/auth/login",
            headers=self.headers(),
            json={"username": username, "password": PASSWORD},
        )
        assert response.status_code == 200
        return response.json()

    def submit(
        self,
        thread_id: str,
        csrf: str,
        message: str,
        client_id: str,
    ) -> dict[str, object]:
        """提交一条异步消息，并返回 TestClient 等待后台完成后的 202 Body。"""

        response = self.client.post(
            f"/api/conversations/{thread_id}/messages",
            headers=self.headers(csrf),
            json={"client_message_id": client_id, "message": message},
        )
        assert response.status_code == 202
        return response.json()


def _identity(runtime: _Runtime):
    """从测试 Cookie 解析可信身份，供 Repository 层场景使用。"""

    token = runtime.client.cookies.get(runtime.settings.cookie_name)
    assert token is not None
    identity = runtime.repository.resolve_session(token)
    assert identity is not None
    return identity


def _pending_projection(runtime: _Runtime, action: str) -> bool:
    """直接构造公开待处理 Run，并验证刷新读取的动作标识稳定。"""

    session = runtime.session()
    csrf = str(session["csrf_token"])
    thread_id = runtime.conversation(csrf)
    identity = _identity(runtime)
    repository = runtime.services.require_conversation_repository()
    accepted = repository.accept_action(
        thread_id=thread_id,
        subject_id=identity.subject_id,
        workspace_id=identity.workspace_id,
        access_mode=identity.actor_type,
        client_request_id=f"pending-{action}",
        request_kind="refund_decision",
        label="待处理动作",
        request_payload={"action": action},
    )
    repository.mark_run_started(accepted.run.run_id)
    repository.complete_run(
        run_id=accepted.run.run_id,
        assistant_message="需要你的确认后才能继续。",
        payload={"public_status": "waiting", "l2_pending_action": action},
        pending_action=action,
    )
    detail = runtime.client.get(f"/api/conversations/{thread_id}")
    history = runtime.client.get(f"/api/conversations/{thread_id}/messages")
    return (
        detail.status_code == 200
        and detail.json()["conversation"]["pending_action"] == action
        and history.json()["messages"][-1]["payload"]["l2_pending_action"] == action
    )


def _evaluate(runtime: _Runtime, scenario: ConversationEvalScenario) -> bool:
    """执行单个固定场景并返回是否满足其确定性断言。"""

    current = runtime.session()
    csrf = str(current["csrf_token"])
    thread_id = runtime.conversation(csrf)
    repository = runtime.services.require_conversation_repository()
    scenario_id = scenario.scenario_id

    if scenario_id == "history-three-turns":
        for index, message in enumerate(
            ("帮我查询物流", "普通商品退货期限是几天？", "再查一次物流"),
            start=1,
        ):
            runtime.submit(thread_id, csrf, message, f"history-{index}")
        history = runtime.client.get(f"/api/conversations/{thread_id}/messages").json()
        return len(history["messages"]) == 6
    if scenario_id == "history-policy-citations":
        runtime.submit(thread_id, csrf, "普通商品退货期限是几天？", "citation-1")
        messages = runtime.client.get(
            f"/api/conversations/{thread_id}/messages"
        ).json()["messages"]
        return bool(messages[-1]["payload"]["citations"])
    if scenario_id == "history-action-payload":
        return _pending_projection(runtime, "refund_approval")
    if scenario_id == "history-refresh":
        runtime.submit(thread_id, csrf, "帮我查询物流", "refresh-1")
        first = runtime.client.get(f"/api/conversations/{thread_id}/messages").json()
        second = runtime.client.get(f"/api/conversations/{thread_id}/messages").json()
        return first == second and len(first["messages"]) == 2
    if scenario_id == "history-pagination":
        runtime.submit(thread_id, csrf, "帮我查询物流", "page-run-1")
        first = runtime.client.get(
            f"/api/conversations/{thread_id}/messages?limit=1"
        ).json()
        second = runtime.client.get(
            f"/api/conversations/{thread_id}/messages?limit=1&after_sequence="
            f"{first['next_after_sequence']}"
        ).json()
        return (
            first["messages"][0]["sequence_no"] == 1
            and second["messages"][0]["sequence_no"] == 2
        )
    if scenario_id == "history-partial-marker":
        sessions = sessionmaker(runtime.engine)
        with sessions.begin() as database:
            row = database.get(ConversationRow, thread_id)
            assert row is not None
            row.history_state = "partial"
        detail = runtime.client.get(f"/api/conversations/{thread_id}").json()
        return detail["conversation"]["history_state"] == "partial"
    if scenario_id == "lifecycle-create":
        return runtime.client.get(f"/api/conversations/{thread_id}").status_code == 200
    if scenario_id == "lifecycle-empty-reuse":
        return runtime.conversation(csrf) == thread_id
    if scenario_id == "lifecycle-list":
        listed = runtime.client.get("/api/conversations").json()["conversations"]
        return [item["thread_id"] for item in listed] == [thread_id]
    if scenario_id == "lifecycle-detail":
        detail = runtime.client.get(f"/api/conversations/{thread_id}").json()
        return bool(detail["conversation"]["title"])
    if scenario_id == "lifecycle-archive-restore":
        registered = runtime.register("v06.archive")
        csrf = str(registered["csrf_token"])
        thread_id = runtime.conversation(csrf)
        archived = runtime.client.patch(
            f"/api/conversations/{thread_id}",
            headers=runtime.headers(csrf),
            json={"lifecycle_status": "archived"},
        )
        restored = runtime.client.patch(
            f"/api/conversations/{thread_id}",
            headers=runtime.headers(csrf),
            json={"lifecycle_status": "active"},
        )
        return (
            archived.json()["conversation"]["lifecycle_status"] == "archived"
            and restored.json()["conversation"]["lifecycle_status"] == "active"
        )
    if scenario_id == "lifecycle-delete-tombstone":
        runtime.submit(thread_id, csrf, "帮我查询物流", "delete-1")
        deleted = runtime.client.delete(
            f"/api/conversations/{thread_id}", headers=runtime.headers(csrf)
        )
        with open_sqlite_checkpointer(runtime.checkpoint) as checkpointer:
            checkpoint = checkpointer.get_tuple(
                {"configurable": {"thread_id": thread_id}}
            )
        return deleted.status_code == 204 and checkpoint is None
    if scenario_id == "identity-registered-list":
        runtime.register("v06.registered")
        registered_csrf = str(runtime.session()["csrf_token"])
        own = runtime.conversation(registered_csrf)
        listed = runtime.client.get("/api/conversations").json()["conversations"]
        return [item["thread_id"] for item in listed] == [own]
    if scenario_id == "identity-guest-rotation":
        registered = runtime.register("v06.rotation")
        csrf = str(registered["csrf_token"])
        thread_id = runtime.conversation(csrf)
        rotated = runtime.client.post(
            "/api/auth/logout", headers=runtime.headers(csrf)
        ).json()
        old = runtime.client.get(f"/api/conversations/{thread_id}")
        return rotated["mode"] == "anonymous" and old.status_code == 401
    if scenario_id in {"identity-cross-user-history", "identity-cross-user-run"}:
        first = runtime.register("v06.first")
        first_csrf = str(first["csrf_token"])
        own_thread = runtime.conversation(first_csrf)
        accepted = runtime.submit(
            own_thread, first_csrf, "查询不存在订单", "cross-user-run"
        )
        other = TestClient(
            runtime.client.app,
            base_url=ORIGIN,
            raise_server_exceptions=False,
        )
        try:
            runtime.register("v06.second", other)
            path = (
                f"/api/conversations/{own_thread}/messages"
                if scenario_id.endswith("history")
                else f"/api/conversations/{own_thread}/runs/{accepted['run']['run_id']}"
            )
            return other.get(path).status_code == 404
        finally:
            other.close()
    if scenario_id == "identity-deleted-url":
        runtime.client.delete(
            f"/api/conversations/{thread_id}", headers=runtime.headers(csrf)
        )
        return runtime.client.get(f"/api/conversations/{thread_id}").status_code == 404
    if scenario_id in {
        "idempotency-client-request",
        "idempotency-payload-conflict",
    }:
        payload = {"client_message_id": "same-client-id", "message": "帮我查询物流"}
        first = runtime.client.post(
            f"/api/conversations/{thread_id}/messages",
            headers=runtime.headers(csrf),
            json=payload,
        )
        second_payload = (
            payload
            if scenario_id.endswith("request")
            else {**payload, "message": "不同问题"}
        )
        second = runtime.client.post(
            f"/api/conversations/{thread_id}/messages",
            headers=runtime.headers(csrf),
            json=second_payload,
        )
        return (
            first.json()["run"]["run_id"] == second.json()["run"]["run_id"]
            if scenario_id.endswith("request")
            else second.status_code == 409
        )
    if scenario_id == "idempotency-thread-busy":
        identity = _identity(runtime)
        repository.accept_chat_message(
            thread_id=thread_id,
            subject_id=identity.subject_id,
            workspace_id=identity.workspace_id,
            access_mode=identity.actor_type,
            client_request_id="busy-one",
            message="第一条",
        )
        try:
            repository.accept_chat_message(
                thread_id=thread_id,
                subject_id=identity.subject_id,
                workspace_id=identity.workspace_id,
                access_mode=identity.actor_type,
                client_request_id="busy-two",
                message="第二条",
            )
        except ValueError as error:
            return str(error) == "thread_busy"
        return False
    if scenario_id == "idempotency-event-key":
        identity = _identity(runtime)
        accepted = repository.accept_chat_message(
            thread_id=thread_id,
            subject_id=identity.subject_id,
            workspace_id=identity.workspace_id,
            access_mode=identity.actor_type,
            client_request_id="event-key",
            message="帮我查询物流",
        )
        repository.mark_run_started(accepted.run.run_id)
        first = repository.append_step_event(
            run_id=accepted.run.run_id,
            event_key="same-step",
            phase="testing",
            message="正在测试",
        )
        second = repository.append_step_event(
            run_id=accepted.run.run_id,
            event_key="same-step",
            phase="testing",
            message="正在测试",
        )
        return first.event_id == second.event_id
    if scenario_id == "idempotency-explicit-retry":
        identity = _identity(runtime)
        accepted = repository.accept_chat_message(
            thread_id=thread_id,
            subject_id=identity.subject_id,
            workspace_id=identity.workspace_id,
            access_mode=identity.actor_type,
            client_request_id="failed-original",
            message="查询 ORD-001",
        )
        repository.fail_run(
            run_id=accepted.run.run_id,
            error_code="run_failed",
            assistant_message="失败",
        )
        retried = runtime.client.post(
            f"/api/conversations/{thread_id}/runs/{accepted.run.run_id}/retry",
            headers=runtime.headers(csrf),
            json={"client_message_id": "retry-client"},
        )
        return (
            retried.status_code == 202
            and retried.json()["run"]["retry_of_run_id"] == accepted.run.run_id
        )
    if scenario_id.startswith("pending-"):
        action = {
            "pending-refund": "refund_approval",
            "pending-l2-upgrade": "upgrade_confirmation",
            "pending-user-input": "user_input",
            "pending-memory": "memory_confirmation",
        }[scenario_id]
        return _pending_projection(runtime, action)
    if scenario_id in {"sse-step-before-terminal", "sse-last-event-id"}:
        accepted = runtime.submit(thread_id, csrf, "帮我查询物流", "sse-run-1")
        run_id = accepted["run"]["run_id"]
        all_events = repository.list_events(run_id=run_id)
        if scenario_id.endswith("terminal"):
            types = [event.event_type for event in all_events]
            return "step.updated" in types and types.index(
                "step.updated"
            ) < types.index("run.completed")
        cursor = all_events[1].event_id
        replay = runtime.client.get(
            f"/api/conversations/{thread_id}/runs/{run_id}/events",
            headers={"Last-Event-ID": str(cursor)},
        ).text
        return f"id: {cursor}\n" not in replay and "event: run.completed" in replay
    if scenario_id == "failure-public-message":
        identity = _identity(runtime)
        accepted = repository.accept_chat_message(
            thread_id=thread_id,
            subject_id=identity.subject_id,
            workspace_id=identity.workspace_id,
            access_mode=identity.actor_type,
            client_request_id="failed-public",
            message="触发失败",
        )
        repository.fail_run(
            run_id=accepted.run.run_id,
            error_code="run_failed",
            assistant_message="本次处理未能完成，请重试。",
        )
        history = runtime.client.get(f"/api/conversations/{thread_id}/messages").json()
        return (
            history["messages"][-1]["kind"] == "status"
            and history["messages"][-1]["status"] == "failed"
        )
    if scenario_id == "recovery-interrupted-run":
        identity = _identity(runtime)
        accepted = repository.accept_chat_message(
            thread_id=thread_id,
            subject_id=identity.subject_id,
            workspace_id=identity.workspace_id,
            access_mode=identity.actor_type,
            client_request_id="interrupted-public",
            message="处理中",
        )
        count = repository.interrupt_unfinished_runs()
        run = repository.get_run(accepted.run.run_id, thread_id=thread_id)
        return count == 1 and run is not None and run.status == "interrupted"
    if scenario_id == "data-public-projection-minimal":
        runtime.submit(thread_id, csrf, "帮我查询物流", "minimal-public")
        history = runtime.client.get(f"/api/conversations/{thread_id}/messages").text
        forbidden = ("prompt", "reasoning", "tool_output", "api_key", "hidden")
        return all(item not in history.lower() for item in forbidden)
    if scenario_id == "compatibility-legacy-sync-api":
        response = runtime.client.post(
            "/api/chat/messages",
            headers=runtime.headers(csrf),
            json={"thread_id": thread_id, "message": "帮我查询物流"},
        )
        history = runtime.client.get(f"/api/conversations/{thread_id}/messages").json()
        return response.status_code == 200 and len(history["messages"]) == 2
    raise AssertionError(f"unknown scenario: {scenario_id}")


def run_conversation_eval_suite() -> ConversationEvalReport:
    """运行 32 条隔离场景并汇总消息、身份和公开数据门槛。"""

    results: list[ConversationEvalResult] = []
    with TemporaryDirectory(prefix="commerce-resolve-v06-eval-") as raw_root:
        root = Path(raw_root)
        policy_database = root / "policy.sqlite"
        build_policy_index(POLICY_SOURCE, policy_database)
        for index, scenario in enumerate(SCENARIOS):
            scenario_root = root / f"scenario-{index:02d}"
            scenario_root.mkdir()
            runtime = _Runtime(scenario_root, policy_database)
            try:
                passed = _evaluate(runtime, scenario)
                results.append(
                    ConversationEvalResult(
                        scenario_id=scenario.scenario_id,
                        category=scenario.category,
                        passed=passed,
                    )
                )
            except Exception as error:
                results.append(
                    ConversationEvalResult(
                        scenario_id=scenario.scenario_id,
                        category=scenario.category,
                        passed=False,
                        error_type=type(error).__name__,
                    )
                )
            finally:
                runtime.close()
    passed_count = sum(result.passed for result in results)
    failed_ids = {result.scenario_id for result in results if not result.passed}
    duplicate_messages = sum("idempotency" in item for item in failed_ids)
    duplicate_runs = sum("client-request" in item for item in failed_ids)
    duplicate_events = sum("event-key" in item for item in failed_ids)
    cross_identity_leaks = sum("identity" in item for item in failed_ids)
    public_data_leaks = sum("minimal" in item for item in failed_ids)
    lost_messages = sum("history" in item for item in failed_ids)
    return ConversationEvalReport(
        suite="v0.6",
        total_scenarios=len(SCENARIOS),
        passed_scenarios=passed_count,
        duplicate_messages=duplicate_messages,
        duplicate_runs=duplicate_runs,
        duplicate_events=duplicate_events,
        cross_identity_leaks=cross_identity_leaks,
        public_data_leaks=public_data_leaks,
        lost_messages=lost_messages,
        category_counts=dict(Counter(item.category for item in SCENARIOS)),
        passed=(
            passed_count == len(SCENARIOS)
            and duplicate_messages == 0
            and duplicate_runs == 0
            and duplicate_events == 0
            and cross_identity_leaks == 0
            and public_data_leaks == 0
            and lost_messages == 0
        ),
        results=tuple(results),
    )
