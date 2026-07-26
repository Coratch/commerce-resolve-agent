"""验证 v1.3.2 沉浸式界面的固定离线 Eval。"""

from commerce_resolve.immersive_interface_evaluation import (
    IMMERSIVE_INTERFACE_EVAL_SCENARIOS,
    run_immersive_interface_eval_suite,
)


def test_immersive_interface_suite_preserves_superseded_scenarios() -> None:
    """验证旧沉浸界面 Suite 在页面被取代后返回稳定失败而非异常。"""

    report = run_immersive_interface_eval_suite()

    assert len(IMMERSIVE_INTERFACE_EVAL_SCENARIOS) == 24
    assert report.total_scenarios == 24
    assert report.passed_scenarios == 22
    assert report.immersive_interface_safety_violations == 0
    assert report.passed is False


def test_immersive_interface_suite_detects_injected_failure() -> None:
    """验证固定证据缺失会使 Suite 失败而非恒真通过。"""

    baseline = run_immersive_interface_eval_suite()
    report = run_immersive_interface_eval_suite(
        forced_failure="canvas-visibility-pause"
    )

    assert report.passed is False
    assert report.passed_scenarios == baseline.passed_scenarios - 1
    failed = [result for result in report.results if not result.passed]
    assert "canvas-visibility-pause" in {result.scenario_id for result in failed}
