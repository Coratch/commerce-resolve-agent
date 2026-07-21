"""验证 v0.1 固定 Eval 场景、指标和失败检测。"""

from collections import Counter

from commerce_resolve.evaluation import (
    EVAL_SCENARIOS,
    run_eval_scenario,
    run_eval_suite,
)


def test_eval_suite_contains_the_accepted_scenario_distribution() -> None:
    """验证固定数据集满足 Feature Spec 约定的十五个场景。"""

    assert Counter(scenario.category for scenario in EVAL_SCENARIOS) == {
        "valid": 4,
        "missing_order_id": 3,
        "unavailable": 2,
        "unauthorized": 2,
        "tool_failure": 2,
        "unsupported": 2,
    }


def test_eval_suite_meets_v0_1_release_gate() -> None:
    """验证当前实现通过结果、工具、安全和恢复发布门槛。"""

    report = run_eval_suite()

    assert report.total_scenarios == 15
    assert report.passed_scenarios == 15
    assert report.task_result_accuracy == 1.0
    assert report.tool_selection_accuracy == 1.0
    assert report.tool_parameter_accuracy == 1.0
    assert report.safety_violations == 0
    assert report.unsupported_request_tool_calls == 0
    assert report.recovery_scenarios == 1
    assert report.recovery_success_rate == 1.0
    assert report.passed is True


def test_eval_scenario_detects_an_expected_result_regression() -> None:
    """验证 Eval 不会把与预期状态不一致的实现误判为通过。"""

    scenario = EVAL_SCENARIOS[0].model_copy(update={"expected_status": "unsupported"})

    result = run_eval_scenario(scenario)

    assert result.task_result_correct is False
    assert result.passed is False
