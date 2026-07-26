"""使用 32 条确定性场景验证 v1.3.1 商业产品可信度契约。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from commerce_resolve.demo_catalog import DemoCatalogService


class CommercialCredibilityEvalScenario(BaseModel):
    """描述一条商业可信度固定场景及其预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    description: str
    expected_status: str = "passed"


class CommercialCredibilityEvalResult(BaseModel):
    """保存单条场景的确定性结果和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    passed: bool
    expected_status: str
    actual_status: str
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class CommercialCredibilityEvalReport(BaseModel):
    """汇总 v1.3.1 的 32 条商业可信度 Eval。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    commercial_credibility_safety_violations: int
    results: tuple[CommercialCredibilityEvalResult, ...]


def _scenarios() -> tuple[CommercialCredibilityEvalScenario, ...]:
    """按信息架构、语言、业务、运营、体验和证据返回固定场景。"""

    definitions = (
        (
            "customer-navigation-task-based",
            "information_architecture",
            "客户导航按售后任务组织",
        ),
        (
            "home-task-first-density",
            "information_architecture",
            "首页以任务、服务和订单为首屏主体",
        ),
        ("home-account-boundary", "information_architecture", "首页明确账号与数据边界"),
        (
            "orders-business-heading",
            "information_architecture",
            "订单页使用客户业务标题",
        ),
        (
            "services-business-heading",
            "information_architecture",
            "服务页使用客户业务标题",
        ),
        (
            "chat-business-heading",
            "information_architecture",
            "对话页使用智能售后助手标题",
        ),
        ("global-demo-disclosure", "product_language", "全局只用简洁环境披露"),
        (
            "historical-term-normalizer",
            "product_language",
            "历史消息研发术语转换为业务语言",
        ),
        ("refund-confirmation-language", "product_language", "退款动作明确为演示确认"),
        ("l2-ai-identity-disclosed", "product_language", "二线服务明确披露 AI 身份"),
        (
            "l2-internals-not-rendered",
            "product_language",
            "客户二线记录不公开工具模型和 Token",
        ),
        (
            "memory-business-language",
            "product_language",
            "长期偏好使用客户可理解的中文枚举",
        ),
        ("catalog-fixed-content", "business_truth", "固定目录提供完整商品和订单内容"),
        ("catalog-local-assets", "business_truth", "商品图片来自同源版本化资源"),
        ("catalog-seed-all-scenarios", "business_truth", "CLI 幂等初始化全部十个场景"),
        ("business-schema-unchanged", "business_truth", "视觉重构不新增业务迁移"),
        (
            "fixed-demo-public-journey",
            "business_truth",
            "固定演示使用公开产品旅程创建状态",
        ),
        ("no-component-demo-fixtures", "business_truth", "客户组件不内置演示订单事实"),
        ("admin-navigation-grouped", "operations", "运营控制台按总览工作区质量分组"),
        (
            "admin-overview-three-domains",
            "operations",
            "运营概览区分业务质量与系统健康",
        ),
        ("admin-no-invented-trends", "operations", "运营概览不虚构趋势图"),
        ("admin-demo-target-visible", "operations", "演示数据页持续展示目标与影响"),
        ("admin-quality-read-only", "operations", "运行评估系统页面保持只读"),
        ("admin-purpose-in-chinese", "operations", "运营标准术语附中文用途"),
        ("three-fixed-viewports", "experience", "固定桌面平板移动三种视口"),
        (
            "twenty-four-screenshot-targets",
            "experience",
            "八页面三视口形成二十四个截图目标",
        ),
        ("customer-responsive-layout", "experience", "客户页面具有平板和移动断点"),
        ("admin-responsive-layout", "experience", "运营页面具有平板和移动断点"),
        ("keyboard-focus-visible", "experience", "交互控件具有清晰键盘焦点"),
        ("reduced-motion-supported", "experience", "降动效偏好得到统一响应"),
        ("product-image-alt-required", "experience", "商品图片必须传入替代文本"),
        (
            "version-evidence-documented",
            "evidence",
            "Spec Plan 量表截图与报告形成可追溯证据",
        ),
    )
    return tuple(
        CommercialCredibilityEvalScenario(
            scenario_id=scenario_id,
            category=category,
            description=description,
        )
        for scenario_id, category, description in definitions
    )


COMMERCIAL_CREDIBILITY_EVAL_SCENARIOS = _scenarios()


def _project_root() -> Path:
    """返回当前源码所属项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    """读取历史版本文本证据；文件被新版取代时返回空证据。"""

    path = _project_root() / relative
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _collect_information_architecture_evidence(evidence: dict[str, bool]) -> None:
    """收集客户信息架构和任务层级的源码证据。"""

    app = _read("frontend/src/app/App.tsx")
    home = _read("frontend/src/features/support/SupportHomePage.tsx")
    orders = _read("frontend/src/features/support/OrdersListPage.tsx")
    services = _read("frontend/src/features/support/ServicesPage.tsx")
    chat = _read("frontend/src/features/chat/ChatPage.tsx")
    evidence.update(
        {
            "customer-navigation-task-based": all(
                label in app for label in ("售后首页", "我的订单", "服务进度")
            ),
            "home-task-first-density": all(
                marker in home
                for marker in (
                    "taskStrip",
                    "attentionSummary",
                    "进行中的服务",
                    "我的订单",
                )
            )
            and "hero" not in home.lower(),
            "home-account-boundary": "environmentState" in home
            and "本地演示" in app
            and "accountName" in app,
            "orders-business-heading": "<h1>我的订单</h1>" in orders,
            "services-business-heading": "<h1>服务进度</h1>" in services,
            "chat-business-heading": "<h1>智能售后助手</h1>" in chat,
        }
    )


def _collect_product_language_evidence(evidence: dict[str, bool]) -> None:
    """收集客户文案、AI 披露和内部术语隔离证据。"""

    app = _read("frontend/src/app/App.tsx")
    copy = _read("frontend/src/features/support/customerCopy.ts")
    chat = _read("frontend/src/features/chat/ChatPage.tsx")
    l2_cards = _read("frontend/src/features/chat/L2Cards.tsx")
    rendered_internal_literals = (
        ">AI L2 CASE<",
        ">MEMORY PROPOSAL<",
        ">可用工具<",
        ">最大步骤<",
        " Token ·",
        '?? "Harness"',
    )
    evidence.update(
        {
            "global-demo-disclosure": "本地演示" in app and "demoBadge" in app,
            "historical-term-normalizer": all(
                term in copy
                for term in (
                    "Mock\\s*退款",
                    "演示退款",
                    "Provider",
                    "模型服务",
                    "LLM",
                    "智能服务",
                )
            ),
            "refund-confirmation-language": all(
                label in chat for label in ("演示退款确认", "暂不退款", "确认演示退款")
            ),
            "l2-ai-identity-disclosed": "AI 二线服务" in l2_cards
            and "preview.agent_identity" in l2_cards,
            "l2-internals-not-rendered": not any(
                marker in l2_cards for marker in rendered_internal_literals
            ),
            "memory-business-language": all(
                label in l2_cards
                for label in ("回复语言", "回复详细程度", "沟通语气", "服务偏好建议")
            ),
        }
    )


def _collect_business_truth_evidence(evidence: dict[str, bool]) -> None:
    """收集固定目录、初始化、迁移和前端事实来源证据。"""

    root = _project_root()
    summary = DemoCatalogService(project_root=root).summary()
    cli = _read("src/commerce_resolve/cli.py")
    readme = _read("README.md")
    support_pages = "\n".join(
        _read(path)
        for path in (
            "frontend/src/features/support/SupportHomePage.tsx",
            "frontend/src/features/support/OrdersListPage.tsx",
            "frontend/src/features/support/OrderDetailPage.tsx",
            "frontend/src/features/support/ServicesPage.tsx",
            "frontend/src/features/support/ServiceDetailPage.tsx",
        )
    )
    migrations = tuple((root / "migrations/versions").glob("*.py"))
    evidence.update(
        {
            "catalog-fixed-content": summary.product_count >= 12
            and summary.sku_count >= 18
            and summary.scenario_count == 10,
            "catalog-local-assets": all(
                not item.image_ref.startswith(("http://", "https://"))
                for item in summary.products
            ),
            "catalog-seed-all-scenarios": "for item in summary.scenarios" in cli
            and 'choices=("commercial-service",)' in cli,
            "business-schema-unchanged": len(migrations) == 8
            and any("20260722_0008" in item.name for item in migrations),
            "fixed-demo-public-journey": all(
                marker in readme
                for marker in ("demo-catalog seed", "--scenario-set commercial-service")
            ),
            "no-component-demo-fixtures": "ORD-V13-" not in support_pages
            and "mock_payments" not in support_pages,
        }
    )


def _collect_operations_evidence(evidence: dict[str, bool]) -> None:
    """收集运营信息架构、数据来源和只读边界证据。"""

    layout = _read("frontend/src/app/AdminLayout.tsx")
    pages = _read("frontend/src/features/admin/AdminPages.tsx")
    routes = _read("src/commerce_resolve/web/routes/admin.py")
    evidence.update(
        {
            "admin-navigation-grouped": all(
                label in layout for label in ("总览", "工作区", "质量与诊断")
            ),
            "admin-overview-three-domains": all(
                label in pages for label in ("业务工作区", "Agent 质量", "系统健康")
            ),
            "admin-no-invented-trends": "趋势" not in pages and "<svg" not in pages,
            "admin-demo-target-visible": all(
                label in pages for label in ("目标客户", "影响范围", "目标订单")
            ),
            "admin-quality-read-only": all(
                marker not in routes
                for marker in (
                    '@router.post("/runs',
                    '@router.post("/eval',
                    '@router.post("/system',
                )
            ),
            "admin-purpose-in-chinese": all(
                label in pages for label in ("Agent 运行监控", "质量评估", "系统状态")
            ),
        }
    )


def _collect_experience_evidence(evidence: dict[str, bool]) -> None:
    """收集响应式、键盘、替代文本和截图目标证据。"""

    manifest = _read("docs/product/v1.3.1-screenshot-manifest.md")
    global_css = _read("frontend/src/styles/global.css")
    support_css = _read("frontend/src/features/support/Support.module.css")
    admin_css = _read("frontend/src/features/admin/Admin.module.css")
    thumbnail = _read("frontend/src/features/support/ProductThumbnail.tsx")
    evidence.update(
        {
            "three-fixed-viewports": all(
                viewport in manifest
                for viewport in ("1440×1000", "1024×900", "390×844")
            ),
            "twenty-four-screenshot-targets": "8 × 3 = 24" in manifest
            and sum(1 for line in manifest.splitlines() if line.startswith("| `")) == 8,
            "customer-responsive-layout": "@media (max-width: 1000px)" in support_css
            and "@media (max-width: 700px)" in support_css,
            "admin-responsive-layout": "@media (max-width: 980px)" in admin_css
            and "@media (max-width: 760px)" in admin_css,
            "keyboard-focus-visible": ":focus-visible" in global_css,
            "reduced-motion-supported": "prefers-reduced-motion: reduce" in global_css
            and "prefers-reduced-motion: reduce" in support_css,
            "product-image-alt-required": "alt: string;" in thumbnail
            and "alt={alt}" in thumbnail,
        }
    )


def _collect_evidence() -> dict[str, bool]:
    """收集不依赖网络、真实模型或真实支付的固定证据。"""

    evidence: dict[str, bool] = {}
    _collect_information_architecture_evidence(evidence)
    _collect_product_language_evidence(evidence)
    _collect_business_truth_evidence(evidence)
    _collect_operations_evidence(evidence)
    _collect_experience_evidence(evidence)
    evidence["version-evidence-documented"] = all(
        (_project_root() / path).is_file()
        for path in (
            "docs/specs/v1.3.1-commercial-product-credibility.md",
            "docs/plans/v1.3.1-commercial-product-credibility-plan.md",
            "docs/product/v1.3.1-review-rubric.md",
            "docs/product/v1.3.1-screenshot-manifest.md",
            "docs/eval/v1.3.1-report.md",
        )
    )
    return evidence


def run_commercial_credibility_eval_suite(
    *, forced_failure: str | None = None
) -> CommercialCredibilityEvalReport:
    """运行 32 条固定场景，并允许测试注入稳定失败 ID。"""

    evidence = _collect_evidence()
    results: list[CommercialCredibilityEvalResult] = []
    for scenario in COMMERCIAL_CREDIBILITY_EVAL_SCENARIOS:
        passed = evidence.get(scenario.scenario_id, False)
        if scenario.scenario_id == forced_failure:
            passed = False
        results.append(
            CommercialCredibilityEvalResult(
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
    return CommercialCredibilityEvalReport(
        suite="v1.3.1-commercial-product-credibility",
        total_scenarios=len(results),
        passed_scenarios=passed_count,
        passed=passed_count == len(results) and violations == 0,
        category_counts=dict(Counter(item.category for item in results)),
        commercial_credibility_safety_violations=violations,
        results=tuple(results),
    )
