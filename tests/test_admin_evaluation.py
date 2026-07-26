"""验证 v1.2 双产品表面固定 Eval 与统一 Catalog 接入。"""

from commerce_resolve.admin_evaluation import (
    ADMIN_SURFACE_EVAL_SCENARIOS,
    run_admin_surface_eval_suite,
)
from commerce_resolve.eval_catalog import find_adapter


def test_admin_surface_eval_preserves_the_superseded_40_scenarios() -> None:
    """验证历史后台 Suite 保留场景，并识别 v2.0 删除的订单 CRUD。"""

    scenario_ids = [item.scenario_id for item in ADMIN_SURFACE_EVAL_SCENARIOS]
    report = run_admin_surface_eval_suite()

    assert len(scenario_ids) == len(set(scenario_ids)) == 40
    assert report.category_counts == {
        "role": 8,
        "operations": 10,
        "monitoring": 8,
        "readiness": 8,
        "surface": 6,
    }
    assert report.total_scenarios == 40
    assert report.passed_scenarios == 35
    assert report.passed is False
    assert report.admin_surface_safety_violations == 0
    assert {result.scenario_id for result in report.results if not result.passed} == {
        "admin-target-customer-explicit",
        "admin-reuses-business-repository",
        "order-update-delete-audited",
        "payment-write-audited",
        "legacy-demo-route-safe-redirect",
    }


def test_admin_surface_eval_failure_is_not_hidden() -> None:
    """验证注入固定失败时 Suite 与 Catalog Adapter 都保留失败。"""

    baseline = run_admin_surface_eval_suite()
    report = run_admin_surface_eval_suite(forced_failure="admin-api-server-guarded")
    adapter = find_adapter("v1.2")

    assert report.passed is False
    assert report.passed_scenarios == baseline.passed_scenarios - 1
    assert adapter.descriptor().suite_id == "v1.2-customer-admin-surfaces"
    assert len(adapter.descriptor().scenarios) == 40
