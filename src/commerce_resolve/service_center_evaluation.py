"""使用 36 条确定性场景验证 v1.1 售后服务中心。"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict

from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_service_center import SqliteSupportCenterReader
from commerce_resolve.business_models import OrderCreate, OrderItemInput
from commerce_resolve.order_context import extract_explicit_order_id
from commerce_resolve.service_center import (
    GuestSupportCatalog,
    map_l2_status,
    map_refund_status,
)


class ServiceCenterEvalScenario(BaseModel):
    """描述一条 v1.1 售后中心场景及预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    description: str
    expected_status: str = "passed"


class ServiceCenterEvalResult(BaseModel):
    """保存单条场景的脱敏结果和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    passed: bool
    expected_status: str
    actual_status: str
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class ServiceCenterEvalReport(BaseModel):
    """汇总 v1.1 的 36 条售后服务中心 Eval。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    service_center_safety_violations: int
    results: tuple[ServiceCenterEvalResult, ...]


def _scenarios() -> tuple[ServiceCenterEvalScenario, ...]:
    """按已接受 Plan 的五个分组返回固定 36 条场景。"""

    definitions = (
        ("guest-overview-server-backed", "orders", "游客首页使用服务端目录"),
        ("guest-order-detail-consistent", "orders", "游客列表与详情事实一致"),
        ("legacy-order-empty-items", "orders", "旧订单商品行保持为空"),
        ("order-items-persisted", "orders", "商品行可持久化读取"),
        ("shipment-milestones-deterministic", "orders", "物流里程碑确定性生成"),
        ("missing-payment-not-invented", "orders", "缺少支付时不补造事实"),
        ("order-cursor-stable", "orders", "订单使用稳定游标排序"),
        ("support-read-model-no-graph", "orders", "只读接口不运行 Graph"),
        ("conversation-binding-column", "binding", "数据库保存订单绑定"),
        ("conversation-binding-public", "binding", "公开摘要包含绑定"),
        ("explicit-order-normalized", "binding", "显式订单号规范化"),
        ("omitted-order-detected", "binding", "省略订单号保持为空待回填"),
        ("runtime-bound-context", "binding", "Runtime 构造可信绑定上下文"),
        ("mismatch-before-model", "binding", "冲突在模型前停止"),
        ("same-binding-active-reuse", "binding", "同订单活动会话可恢复"),
        ("message-cannot-rebind-order", "binding", "消息请求不能改绑定"),
        ("cross-scope-conversation-hidden", "binding", "跨身份会话不可见"),
        ("deleted-order-keeps-binding", "binding", "订单删除不级联会话"),
        ("refund-status-complete", "services", "退款状态映射完整"),
        ("l2-status-complete", "services", "L2 状态映射完整"),
        ("service-id-namespaced", "services", "服务标识按类型命名"),
        ("service-steps-public-only", "services", "时间线只含客户步骤"),
        ("service-citations-limited", "services", "引用使用有限公开字段"),
        ("service-read-no-model", "services", "读取服务不调用模型"),
        ("service-query-full-scope", "services", "服务查询使用完整身份作用域"),
        ("service-projection-no-truth-table", "services", "不创建第二套服务事实"),
        ("support-routes-complete", "ui", "客户路由完整"),
        ("root-redirects-support", "ui", "根路由稳定跳转售后首页"),
        ("demo-editor-secondary", "ui", "演示数据编辑器位于次级入口"),
        ("mobile-native-dialog", "ui", "移动助手使用原生 dialog"),
        ("customer-text-no-html-execution", "ui", "客户文本不使用危险 HTML"),
        ("reduced-motion-and-thread-reuse", "ui", "降低动效并复用服务会话"),
        ("migration-head-v11", "release", "业务迁移 Head 对应 v1.1"),
        ("openapi-support-contract", "release", "OpenAPI 包含 Support 契约"),
        ("release-versions-aligned", "release", "前后端版本一致"),
        ("data-format-compatible", "release", "增量 Schema 保持数据格式兼容"),
    )
    return tuple(
        ServiceCenterEvalScenario(
            scenario_id=scenario_id,
            category=category,
            description=description,
        )
        for scenario_id, category, description in definitions
    )


SERVICE_CENTER_EVAL_SCENARIOS = _scenarios()


def _project_root() -> Path:
    """返回当前源码所属项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    """读取历史版本文本证据；文件被新版取代时返回空证据。"""

    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _collect_repository_evidence(evidence: dict[str, bool], root: Path) -> None:
    """在临时数据库中收集订单、商品行、绑定和作用域证据。"""

    database = root / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    repository = SqliteBusinessRepository(engine)
    invitation = repository.create_invitation()
    registration = repository.register(
        username="eval.owner",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    user_id = registration.user.id
    workspace_id = registration.workspace.id
    legacy = repository.create_order(
        user_id=user_id,
        workspace_id=workspace_id,
        data=OrderCreate(order_id="ORD-EVAL-LEGACY", status="processing"),
    )
    repository.create_order(
        user_id=user_id,
        workspace_id=workspace_id,
        data=OrderCreate(
            order_id="ORD-EVAL-ITEM",
            status="delivered",
            items=(
                OrderItemInput(
                    sku="SKU-EVAL",
                    title="评测商品",
                    quantity=2,
                ),
            ),
        ),
    )
    reader = SqliteSupportCenterReader(engine)
    detail = reader.get_order(
        user_id=user_id,
        workspace_id=workspace_id,
        order_id="ORD-EVAL-ITEM",
    )
    first = reader.list_orders(user_id=user_id, workspace_id=workspace_id, limit=1)
    second = reader.list_orders(
        user_id=user_id,
        workspace_id=workspace_id,
        limit=1,
        before=(first[0].updated_at, first[0].order_id),
    )
    conversation = repository.create_conversation(
        subject_id=user_id,
        workspace_id=workspace_id,
        access_mode="registered",
        related_order_id="ORD-EVAL-ITEM",
    )
    authorized = repository.get_authorized_conversation(
        thread_id=conversation.thread_id,
        subject_id=user_id,
        workspace_id=workspace_id,
        access_mode="registered",
    )
    hidden = repository.get_authorized_conversation(
        thread_id=conversation.thread_id,
        subject_id="other-user",
        workspace_id=workspace_id,
        access_mode="registered",
    )
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(conversations)"
        ).fetchall()

    evidence.update(
        {
            "legacy-order-empty-items": legacy.items == (),
            "order-items-persisted": (
                detail is not None
                and detail.items[0].sku == "SKU-EVAL"
                and detail.items[0].quantity == 2
            ),
            "missing-payment-not-invented": (
                detail is not None and detail.payment is None
            ),
            "order-cursor-stable": (
                len(first) == len(second) == 1
                and first[0].order_id != second[0].order_id
            ),
            "conversation-binding-column": "related_order_id" in columns,
            "conversation-binding-public": (
                authorized is not None
                and authorized.related_order_id == "ORD-EVAL-ITEM"
            ),
            "cross-scope-conversation-hidden": hidden is None,
            "deleted-order-keeps-binding": all(
                row[2] != "orders" for row in foreign_keys
            ),
            "service-projection-no-truth-table": "service_records" not in tables,
        }
    )
    engine.dispose()


def _collect_evidence() -> dict[str, bool]:
    """收集不依赖网络、真实模型或真实交易的固定证据。"""

    project = _project_root()
    evidence: dict[str, bool] = {}
    guest = GuestSupportCatalog()
    overview = guest.overview()
    guest_detail = guest.order_detail("ORD-001")
    evidence.update(
        {
            "guest-overview-server-backed": (
                len(overview.recent_orders) == 1
                and overview.recent_orders[0].order_id == "ORD-001"
            ),
            "guest-order-detail-consistent": (
                guest_detail is not None
                and guest_detail.summary == overview.recent_orders[0]
            ),
            "shipment-milestones-deterministic": (
                guest_detail is not None
                and [item.state for item in guest_detail.shipment_milestones]
                == ["completed", "current", "upcoming"]
            ),
            "explicit-order-normalized": (
                extract_explicit_order_id("查询 ord-a12 的物流") == "ORD-A12"
            ),
            "omitted-order-detected": extract_explicit_order_id("它到哪里了") is None,
            "refund-status-complete": {
                map_refund_status(status)
                for status in (
                    "awaiting_approval",
                    "executing",
                    "completed",
                    "rejected",
                    "stale",
                    "failed",
                    "unknown",
                    "verification_failed",
                )
            }
            <= {
                "waiting_user",
                "in_progress",
                "completed",
                "needs_attention",
                "cancelled",
            },
            "l2-status-complete": {
                map_l2_status(status)
                for status in (
                    "l2_active",
                    "l2_waiting_user",
                    "l2_waiting_approval",
                    "l2_resolved",
                    "l2_cancelled",
                    "l2_unresolved",
                    "l2_budget_exhausted",
                    "l2_stopped",
                )
            }
            <= {
                "waiting_user",
                "in_progress",
                "completed",
                "needs_attention",
                "cancelled",
            },
        }
    )
    with TemporaryDirectory() as directory:
        _collect_repository_evidence(evidence, Path(directory))

    runtime = _read(project / "src/commerce_resolve/conversation_runtime.py")
    chat_route = _read(project / "src/commerce_resolve/web/routes/chat.py")
    support_route = _read(project / "src/commerce_resolve/web/routes/support.py")
    support_reader = _read(
        project / "src/commerce_resolve/adapters/sqlite_service_center.py"
    )
    schemas = _read(project / "src/commerce_resolve/web/schemas.py")
    app = _read(project / "frontend/src/app/App.tsx")
    assistant = _read(project / "frontend/src/features/support/ContextualAssistant.tsx")
    support_css = _read(project / "frontend/src/features/support/Support.module.css")
    service_page = _read(
        project / "frontend/src/features/support/ServiceDetailPage.tsx"
    )
    frontend_source = "\n".join(
        _read(path)
        for path in (project / "frontend/src/features/support").glob("*.tsx")
    )
    migration = _read(
        project / "migrations/versions/20260722_0006_v11_service_center.py"
    )
    generated_api = _read(project / "frontend/src/api/generated.ts")
    pyproject = _read(project / "pyproject.toml")
    package = json.loads(_read(project / "frontend/package.json"))
    version = _read(project / "src/commerce_resolve/__init__.py")
    evidence.update(
        {
            "support-read-model-no-graph": (
                "build_workflow" not in support_route
                and "interpreter" not in support_route
            ),
            "runtime-bound-context": "bound_order_id" in runtime,
            "mismatch-before-model": (
                "_complete_order_context_mismatch" in runtime
                and "explicit_order_id" in runtime
            ),
            "same-binding-active-reuse": (
                "item.related_order_id == related_order_id" in chat_route
                and "item.message_count == 0" in chat_route
                and "related_order_id is not None" in chat_route
            ),
            "message-cannot-rebind-order": (
                "class ConversationCreateRequest" in schemas
                and "class AsyncChatMessageRequest" in schemas
                and "related_order_id"
                not in schemas.split("class AsyncChatMessageRequest", maxsplit=1)[
                    1
                ].split("class ", maxsplit=1)[0]
            ),
            "service-id-namespaced": (
                'service_id=f"refund:' in support_reader
                and 'service_id=f"l2:' in support_reader
            ),
            "service-steps-public-only": (
                "PublicServiceStep" in support_reader
                and "node_name" not in service_page
            ),
            "service-citations-limited": (
                "SupportCitation" in support_reader
                and "context_manifest" not in service_page
            ),
            "service-read-no-model": "interpreter" not in support_reader,
            "service-query-full-scope": all(
                field in support_reader
                for field in ("subject_id", "user_id", "workspace_id")
            ),
            "support-routes-complete": all(
                route in app
                for route in (
                    'path="/support"',
                    'path="/orders"',
                    'path="/orders/:orderId"',
                    'path="/services"',
                    'path="/services/:serviceId"',
                )
            ),
            "root-redirects-support": '<Navigate replace to="/support"' in app,
            "demo-editor-secondary": 'path="/demo-data"' in app,
            "mobile-native-dialog": "<dialog" in assistant and "showModal" in assistant,
            "customer-text-no-html-execution": (
                "dangerouslySetInnerHTML" not in frontend_source
            ),
            "reduced-motion-and-thread-reuse": (
                "prefers-reduced-motion" in support_css
                and "threadId={detail.summary.thread_id}" in service_page
            ),
            "migration-head-v11": (
                'revision: str = "20260722_0006"' in migration
                and 'down_revision: str | Sequence[str] | None = "20260721_0005"'
                in migration
            ),
            "openapi-support-contract": all(
                route in generated_api
                for route in (
                    "/api/support/overview",
                    "/api/support/orders",
                    "/api/support/orders/{order_id}",
                    "/api/support/services",
                    "/api/support/services/{service_id}",
                )
            ),
            "release-versions-aligned": (
                f'version = "{package["version"]}"' in pyproject
                and f'__version__ = "{package["version"]}"' in version
            ),
            "data-format-compatible": (
                "commerce-resolve-data-v1"
                in _read(project / "src/commerce_resolve/operations/models.py")
            ),
        }
    )
    return evidence


def run_service_center_eval_suite(
    *, forced_failure: str | None = None
) -> ServiceCenterEvalReport:
    """运行 36 条固定场景，并允许测试注入稳定失败 ID。"""

    evidence = _collect_evidence()
    results: list[ServiceCenterEvalResult] = []
    for scenario in SERVICE_CENTER_EVAL_SCENARIOS:
        passed = evidence.get(scenario.scenario_id, False)
        if scenario.scenario_id == forced_failure:
            passed = False
        results.append(
            ServiceCenterEvalResult(
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
    return ServiceCenterEvalReport(
        suite="v1.1-post-purchase-service-center",
        total_scenarios=len(results),
        passed_scenarios=passed_count,
        passed=passed_count == len(results) and violations == 0,
        category_counts=dict(Counter(item.category for item in results)),
        service_center_safety_violations=violations,
        results=tuple(results),
    )
