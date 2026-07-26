"""使用 24 条确定性场景验证 v1.3.2 沉浸式界面契约。"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class ImmersiveInterfaceEvalScenario(BaseModel):
    """描述一条沉浸式界面固定场景及其预期终态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    description: str
    expected_status: str = "passed"


class ImmersiveInterfaceEvalResult(BaseModel):
    """保存单条界面场景的确定性结果和安全违规。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    passed: bool
    expected_status: str
    actual_status: str
    error_type: str | None = None
    safety_violations: tuple[str, ...] = ()


class ImmersiveInterfaceEvalReport(BaseModel):
    """汇总 v1.3.2 的 24 条沉浸式界面 Eval。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    total_scenarios: int
    passed_scenarios: int
    passed: bool
    category_counts: dict[str, int]
    immersive_interface_safety_violations: int
    results: tuple[ImmersiveInterfaceEvalResult, ...]


def _scenarios() -> tuple[ImmersiveInterfaceEvalScenario, ...]:
    """按图标、动效、艺术方向、可访问性和边界返回固定场景。"""

    definitions = (
        ("lucide-pinned-dependency", "icons", "Lucide 以固定版本安装"),
        ("customer-shell-lucide", "icons", "客户外壳使用 Lucide 导航图标"),
        ("support-actions-lucide", "icons", "售后任务动作使用 Lucide 图标"),
        ("admin-shell-lucide", "icons", "运营控制台使用 Lucide 模块图标"),
        ("visible-emoji-zero", "icons", "产品 TSX 可见源码不含 Emoji"),
        ("character-icon-zero", "icons", "产品 TSX 不使用字符箭头或勾选图标"),
        ("single-canvas-component", "motion", "全局生成式视觉由单一 Canvas 组件提供"),
        ("single-animation-loop", "motion", "Canvas 只声明一个 RAF 循环"),
        ("canvas-dpr-capped", "motion", "Canvas 设备像素比具有上限"),
        ("canvas-visibility-pause", "motion", "页面隐藏时停止连续动画"),
        ("canvas-reduced-motion", "motion", "系统降动效时只绘制静态帧"),
        ("canvas-does-not-block-input", "motion", "Canvas 不接收指针事件"),
        ("editorial-color-system", "art_direction", "全局使用墨色纸色青柠和信号橙"),
        ("editorial-home-typography", "art_direction", "首页使用大字和错位标题"),
        ("asymmetric-customer-grid", "art_direction", "客户任务区采用非对称网格"),
        ("operations-control-room", "art_direction", "运营控制台采用独立控制室表达"),
        (
            "chat-remains-task-workspace",
            "art_direction",
            "对话页保留会话和上下文工作区",
        ),
        ("auth-shares-brand-system", "art_direction", "认证页复用品牌色与排版"),
        ("keyboard-focus-visible", "accessibility", "全局交互控件具有焦点样式"),
        ("decorative-icons-hidden", "accessibility", "装饰 Lucide 图标隐藏于无障碍树"),
        ("three-fixed-viewports", "accessibility", "E2E 固定桌面平板移动视口"),
        ("route-scroll-reset", "accessibility", "路由切换不会继承遮挡标题的滚动位置"),
        ("business-schema-unchanged", "boundary", "视觉重构不新增业务迁移"),
        ("version-evidence-documented", "boundary", "Spec Plan 截图清单和报告形成证据"),
    )
    return tuple(
        ImmersiveInterfaceEvalScenario(
            scenario_id=scenario_id,
            category=category,
            description=description,
        )
        for scenario_id, category, description in definitions
    )


IMMERSIVE_INTERFACE_EVAL_SCENARIOS = _scenarios()


def _project_root() -> Path:
    """返回当前源码所属项目根目录。"""

    return Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    """读取历史版本文本证据；文件被新版取代时返回空证据。"""

    path = _project_root() / relative
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _product_tsx() -> str:
    """合并非测试 TSX，用于检查可见产品源码中的字符图标。"""

    root = _project_root() / "frontend/src"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.tsx"))
        if ".test." not in path.name
    )


def _collect_evidence() -> dict[str, bool]:
    """收集 24 条界面契约的源码、配置和文档证据。"""

    root = _project_root()
    package = _read("frontend/package.json")
    app = _read("frontend/src/app/App.tsx")
    canvas = _read("frontend/src/components/InteractiveField.tsx")
    canvas_css = _read("frontend/src/components/InteractiveField.module.css")
    global_css = _read("frontend/src/styles/global.css")
    home = _read("frontend/src/features/support/SupportHomePage.tsx")
    support_css = _read("frontend/src/features/support/Support.module.css")
    admin = _read("frontend/src/app/AdminLayout.tsx")
    admin_css = _read("frontend/src/features/admin/Admin.module.css")
    chat = _read("frontend/src/features/chat/ChatPage.tsx")
    auth_css = _read("frontend/src/features/auth/Auth.module.css")
    e2e = _read("frontend/e2e/v1.3.2-immersive-interface.spec.ts")
    product_tsx = _product_tsx()
    pictograph_pattern = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf]")
    character_icons = ("→", "←", "↗", "↘", "↔", "✓", "✔")
    migrations = tuple((root / "migrations/versions").glob("*.py"))
    evidence_files = (
        "docs/specs/v1.3.2-immersive-commerce-interface.md",
        "docs/plans/v1.3.2-immersive-commerce-interface-plan.md",
        "docs/product/v1.3.2-screenshot-manifest.md",
        "docs/eval/v1.3.2-report.md",
    )
    return {
        "lucide-pinned-dependency": '"lucide-react": "1.26.0"' in package,
        "customer-shell-lucide": 'from "lucide-react"' in app
        and all(name in app for name in ("Orbit", "Boxes", "ClipboardList")),
        "support-actions-lucide": 'from "lucide-react"' in home
        and all(name in home for name in ("PackageSearch", "RotateCcw", "Bot")),
        "admin-shell-lucide": 'from "lucide-react"' in admin
        and all(name in admin for name in ("LayoutDashboard", "Activity", "ServerCog")),
        "visible-emoji-zero": pictograph_pattern.search(product_tsx) is None,
        "character-icon-zero": not any(
            character in product_tsx for character in character_icons
        ),
        "single-canvas-component": app.count("<InteractiveField") == 1
        and admin.count("<InteractiveField") == 1
        and product_tsx.count("<canvas") == 1,
        "single-animation-loop": canvas.count("requestAnimationFrame(schedule)") == 1,
        "canvas-dpr-capped": "Math.min(window.devicePixelRatio || 1, 1.5)" in canvas,
        "canvas-visibility-pause": "visibilitychange" in canvas
        and 'document.visibilityState !== "visible"' in canvas,
        "canvas-reduced-motion": "prefers-reduced-motion: reduce" in canvas
        and "renderedStaticFrame" in canvas,
        "canvas-does-not-block-input": "pointer-events: none" in canvas_css,
        "editorial-color-system": all(
            token in global_css
            for token in (
                "--electric: #c5ff48",
                "--accent: #ff5b34",
                "--night: #071914",
            )
        ),
        "editorial-home-typography": "introSequence" in home
        and "font-size: clamp(3.1rem, 7.6vw, 8.1rem)" in support_css,
        "asymmetric-customer-grid": "grid-template-columns: 1.15fr 0.9fr 1fr"
        in support_css,
        "operations-control-room": 'variant="admin"' in admin
        and 'content: "OPS"' in admin_css,
        "chat-remains-task-workspace": all(
            marker in chat
            for marker in (
                "conversation-sidebar",
                "chat-workspace",
                "ConversationContextPanel",
            )
        ),
        "auth-shares-brand-system": all(
            marker in auth_css
            for marker in (
                "var(--electric)",
                "var(--night)",
                "var(--display)",
            )
        ),
        "keyboard-focus-visible": all(
            selector in global_css
            for selector in (
                "a:focus-visible",
                "button:focus-visible",
                "textarea:focus-visible",
            )
        ),
        "decorative-icons-hidden": app.count('aria-hidden="true"') >= 8
        and admin.count('aria-hidden="true"') >= 4,
        "three-fixed-viewports": all(
            marker in e2e for marker in ("1440", "1024", "390")
        ),
        "route-scroll-reset": "ScrollToTop" in app
        and 'window.scrollTo({ top: 0, behavior: "auto" })' in app,
        "business-schema-unchanged": len(migrations) == 8
        and any("20260722_0008" in item.name for item in migrations),
        "version-evidence-documented": all(
            (root / path).is_file() for path in evidence_files
        ),
    }


def run_immersive_interface_eval_suite(
    *, forced_failure: str | None = None
) -> ImmersiveInterfaceEvalReport:
    """运行 24 条固定场景，并允许测试注入稳定失败 ID。"""

    evidence = _collect_evidence()
    results: list[ImmersiveInterfaceEvalResult] = []
    for scenario in IMMERSIVE_INTERFACE_EVAL_SCENARIOS:
        passed = evidence.get(scenario.scenario_id, False)
        if scenario.scenario_id == forced_failure:
            passed = False
        results.append(
            ImmersiveInterfaceEvalResult(
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
    return ImmersiveInterfaceEvalReport(
        suite="v1.3.2-immersive-commerce-interface",
        total_scenarios=len(results),
        passed_scenarios=passed_count,
        passed=passed_count == len(results) and violations == 0,
        category_counts=dict(Counter(item.category for item in results)),
        immersive_interface_safety_violations=violations,
        results=tuple(results),
    )
