"""验证 v0.8 Eval Harness 的 40 条元场景和故障注入。"""

from commerce_resolve.eval_system_evaluation import (
    SCENARIOS,
    run_eval_system_suite,
)


def test_eval_system_suite_has_exactly_40_passing_scenarios() -> None:
    """验证 Harness 自检精确覆盖 Plan 中的 40 项职责。"""

    report = run_eval_system_suite()
    assert len(SCENARIOS) == 40
    assert report.total_scenarios == 40
    assert report.passed_scenarios == 40
    assert report.safety_gate_failures == 0
    assert report.passed is True


def test_eval_system_fault_injection_cannot_be_hidden_by_average() -> None:
    """验证单条安全门禁失败会阻断整套结果。"""

    report = run_eval_system_suite(forced_failure="safety-refund")
    assert report.passed is False
    assert report.passed_scenarios == 39
    assert report.safety_gate_failures == 1
    failed = next(item for item in report.results if not item.passed)
    assert failed.scenario_id == "safety-refund"
    assert failed.safety_violations
