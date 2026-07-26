"""使用 48 条确定性场景验证 v1.3 商业化售后体验。"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_policy import (
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.demo_catalog import DemoCatalogService
from commerce_resolve.gateways import Dependencies
from commerce_resolve.models import OrderView, ShipmentView
from commerce_resolve.service_resolution import ServiceResolution
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow


class CommercialExperienceEvalScenario(BaseModel):
    """描述一条 v1.3 固定场景及预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    description: str
    expected_status: str = "passed"


class CommercialExperienceEvalResult(BaseModel):
    """保存单条场景的确定性结果和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    passed: bool
    expected_status: str
    actual_status: str
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class CommercialExperienceEvalReport(BaseModel):
    """汇总 v1.3 的 48 条商业化售后体验 Eval。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    commercial_experience_safety_violations: int
    results: tuple[CommercialExperienceEvalResult, ...]


def _scenarios() -> tuple[CommercialExperienceEvalScenario, ...]:
    """按已接受 Plan 的五个分组返回固定 48 条场景。"""

    definitions = (
        ("catalog-version-fixed", "catalog", "目录版本固定为 v1.3"),
        ("catalog-product-count", "catalog", "目录至少包含 12 个 SPU"),
        ("catalog-sku-count", "catalog", "目录至少包含 18 个 SKU"),
        ("catalog-persona-count", "catalog", "目录至少包含 3 个画像"),
        ("catalog-scenario-count", "catalog", "目录至少包含 10 个场景"),
        ("catalog-assets-verified", "catalog", "本地资源摘要全部通过校验"),
        ("catalog-assets-local-only", "catalog", "商品资源只使用同源本地路径"),
        ("catalog-seed-idempotent", "catalog", "重复初始化不复制业务事实"),
        ("snapshot-migration-additive", "catalog", "0008 增量增加商品快照"),
        ("v12-upgrade-compatible", "catalog", "0008 从 v1.2 Head 原地升级"),
        ("guest-catalog-shared", "projection", "游客场景复用版本化目录"),
        ("support-search-in-sql", "projection", "订单搜索在 SQLite 查询内执行"),
        ("support-view-bounded", "projection", "订单状态筛选使用有限枚举"),
        ("support-cursor-bound-query", "projection", "游标绑定搜索和筛选条件"),
        ("overview-product-preview", "projection", "首页返回商品预览"),
        ("order-snapshot-public", "projection", "订单详情返回快照状态"),
        ("package-detail-public", "projection", "订单详情返回包裹与商品关联"),
        ("amount-source-separated", "projection", "快照小计与权威支付金额分离"),
        ("service-product-preview", "projection", "服务投影关联有限商品预览"),
        ("support-read-no-model", "projection", "售后读取不调用模型"),
        ("ui-product-fallback", "projection", "商品图片具有同源降级资源"),
        ("ui-commercial-order-card", "projection", "订单卡片展示履约和售后信号"),
        ("fake-combined-intent", "guidance", "Fake 识别组合咨询"),
        ("combined-concerns-complete", "guidance", "组合关注点完整"),
        ("explicit-refund-priority", "guidance", "明确退款优先走退款路径"),
        ("single-order-compatible", "guidance", "单一订单查询保持旧路径"),
        ("single-policy-compatible", "guidance", "单一政策查询保持旧路径"),
        ("guidance-nodes-registered", "guidance", "组合节点已注册到主图"),
        ("guidance-order-once", "guidance", "组合咨询只查询一次订单"),
        ("guidance-shipment-once", "guidance", "组合咨询只查询一次物流"),
        ("guidance-policy-once", "guidance", "组合咨询只检索一次政策"),
        ("resolution-schema-closed", "guidance", "结构化方案禁止额外字段"),
        ("allowed-actions-closed", "guidance", "允许动作来自有限枚举"),
        ("guidance-no-refund-write", "guidance", "资格咨询不创建退款副作用"),
        ("payload-v2-persisted", "recovery", "新助手消息保存 Payload v2"),
        ("payload-v1-readable", "recovery", "Payload v1 保持兼容"),
        ("unknown-payload-text-only", "recovery", "未知 Payload 只显示正文"),
        ("sse-public-stage-limited", "recovery", "SSE 只公开有限阶段"),
        ("checkpoint-resolution-allowed", "recovery", "Checkpoint 支持结构化方案"),
        ("refresh-message-source", "recovery", "刷新从消息 API 恢复最终方案"),
        ("admin-seed-guarded", "safety", "场景写入经过管理员权限链"),
        ("admin-seed-audited", "safety", "场景初始化写入脱敏审计"),
        ("catalog-path-contained", "safety", "资源路径拒绝逃逸"),
        ("support-scope-filtered", "safety", "售后查询绑定用户与工作区"),
        ("refund-approval-reused", "safety", "退款仍复用既有审批 API"),
        ("client-actions-fixed", "safety", "前端动作映射不执行任意 URL"),
        ("responsive-keyboard-motion", "safety", "三视口、键盘与降动效均覆盖"),
        ("release-contract-v13", "safety", "版本、迁移与 OpenAPI 契约一致"),
    )
    return tuple(
        CommercialExperienceEvalScenario(
            scenario_id=scenario_id,
            category=category,
            description=description,
        )
        for scenario_id, category, description in definitions
    )


COMMERCIAL_EXPERIENCE_EVAL_SCENARIOS = _scenarios()


def _project_root() -> Path:
    """返回当前源码所属项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    """读取项目内固定文本证据。"""

    return path.read_text(encoding="utf-8")


def _catalog_and_seed_evidence(evidence: dict[str, bool]) -> None:
    """校验目录，并在临时业务库验证初始化幂等性。"""

    project = _project_root()
    catalog_service = DemoCatalogService(project_root=project)
    summary = catalog_service.summary()
    _catalog, assets = catalog_service.load()
    evidence.update(
        {
            "catalog-version-fixed": summary.catalog_version == "v1.3",
            "catalog-product-count": summary.product_count >= 12,
            "catalog-sku-count": summary.sku_count >= 18,
            "catalog-persona-count": summary.persona_count >= 3,
            "catalog-scenario-count": summary.scenario_count >= 10,
            "catalog-assets-verified": len(assets.assets) >= 12,
            "catalog-assets-local-only": all(
                item.relative_path.startswith("catalog/v1.3/")
                and "://" not in item.relative_path
                for item in assets.assets
            ),
        }
    )
    with TemporaryDirectory() as directory:
        database = Path(directory) / "business.sqlite"
        upgrade_business_database(database)
        engine = create_business_engine(database)
        repository = SqliteBusinessRepository(engine)
        invitation = repository.create_invitation()
        registration = repository.register(
            username="v13.eval",
            password="correct horse battery",
            invitation_code=invitation.code,
        )
        service = DemoCatalogService(project_root=project, engine=engine)
        scenario_id = next(
            item.scenario_id for item in summary.scenarios if item.packages
        )
        first = service.seed(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            scenario_id=scenario_id,
        )
        second = service.seed(
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            scenario_id=scenario_id,
        )
        with sqlite3.connect(database) as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "orders",
                    "order_items",
                    "shipment_packages",
                    "shipment_package_items",
                    "mock_payments",
                )
            }
        evidence["catalog-seed-idempotent"] = (
            first.created
            and not second.created
            and first.order_id == second.order_id
            and counts["orders"] == 1
            and all(value >= 1 for key, value in counts.items() if key != "orders")
        )
        engine.dispose()


def _guidance_evidence(evidence: dict[str, bool]) -> None:
    """运行真实主图组合路径并收集调用预算和资金边界证据。"""

    interpreter = FakeQueryInterpreter()
    single_order = interpreter.interpret("查询 ORD-001")
    single_policy = interpreter.interpret("普通商品退货期限是几天？")
    refund = interpreter.interpret("请发起退款 ORD-001，商品有质量问题")
    combo = interpreter.interpret("ORD-001 的物流到哪了，并且能不能退款？")
    evidence.update(
        {
            "fake-combined-intent": combo.intent == "service_guidance",
            "combined-concerns-complete": set(combo.concerns)
            == {"shipment_status", "refund_eligibility"},
            "explicit-refund-priority": refund.intent == "refund_request",
            "single-order-compatible": single_order.intent == "order_inquiry",
            "single-policy-compatible": single_policy.intent == "policy_inquiry",
        }
    )
    with TemporaryDirectory() as directory:
        policy_database = Path(directory) / "policy.sqlite"
        build_policy_index(_project_root() / "data/policies", policy_database)
        order_gateway = FakeOrderGateway(
            {
                ("eval-user", "ORD-001"): OrderView(
                    order_id="ORD-001",
                    user_id="eval-user",
                    status="delivered",
                )
            }
        )
        logistics_gateway = FakeLogisticsGateway(
            {
                "ORD-001": ShipmentView(
                    order_id="ORD-001",
                    status="delivered",
                    last_event="包裹已签收",
                    estimated_delivery_at=date(2026, 7, 23),
                )
            }
        )
        policy = SqlitePolicyRepository(
            policy_database,
            source_root=_project_root() / "data/policies",
        )
        graph = build_workflow(
            Dependencies(
                interpreter=interpreter,
                order_gateway=order_gateway,
                logistics_gateway=logistics_gateway,
                policy_repository=policy,
            ),
            checkpointer=InMemorySaver(),
        )
        result = graph.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "ORD-001 的物流到哪了，并且能不能退款？",
                    }
                ]
            },
            config={"configurable": {"thread_id": "v13-eval-guidance"}},
            context=RunContext(user_id="eval-user", as_of=date(2026, 7, 23)),
        )
        resolution = result.get("service_resolution")
        evidence.update(
            {
                "guidance-order-once": order_gateway.calls
                == [("eval-user", "ORD-001")],
                "guidance-shipment-once": logistics_gateway.calls == ["ORD-001"],
                "guidance-policy-once": len(policy.calls) == 1,
                "resolution-schema-closed": isinstance(resolution, ServiceResolution)
                and resolution.stop_reason == "completed",
                "allowed-actions-closed": isinstance(resolution, ServiceResolution)
                and set(resolution.allowed_actions)
                <= {
                    "view_order",
                    "view_policy",
                    "request_refund",
                    "upgrade_l2",
                    "provide_information",
                },
                "guidance-no-refund-write": result.get("refund_action_id") is None
                and result.get("refund_result") is None,
            }
        )


def _source_evidence(evidence: dict[str, bool]) -> None:
    """从版本化源码、迁移和生成契约收集结构与安全证据。"""

    project = _project_root()
    migration = _read(
        project / "migrations/versions/20260722_0008_v13_commercial_experience.py"
    )
    support_reader = _read(
        project / "src/commerce_resolve/adapters/sqlite_service_center.py"
    )
    support_routes = _read(project / "src/commerce_resolve/web/routes/support.py")
    service_center = _read(project / "src/commerce_resolve/service_center.py")
    service_models = _read(project / "src/commerce_resolve/service_center_models.py")
    admin_routes = _read(project / "src/commerce_resolve/web/routes/admin.py")
    workflow = _read(project / "src/commerce_resolve/service_guidance.py")
    projection = _read(project / "src/commerce_resolve/conversation_projection.py")
    chat_route = _read(project / "src/commerce_resolve/web/routes/chat.py")
    checkpointing = _read(project / "src/commerce_resolve/checkpointing.py")
    client = _read(project / "frontend/src/api/client.ts")
    decoder = _read(project / "frontend/src/features/chat/useConversationSession.ts")
    card = _read(project / "frontend/src/features/chat/ServiceResolutionCard.tsx")
    support_css = _read(project / "frontend/src/features/support/Support.module.css")
    panel = _read(project / "frontend/src/features/chat/ConversationPanel.tsx")
    generated = _read(project / "frontend/src/api/generated.ts")
    package = json.loads(_read(project / "frontend/package.json"))
    pyproject = _read(project / "pyproject.toml")
    version = _read(project / "src/commerce_resolve/__init__.py")
    evidence.update(
        {
            "snapshot-migration-additive": all(
                field in migration
                for field in (
                    "product_ref",
                    "variant_title",
                    "unit_amount_minor",
                    "image_ref",
                    "shipment_packages",
                )
            ),
            "v12-upgrade-compatible": (
                'down_revision: str | Sequence[str] | None = "20260722_0007"'
                in migration
            ),
            "guest-catalog-shared": "DemoCatalogService" in service_center,
            "support-search-in-sql": "pattern =" in support_reader
            and "func.lower" in support_reader,
            "support-view-bounded": all(
                value in support_routes
                for value in (
                    '"all"',
                    '"processing"',
                    '"shipping"',
                    '"delivered"',
                    '"after_sales"',
                )
            ),
            "support-cursor-bound-query": "_decode_cursor(cursor, binding=binding)"
            in support_routes
            and '"binding": binding' in support_routes,
            "overview-product-preview": "preview_items" in support_reader,
            "order-snapshot-public": "snapshot_state" in support_reader
            and "class SupportOrderItem" in service_models,
            "package-detail-public": "class SupportShipmentPackage" in service_models
            and "items=tuple(" in support_reader,
            "amount-source-separated": "class SupportAmountSummary" in service_models
            and "item_subtotal" in support_reader
            and "paid_amount" in support_reader,
            "service-product-preview": "product_preview" in support_reader,
            "support-read-no-model": "interpreter" not in support_routes,
            "ui-product-fallback": "/catalog/v1.3/fallback.svg"
            in _read(project / "frontend/src/features/support/ProductThumbnail.tsx"),
            "ui-commercial-order-card": all(
                field
                in _read(project / "frontend/src/features/support/OrderSummaryCard.tsx")
                for field in ("fulfillment_summary", "latest_service_summary")
            ),
            "guidance-nodes-registered": all(
                node in workflow
                for node in (
                    "prepare_service_guidance",
                    "load_guidance_order",
                    "load_guidance_shipment",
                    "retrieve_guidance_policy",
                    "assemble_service_resolution",
                )
            ),
            "payload-v2-persisted": "payload_version=2" in chat_route
            and "service_resolution" in projection,
            "payload-v1-readable": "message.payload_version !== 1" in decoder,
            "unknown-payload-text-only": "message.payload_version !== 2" in decoder
            and "citations: []" in decoder,
            "sse-public-stage-limited": "assembling_service"
            in _read(project / "src/commerce_resolve/conversation_runtime.py")
            and "raw_prompt"
            not in _read(project / "src/commerce_resolve/conversation_runtime.py"),
            "checkpoint-resolution-allowed": "ServiceResolution" in checkpointing,
            "refresh-message-source": "listConversationMessages" in decoder
            and "service_resolution" in decoder,
            "admin-seed-guarded": "require_admin_access" in admin_routes
            and "mutation=True" in admin_routes,
            "admin-seed-audited": 'action="demo_scenario.seed"' in admin_routes,
            "catalog-path-contained": "_safe_child"
            in _read(project / "src/commerce_resolve/demo_catalog.py"),
            "support-scope-filtered": all(
                field in support_reader for field in ("user_id", "workspace_id")
            ),
            "refund-approval-reused": "/refund-approval" in client
            and "request_refund" in card,
            "client-actions-fixed": "actionLabels" in card
            and "window.location" not in card,
            "responsive-keyboard-motion": all(
                marker in support_css
                for marker in (
                    "@media (max-width: 1000px)",
                    "@media (max-width: 700px)",
                    "prefers-reduced-motion",
                )
            )
            and "handleKeyDown" in panel,
            "release-contract-v13": (
                f'version = "{package["version"]}"' in pyproject
                and f'__version__ = "{package["version"]}"' in version
                and package["version"] == "1.3.0"
                and 'revision: str = "20260722_0008"' in migration
                and "ServiceResolution" in generated
                and "/api/admin/demo-catalog" in generated
            ),
        }
    )


def _collect_evidence() -> dict[str, bool]:
    """收集不依赖网络、真实模型或真实支付的全部固定证据。"""

    evidence: dict[str, bool] = {}
    _catalog_and_seed_evidence(evidence)
    _guidance_evidence(evidence)
    _source_evidence(evidence)
    return evidence


def run_commercial_experience_eval_suite(
    *, forced_failure: str | None = None
) -> CommercialExperienceEvalReport:
    """运行 48 条固定场景，并允许测试注入稳定失败 ID。"""

    evidence = _collect_evidence()
    results: list[CommercialExperienceEvalResult] = []
    for scenario in COMMERCIAL_EXPERIENCE_EVAL_SCENARIOS:
        passed = evidence.get(scenario.scenario_id, False)
        if scenario.scenario_id == forced_failure:
            passed = False
        results.append(
            CommercialExperienceEvalResult(
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
    return CommercialExperienceEvalReport(
        suite="v1.3-commercial-service-experience",
        total_scenarios=len(results),
        passed_scenarios=passed_count,
        passed=passed_count == len(results) and violations == 0,
        category_counts=dict(Counter(item.category for item in results)),
        commercial_experience_safety_violations=violations,
        results=tuple(results),
    )
