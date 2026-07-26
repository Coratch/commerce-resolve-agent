"""验证 v1.1 售后服务中心固定 Eval 与统一 Catalog 接入。"""

from commerce_resolve.eval_catalog import find_adapter
from commerce_resolve.service_center_evaluation import (
    SERVICE_CENTER_EVAL_SCENARIOS,
    run_service_center_eval_suite,
)


def test_service_center_eval_preserves_the_superseded_36_scenarios() -> None:
    """验证历史售后中心 Suite 保留场景并识别新悬浮 Agent 契约。"""

    scenario_ids = [item.scenario_id for item in SERVICE_CENTER_EVAL_SCENARIOS]
    report = run_service_center_eval_suite()

    assert len(scenario_ids) == len(set(scenario_ids)) == 36
    assert report.category_counts == {
        "orders": 8,
        "binding": 10,
        "services": 8,
        "ui": 6,
        "release": 4,
    }
    assert report.total_scenarios == 36
    assert report.passed_scenarios == 31
    assert report.passed is False
    assert report.service_center_safety_violations == 0


def test_service_center_eval_failure_is_not_hidden() -> None:
    """验证注入一个固定失败时 Suite 与 Catalog Adapter 都不掩盖结果。"""

    baseline = run_service_center_eval_suite()
    report = run_service_center_eval_suite(
        forced_failure="guest-overview-server-backed"
    )
    adapter = find_adapter("v1.1")

    assert report.passed is False
    assert report.passed_scenarios == baseline.passed_scenarios - 1
    assert adapter.descriptor().suite_id == "v1.1-post-purchase-service-center"
    assert len(adapter.descriptor().scenarios) == 36
