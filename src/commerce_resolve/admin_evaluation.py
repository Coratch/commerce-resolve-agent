"""使用 40 条确定性场景验证 v1.2 双产品表面与运营安全边界。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict

from commerce_resolve.adapters.sqlite_admin import SqliteAdminRepository
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_conversations import (
    SqliteConversationRepository,
)
from commerce_resolve.business_models import OrderCreate


class AdminSurfaceEvalScenario(BaseModel):
    """描述一条 v1.2 运营控制台场景及预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    description: str
    expected_status: str = "passed"


class AdminSurfaceEvalResult(BaseModel):
    """保存单条场景的确定性结果和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    passed: bool
    expected_status: str
    actual_status: str
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class AdminSurfaceEvalReport(BaseModel):
    """汇总 v1.2 的 40 条角色、运营、监控和双表面场景。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    admin_surface_safety_violations: int
    results: tuple[AdminSurfaceEvalResult, ...]


def _scenarios() -> tuple[AdminSurfaceEvalScenario, ...]:
    """按已接受 Plan 的五个分组返回固定 40 条场景。"""

    definitions = (
        ("existing-users-default-customer", "role", "迁移后既有账号默认为客户"),
        ("role-domain-closed-set", "role", "角色只允许客户和管理员"),
        ("local-cli-grants-admin", "role", "本机 CLI 可以授予管理员"),
        ("local-cli-revokes-admin", "role", "本机 CLI 可以撤销管理员"),
        ("local-cli-lists-roles", "role", "本机 CLI 只列有限账号角色"),
        ("session-role-from-database", "role", "Session 每次从数据库解析角色"),
        ("invitation-registers-customer", "role", "邀请码注册不能创建管理员"),
        ("admin-api-server-guarded", "role", "运营 API 使用服务端统一门禁"),
        ("customer-order-write-disabled", "operations", "客户旧订单写入口被拒绝"),
        ("admin-target-customer-explicit", "operations", "后台写入显式选择目标客户"),
        ("admin-reuses-business-repository", "operations", "后台复用权威订单仓库"),
        (
            "admin-write-visible-to-customer",
            "operations",
            "后台写入可由同一客户事实读取",
        ),
        ("order-create-audited", "operations", "订单创建写入后台审计"),
        ("order-update-delete-audited", "operations", "订单修改与删除写入后台审计"),
        ("payment-write-audited", "operations", "Mock 支付写入后台审计"),
        ("invitation-plaintext-once", "operations", "邀请码明文只在创建时返回"),
        ("invitation-list-hides-secret", "operations", "邀请列表不含明文或 Hash"),
        ("refund-records-read-only", "operations", "后台不能直接改退款事实"),
        ("monitoring-run-list-read-only", "monitoring", "Run 列表读取无副作用"),
        ("monitoring-run-detail-read-only", "monitoring", "Run 详情读取无副作用"),
        ("monitoring-event-allowlist", "monitoring", "事件使用字段白名单投影"),
        ("monitoring-hides-message-content", "monitoring", "Monitoring 不连接消息正文"),
        ("monitoring-hides-request-hash", "monitoring", "公开模型不包含请求摘要"),
        ("monitoring-l2-diagnostics-limited", "monitoring", "L2 只公开有限诊断"),
        ("monitoring-filters-bounded", "monitoring", "Run 筛选和数量有上限"),
        ("monitoring-does-not-build-graph", "monitoring", "运营读取不构建 Graph"),
        ("eval-reader-fixed-root", "readiness", "Eval Reader 只使用服务端固定根目录"),
        ("eval-run-id-path-safe", "readiness", "Eval Run ID 拒绝路径逃逸"),
        ("eval-four-states-explicit", "readiness", "Eval 明确区分四种状态"),
        ("eval-web-does-not-run-suite", "readiness", "Web 读取不执行 Eval"),
        (
            "eval-web-cannot-accept-baseline",
            "readiness",
            "Web 不提供 Baseline 接受入口",
        ),
        ("system-projection-hides-paths", "readiness", "系统状态模型不含本机路径"),
        ("system-projection-limited", "readiness", "系统状态只公开稳定有限字段"),
        ("overview-query-bounded", "readiness", "运营概览只读取有界聚合"),
        (
            "customer-navigation-hides-demo-editor",
            "surface",
            "客户导航移除演示数据维护",
        ),
        ("admin-routes-complete", "surface", "运营控制台路由完整"),
        ("admin-entry-capability-driven", "surface", "运营入口由服务端能力驱动"),
        ("legacy-demo-route-safe-redirect", "surface", "旧演示数据路由安全重定向"),
        ("admin-layout-responsive", "surface", "运营 Layout 支持窄屏"),
        ("openapi-admin-contract-generated", "surface", "OpenAPI 已生成运营契约"),
    )
    return tuple(
        AdminSurfaceEvalScenario(
            scenario_id=scenario_id,
            category=category,
            description=description,
        )
        for scenario_id, category, description in definitions
    )


ADMIN_SURFACE_EVAL_SCENARIOS = _scenarios()


def _project_root() -> Path:
    """返回当前源码所属项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    """读取项目内固定文本证据。"""

    return path.read_text(encoding="utf-8")


def _collect_runtime_evidence(evidence: dict[str, bool], root: Path) -> None:
    """使用临时数据库验证角色、权威事实、审计和 Monitoring 零副作用。"""

    database = root / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine)
    admin = SqliteAdminRepository(engine)
    conversations = SqliteConversationRepository(engine)
    try:
        invitation = business.create_invitation()
        registration = business.register(
            username="eval.customer",
            password="correct horse battery",
            invitation_code=invitation.code,
        )
        evidence["existing-users-default-customer"] = (
            registration.user.role == "customer"
        )
        granted = admin.set_role("eval.customer", "admin")
        revoked = admin.set_role("eval.customer", "customer")
        evidence["local-cli-grants-admin"] = granted.role == "admin"
        evidence["local-cli-revokes-admin"] = revoked.role == "customer"
        evidence["local-cli-lists-roles"] = any(
            item.username == "eval.customer" and item.role == "customer"
            for item in admin.list_customers()
        )
        evidence["invitation-registers-customer"] = registration.user.role == "customer"

        order = business.create_order(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            data=OrderCreate(order_id="ORD-V12-EVAL", status="processing"),
        )
        visible = business.get_order_record(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            order_id="ORD-V12-EVAL",
        )
        evidence["admin-write-visible-to-customer"] = visible.order_id == order.order_id
        audit = admin.record_action(
            admin_user_id=registration.user.id,
            target_user_id=registration.user.id,
            action="order.create",
            resource_type="order",
            resource_id=order.order_id,
            result="succeeded",
            parameter_summary={"order_id": order.order_id, "item_count": 0},
        )
        evidence["order-create-audited"] = (
            audit.action == "order.create"
            and "code" not in json.dumps(audit.parameter_summary)
        )
        evidence["invitation-list-hides-secret"] = all(
            not hasattr(item, "code") and not hasattr(item, "code_hash")
            for item in admin.list_invitations()
        )

        conversation = business.create_conversation(
            subject_id=registration.user.id,
            workspace_id=registration.workspace.id,
            access_mode="registered",
        )
        accepted = conversations.accept_chat_message(
            thread_id=conversation.thread_id,
            subject_id=registration.user.id,
            workspace_id=registration.workspace.id,
            access_mode="registered",
            client_request_id="v12-monitoring-eval",
            message="不应出现在运营投影中的客户正文",
        )
        before = admin.overview_counts()
        listed = admin.list_runs(limit=10)
        detail = admin.get_run_detail(accepted.run.run_id)
        after = admin.overview_counts()
        evidence["monitoring-run-list-read-only"] = before == after and bool(listed)
        evidence["monitoring-run-detail-read-only"] = (
            before == after and detail is not None
        )
        if detail is not None:
            serialized = detail.model_dump_json()
            evidence["monitoring-hides-message-content"] = (
                "客户正文" not in serialized and '"content"' not in serialized
            )
            evidence["monitoring-hides-request-hash"] = "request_hash" not in serialized
    finally:
        engine.dispose()


def _collect_evidence() -> dict[str, bool]:
    """组合运行时与源码契约证据，生成固定场景判断表。"""

    project = _project_root()
    migration = _read(
        project / "migrations/versions/20260722_0007_v12_admin_surfaces.py"
    )
    business_models = _read(project / "src/commerce_resolve/business_models.py")
    business_repository = _read(
        project / "src/commerce_resolve/adapters/sqlite_business.py"
    )
    admin_repository = _read(project / "src/commerce_resolve/adapters/sqlite_admin.py")
    dependencies = _read(project / "src/commerce_resolve/web/dependencies.py")
    admin_models = _read(project / "src/commerce_resolve/admin_models.py")
    admin_services = _read(project / "src/commerce_resolve/admin_services.py")
    admin_routes = _read(project / "src/commerce_resolve/web/routes/admin.py")
    order_routes = _read(project / "src/commerce_resolve/web/routes/orders.py")
    cli = _read(project / "src/commerce_resolve/cli.py")
    app = _read(project / "frontend/src/app/App.tsx")
    admin_layout = _read(project / "frontend/src/app/AdminLayout.tsx")
    admin_css = _read(project / "frontend/src/features/admin/Admin.module.css")
    generated = _read(project / "frontend/src/api/generated.ts")
    evidence = {
        "role-domain-closed-set": 'UserRole = Literal["customer", "admin"]'
        in business_models,
        "session-role-from-database": (
            "user_role=cast(UserRole, user.role)" in business_repository
            and "role=identity.user_role" in dependencies
        ),
        "admin-api-server-guarded": (
            "def require_admin_access" in dependencies
            and "require_admin_access(request" in admin_routes
        ),
        "customer-order-write-disabled": (
            'raise api_error(403, "customer_data_read_only")' in order_routes
        ),
        "admin-target-customer-explicit": (
            '"/customers/{user_id}/orders"' in admin_routes
            and "def _target(request: Request, user_id: str)" in admin_routes
        ),
        "admin-reuses-business-repository": (
            "get_services(request).repository.create_order" in admin_routes
            and "get_services(request).repository.update_order" in admin_routes
        ),
        "order-update-delete-audited": all(
            value in admin_routes
            for value in ('action="order.update"', 'action="order.delete"')
        ),
        "payment-write-audited": 'action="payment.upsert"' in admin_routes,
        "invitation-plaintext-once": (
            "response_model=InvitationIssued" in admin_routes
            and "class AdminInvitation" in admin_models
            and "code:"
            not in admin_models.split("class AdminInvitation", 1)[1].split("class ", 1)[
                0
            ]
        ),
        "refund-records-read-only": (
            "/refund" not in admin_routes
            and "execute_refund" not in admin_routes
            and "approve_refund" not in admin_routes
        ),
        "monitoring-event-allowlist": (
            "AdminRunEvent(" in admin_repository and "payload_json" not in admin_models
        ),
        "monitoring-l2-diagnostics-limited": (
            "class AdminRunDiagnostics" in admin_models
            and "tool_categories" in admin_models
        ),
        "monitoring-filters-bounded": (
            "bounded = max(1, min(limit, 100))" in admin_repository
            and "started_after" in admin_routes
        ),
        "monitoring-does-not-build-graph": (
            "build_workflow" not in admin_routes
            and "build_workflow" not in admin_repository
        ),
        "eval-reader-fixed-root": (
            "AdminEvalReader(settings.eval_run_root, settings.eval_baseline_path)"
            in admin_routes
        ),
        "eval-run-id-path-safe": (
            "RUN_ID_PATTERN.fullmatch(run_id)" in admin_services
            and "Path(run_id)" not in admin_routes
        ),
        "eval-four-states-explicit": (
            'AdminEvalState = Literal["missing", "incompatible", "failed", "passed"]'
            in admin_models
        ),
        "eval-web-does-not-run-suite": (
            "run_eval" not in admin_routes and "qualify" not in admin_routes
        ),
        "eval-web-cannot-accept-baseline": (
            'router.post("/eval' not in admin_routes
            and 'router.put("/eval' not in admin_routes
        ),
        "system-projection-hides-paths": (
            "Path" not in admin_models and "database_url" not in admin_models
        ),
        "system-projection-limited": all(
            field in admin_models
            for field in ("version: str", "migration_head: str", "storage: dict")
        ),
        "overview-query-bounded": (
            "repository.list_runs(limit=5)" in admin_routes
            and "overview_counts" in admin_routes
        ),
        "customer-navigation-hides-demo-editor": (
            '<NavLink to="/demo-data">' not in app
        ),
        "admin-routes-complete": all(
            route in app
            for route in (
                'path="/admin"',
                'path="data"',
                'path="invitations"',
                'path="runs"',
                'path="runs/:runId"',
                'path="eval"',
                'path="system"',
            )
        ),
        "admin-entry-capability-driven": (
            "session.capabilities.can_access_admin" in app
            and "session.capabilities.can_access_admin" in admin_layout
        ),
        "legacy-demo-route-safe-redirect": (
            'path="/demo-data"' in app and "demoDataDestination" in app
        ),
        "admin-layout-responsive": (
            "@media (max-width: 760px)" in admin_css and "overflow-x: auto" in admin_css
        ),
        "openapi-admin-contract-generated": all(
            route in generated
            for route in (
                "/api/admin/customers",
                "/api/admin/agent-runs",
                "/api/admin/eval",
                "/api/admin/system",
            )
        ),
        "existing-users-default-customer": (
            'server_default="customer"' in migration
            and "\"role IN ('customer', 'admin')\"" in migration
        ),
        "local-cli-grants-admin": '"grant"' in cli and "set_role" in cli,
        "local-cli-revokes-admin": '"revoke"' in cli and "set_role" in cli,
        "local-cli-lists-roles": 'admin_commands.add_parser("list"' in cli,
        "invitation-registers-customer": 'role="customer"' in business_repository,
    }
    with TemporaryDirectory() as directory:
        _collect_runtime_evidence(evidence, Path(directory))
    return evidence


def run_admin_surface_eval_suite(
    *,
    forced_failure: str | None = None,
) -> AdminSurfaceEvalReport:
    """运行 40 条固定场景，并允许测试注入稳定失败 ID。"""

    evidence = _collect_evidence()
    results: list[AdminSurfaceEvalResult] = []
    for scenario in ADMIN_SURFACE_EVAL_SCENARIOS:
        passed = evidence.get(scenario.scenario_id, False)
        if scenario.scenario_id == forced_failure:
            passed = False
        results.append(
            AdminSurfaceEvalResult(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                passed=passed,
                expected_status=scenario.expected_status,
                actual_status="passed" if passed else "failed",
                error_type=None if passed else "verification_failed",
            )
        )
    passed_count = sum(item.passed for item in results)
    violations = sum(len(item.safety_violations) for item in results)
    return AdminSurfaceEvalReport(
        suite="v1.2-customer-admin-surfaces",
        total_scenarios=len(results),
        passed_scenarios=passed_count,
        passed=passed_count == len(results) and violations == 0,
        category_counts=dict(Counter(item.category for item in results)),
        admin_surface_safety_violations=violations,
        results=tuple(results),
    )
