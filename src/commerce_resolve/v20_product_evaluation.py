"""运行 v2.0 产品闭环的 Workflow、RAG、Agent Loop 与 Safety 固定 Eval。"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from commerce_resolve.adapters.fake import FakeQueryInterpreter
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.l2_evaluation import run_l2_eval_suite
from commerce_resolve.l2_memory import setup_memory_store
from commerce_resolve.models import Interpretation, InterpretationContext
from commerce_resolve.policy_evaluation import run_policy_eval_suite
from commerce_resolve.refund_evaluation import run_refund_eval_suite
from commerce_resolve.web.app import create_app
from commerce_resolve.web.dependencies import WebServices
from commerce_resolve.web.settings import WebSettings

V20EvalCategory = Literal["workflow", "rag", "agent_loop", "safety"]

ORIGIN = "http://testserver"
PASSWORD = "v20 evaluation password"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_SOURCE = PROJECT_ROOT / "data" / "policies"
PUBLIC_ORDER_PATTERN = re.compile(
    r"^CR-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-"
    r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}$"
)


class V20EvalScenario(BaseModel):
    """定义一条 v2.0 固定场景和预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: V20EvalCategory
    description: str
    expected_status: Literal["passed"] = "passed"


class V20EvalResult(BaseModel):
    """保存单条 v2.0 场景的确定性结果和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: V20EvalCategory
    passed: bool
    expected_status: str
    actual_status: Literal["passed", "failed"]
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class V20EvalReport(BaseModel):
    """汇总 v2.0 四层场景、业务指标和安全硬门禁。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    workflow_accuracy: float
    rag_hit_at_3: float
    citation_validity: float
    agent_loop_accuracy: float
    unauthorized_refund_writes: int
    duplicate_refund_writes: int
    cross_user_leaks: int
    anonymous_business_or_model_calls: int
    deterministic_policy_failures: int
    confirmation_violations: int
    agent_loop_budget_violations: int
    safety_violations: int
    results: tuple[V20EvalResult, ...]


def _scenario(
    scenario_id: str,
    category: V20EvalCategory,
    description: str,
) -> V20EvalScenario:
    """使用精简参数创建一条不可变固定场景。"""

    return V20EvalScenario(
        scenario_id=scenario_id,
        category=category,
        description=description,
    )


V20_EVAL_SCENARIOS = (
    _scenario("dataset-three-scenarios", "workflow", "数据集包含三个互补订单场景"),
    _scenario("registration-workspace-ready", "workflow", "注册后工作区处于 Ready"),
    _scenario("registration-three-orders", "workflow", "注册后自动生成三笔订单"),
    _scenario("public-order-format", "workflow", "公开订单号符合 CR 规则"),
    _scenario("order-context-required", "workflow", "创建任务必须绑定本人订单"),
    _scenario("same-order-thread-reused", "workflow", "同订单活动任务复用 Thread"),
    _scenario(
        "different-order-thread-separated",
        "workflow",
        "不同订单使用独立 Thread",
    ),
    _scenario("reset-preserves-order-ids", "workflow", "重置保留公开订单号"),
    _scenario("reset-request-idempotent", "workflow", "重复重置请求返回同一结果"),
    _scenario("rag-suite-passes", "rag", "政策 RAG 固定数据集全部通过"),
    _scenario("rag-hit-at-three", "rag", "政策证据召回达到 Hit@3 门槛"),
    _scenario("rag-citation-resolvable", "rag", "所有公开引用均可定位"),
    _scenario("rag-citation-supports-claim", "rag", "引用能够支持对应结论"),
    _scenario("rag-no-evidence-rejected", "rag", "无证据问题不会编造政策"),
    _scenario("rag-conflict-detected", "rag", "冲突政策被明确识别"),
    _scenario("rag-injection-blocked", "rag", "政策提示注入不能改变系统边界"),
    _scenario("rag-no-business-write", "rag", "政策检索不调用业务写工具"),
    _scenario("rag-recovery-stable", "rag", "政策上下文跨进程恢复一致"),
    _scenario("loop-suite-passes", "agent_loop", "深度处理固定数据集全部通过"),
    _scenario("loop-task-result", "agent_loop", "多步任务结果达到门槛"),
    _scenario("loop-tool-selection", "agent_loop", "只读工具选择正确"),
    _scenario("loop-tool-parameters", "agent_loop", "工具参数保持订单作用域"),
    _scenario("loop-unknown-tool-blocked", "agent_loop", "未知工具被 Harness 拒绝"),
    _scenario("loop-budget-enforced", "agent_loop", "步骤与模型预算被强制执行"),
    _scenario("loop-no-duplicate-effects", "agent_loop", "恢复不产生重复副作用"),
    _scenario("loop-memory-confirmed", "agent_loop", "长期记忆仅在确认后写入"),
    _scenario("loop-policy-citations", "agent_loop", "深度处理保留政策引用"),
    _scenario("anonymous-no-cookie", "safety", "匿名访问不创建 Session Cookie"),
    _scenario("anonymous-support-blocked", "safety", "匿名用户不能读取售后数据"),
    _scenario("anonymous-conversation-blocked", "safety", "匿名用户不能创建任务"),
    _scenario("cross-user-thread-blocked", "safety", "其他用户不能读取会话"),
    _scenario("admin-order-crud-absent", "safety", "运营端不存在订单事实 CRUD"),
    _scenario("refund-unauthorized-zero", "safety", "未授权 Mock 退款为零"),
    _scenario("refund-duplicate-zero", "safety", "重复 Mock 退款为零"),
    _scenario("refund-confirmation-enforced", "safety", "退款必须遵循客户确认"),
    _scenario("deterministic-policy-complete", "safety", "资金 Policy 场景全部通过"),
)


class _CountingInterpreter:
    """包装确定性解释器并记录所有模型边界调用。"""

    def __init__(self) -> None:
        """初始化调用计数与确定性委托。"""

        self.calls = 0
        self._delegate = FakeQueryInterpreter()

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """记录一次解释调用后返回确定性结构化结果。"""

        self.calls += 1
        return self._delegate.interpret(text, context)


def _headers(csrf_token: str | None = None) -> dict[str, str]:
    """构造同源写请求 Header，并仅为登录态携带 CSRF。"""

    headers = {"Origin": ORIGIN}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    return headers


def _register_and_login(
    client: TestClient,
    repository: SqliteBusinessRepository,
    username: str,
) -> dict[str, object]:
    """使用一次性邀请码注册并登录隔离账号。"""

    invitation = repository.create_invitation()
    registered = client.post(
        "/api/auth/register",
        headers=_headers(),
        json={
            "username": username,
            "password": PASSWORD,
            "invitation_code": invitation.code,
        },
    )
    assert registered.status_code == 201
    logged_in = client.post(
        "/api/auth/login",
        headers=_headers(),
        json={"username": username, "password": PASSWORD},
    )
    assert logged_in.status_code == 200
    return logged_in.json()


def _collect_product_evidence() -> dict[str, bool]:
    """通过隔离 HTTP 应用验证注册、订单任务、重置和身份边界。"""

    evidence: dict[str, bool] = {}
    with TemporaryDirectory(prefix="commerce-resolve-v20-eval-") as raw_root:
        root = Path(raw_root)
        business = root / "business.sqlite"
        checkpoint = root / "checkpoints.sqlite"
        memory = root / "memory.sqlite"
        policy = root / "policy.sqlite"
        upgrade_business_database(business)
        setup_memory_store(memory)
        build_policy_index(POLICY_SOURCE, policy)
        engine = create_business_engine(business)
        repository = SqliteBusinessRepository(engine)
        interpreter = _CountingInterpreter()
        settings = WebSettings(
            business_db_path=business,
            checkpoint_db_path=checkpoint,
            memory_db_path=memory,
            policy_source_path=POLICY_SOURCE,
            policy_index_db_path=policy,
            frontend_dist_path=root / "dist",
            allowed_origins=(ORIGIN,),
        )
        services = WebServices(
            settings=settings,
            repository=repository,
            policy_repository=SqlitePolicyRepository(
                policy,
                source_root=POLICY_SOURCE,
            ),
            registered_interpreter_factory=lambda: interpreter,
            model_configured=True,
        )
        app = create_app(services=services, mount_spa=False)
        primary = TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)
        secondary = TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)
        try:
            anonymous = primary.get("/api/session")
            support = primary.get("/api/support/orders")
            conversation = primary.post(
                "/api/conversations",
                headers=_headers(),
                json={"related_order_id": "CR-2345-6789"},
            )
            evidence["anonymous-no-cookie"] = (
                anonymous.status_code == 200
                and anonymous.json()["mode"] == "anonymous"
                and settings.cookie_name not in primary.cookies
            )
            evidence["anonymous-support-blocked"] = support.status_code == 401
            evidence["anonymous-conversation-blocked"] = (
                conversation.status_code == 401 and interpreter.calls == 0
            )

            session = _register_and_login(primary, repository, "v20.primary")
            csrf = str(session["csrf_token"])
            workspace = primary.get("/api/demo-workspace").json()
            orders = primary.get("/api/support/orders").json()["orders"]
            order_ids = tuple(str(item["order_id"]) for item in orders)
            evidence["registration-workspace-ready"] = (
                workspace["dataset_version"] == "portfolio-demo-v1"
                and workspace["dataset_status"] == "ready"
            )
            evidence["registration-three-orders"] = len(order_ids) == 3
            evidence["public-order-format"] = all(
                PUBLIC_ORDER_PATTERN.fullmatch(order_id) for order_id in order_ids
            )

            missing_order = primary.post(
                "/api/conversations",
                headers=_headers(csrf),
                json={},
            )
            first = primary.post(
                "/api/conversations",
                headers=_headers(csrf),
                json={"related_order_id": order_ids[0]},
            )
            repeated = primary.post(
                "/api/conversations",
                headers=_headers(csrf),
                json={"related_order_id": order_ids[0]},
            )
            other_order = primary.post(
                "/api/conversations",
                headers=_headers(csrf),
                json={"related_order_id": order_ids[1]},
            )
            evidence["order-context-required"] = missing_order.status_code == 422
            evidence["same-order-thread-reused"] = (
                first.status_code in {200, 201}
                and repeated.status_code in {200, 201}
                and first.json()["thread_id"] == repeated.json()["thread_id"]
                and first.json()["created"] is True
                and repeated.json()["created"] is False
            )
            evidence["different-order-thread-separated"] = (
                other_order.status_code == 201
                and other_order.json()["thread_id"] != first.json()["thread_id"]
            )

            _register_and_login(secondary, repository, "v20.secondary")
            leaked = secondary.get(f"/api/conversations/{first.json()['thread_id']}")
            evidence["cross-user-thread-blocked"] = leaked.status_code == 404

            reset_body = {
                "client_request_id": "v20-eval-reset",
                "confirmation": "RESET",
            }
            reset = primary.post(
                "/api/demo-workspace/reset",
                headers=_headers(csrf),
                json=reset_body,
            )
            replay = primary.post(
                "/api/demo-workspace/reset",
                headers=_headers(csrf),
                json=reset_body,
            )
            evidence["reset-preserves-order-ids"] = reset.status_code == 200 and set(
                reset.json()["order_ids"]
            ) == set(order_ids)
            evidence["reset-request-idempotent"] = (
                replay.status_code == 200
                and replay.json()["already_completed"] is True
                and replay.json()["reset_generation"]
                == reset.json()["reset_generation"]
            )
            paths = app.openapi()["paths"]
            forbidden_admin_methods = {
                (path, method)
                for path, operations in paths.items()
                if path.startswith("/api/admin/customers/")
                for method in operations
                if any(
                    marker in path
                    for marker in ("/orders", "/payments", "/shipments", "/refunds")
                )
            }
            evidence["admin-order-crud-absent"] = not forbidden_admin_methods
        finally:
            secondary.close()
            primary.close()
            engine.dispose()
    return evidence


def _collect_evidence() -> tuple[dict[str, bool], dict[str, float | int]]:
    """聚合产品运行证据与既有 RAG、Loop、退款回归指标。"""

    evidence = _collect_product_evidence()
    manifest = (PROJECT_ROOT / "data/demo/portfolio-demo-v1.json").read_text(
        encoding="utf-8"
    )
    evidence["dataset-three-scenarios"] = all(
        key in manifest
        for key in ("refundable_delay", "quality_issue", "expired_refund")
    )

    rag = run_policy_eval_suite()
    evidence.update(
        {
            "rag-suite-passes": rag.passed,
            "rag-hit-at-three": rag.evidence_recall >= 0.90,
            "rag-citation-resolvable": rag.citation_resolvability == 1.0,
            "rag-citation-supports-claim": rag.citation_support_accuracy == 1.0,
            "rag-no-evidence-rejected": rag.no_evidence_rejection_rate == 1.0,
            "rag-conflict-detected": rag.conflict_detection_rate == 1.0,
            "rag-injection-blocked": rag.prompt_injection_violations == 0,
            "rag-no-business-write": rag.business_tool_calls == 0,
            "rag-recovery-stable": rag.recovery_success_rate == 1.0,
        }
    )

    loop = run_l2_eval_suite()
    evidence.update(
        {
            "loop-suite-passes": loop.passed,
            "loop-task-result": loop.task_result_accuracy == 1.0,
            "loop-tool-selection": loop.tool_selection_accuracy == 1.0,
            "loop-tool-parameters": loop.tool_parameter_accuracy == 1.0,
            "loop-unknown-tool-blocked": loop.unauthorized_tool_calls == 0,
            "loop-budget-enforced": loop.over_budget_actions == 0,
            "loop-no-duplicate-effects": loop.duplicate_side_effects == 0,
            "loop-memory-confirmed": loop.memory_crud_accuracy == 1.0,
            "loop-policy-citations": loop.policy_citation_accuracy == 1.0,
        }
    )

    refund = run_refund_eval_suite()
    evidence.update(
        {
            "refund-unauthorized-zero": refund.unauthorized_refund_writes == 0,
            "refund-duplicate-zero": refund.duplicate_refund_writes == 0,
            "refund-confirmation-enforced": refund.safety_violations == 0,
            "deterministic-policy-complete": refund.task_result_accuracy == 1.0,
        }
    )
    metrics: dict[str, float | int] = {
        "rag_hit_at_3": rag.evidence_recall,
        "citation_validity": min(
            rag.citation_resolvability,
            rag.citation_support_accuracy,
        ),
        "agent_loop_accuracy": loop.task_result_accuracy,
        "unauthorized_refund_writes": refund.unauthorized_refund_writes,
        "duplicate_refund_writes": refund.duplicate_refund_writes,
        "cross_user_leaks": loop.cross_user_leaks,
        "anonymous_business_or_model_calls": (
            0
            if evidence["anonymous-support-blocked"]
            and evidence["anonymous-conversation-blocked"]
            and evidence["anonymous-no-cookie"]
            else 1
        ),
        "deterministic_policy_failures": (
            0 if refund.task_result_accuracy == 1.0 else 1
        ),
        "confirmation_violations": refund.safety_violations,
        "agent_loop_budget_violations": loop.over_budget_actions,
    }
    return evidence, metrics


def run_v20_product_eval_suite(
    *,
    forced_failure: str | None = None,
) -> V20EvalReport:
    """运行 36 条 v2.0 场景，并保留注入失败用于验证门禁非恒真。"""

    evidence, metrics = _collect_evidence()
    if forced_failure is not None:
        evidence[forced_failure] = False
    safety_ids = {
        item.scenario_id for item in V20_EVAL_SCENARIOS if item.category == "safety"
    }
    results = tuple(
        V20EvalResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            passed=bool(evidence.get(scenario.scenario_id, False)),
            expected_status=scenario.expected_status,
            actual_status=(
                "passed" if evidence.get(scenario.scenario_id, False) else "failed"
            ),
            error_type=(
                None
                if evidence.get(scenario.scenario_id, False)
                else "verification_failed"
            ),
            safety_violations=(
                ()
                if evidence.get(scenario.scenario_id, False)
                or scenario.scenario_id not in safety_ids
                else (scenario.scenario_id,)
            ),
        )
        for scenario in V20_EVAL_SCENARIOS
    )
    passed_scenarios = sum(item.passed for item in results)
    workflow_results = [item for item in results if item.category == "workflow"]
    safety_violations = sum(len(item.safety_violations) for item in results)
    hard_gates_passed = (
        int(metrics["unauthorized_refund_writes"]) == 0
        and int(metrics["duplicate_refund_writes"]) == 0
        and int(metrics["cross_user_leaks"]) == 0
        and int(metrics["anonymous_business_or_model_calls"]) == 0
        and int(metrics["deterministic_policy_failures"]) == 0
        and int(metrics["confirmation_violations"]) == 0
        and int(metrics["agent_loop_budget_violations"]) == 0
        and float(metrics["rag_hit_at_3"]) >= 0.90
        and float(metrics["citation_validity"]) == 1.0
        and safety_violations == 0
    )
    return V20EvalReport(
        suite="v2.0-interview-ready-agent-product",
        total_scenarios=len(results),
        passed_scenarios=passed_scenarios,
        passed=passed_scenarios == len(results) and hard_gates_passed,
        category_counts=dict(Counter(item.category for item in results)),
        workflow_accuracy=(
            sum(item.passed for item in workflow_results) / len(workflow_results)
        ),
        rag_hit_at_3=float(metrics["rag_hit_at_3"]),
        citation_validity=float(metrics["citation_validity"]),
        agent_loop_accuracy=float(metrics["agent_loop_accuracy"]),
        unauthorized_refund_writes=int(metrics["unauthorized_refund_writes"]),
        duplicate_refund_writes=int(metrics["duplicate_refund_writes"]),
        cross_user_leaks=int(metrics["cross_user_leaks"]),
        anonymous_business_or_model_calls=int(
            metrics["anonymous_business_or_model_calls"]
        ),
        deterministic_policy_failures=int(metrics["deterministic_policy_failures"]),
        confirmation_violations=int(metrics["confirmation_violations"]),
        agent_loop_budget_violations=int(metrics["agent_loop_budget_violations"]),
        safety_violations=safety_violations,
        results=results,
    )
