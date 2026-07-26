"""验证 v1.3.1 商业产品可信度的固定离线 Eval。"""

from commerce_resolve.commercial_credibility_evaluation import (
    COMMERCIAL_CREDIBILITY_EVAL_SCENARIOS,
    run_commercial_credibility_eval_suite,
)


def test_commercial_credibility_suite_preserves_superseded_scenarios() -> None:
    """验证历史可信度 Suite 在旧页面删除后返回稳定失败而非异常。"""

    report = run_commercial_credibility_eval_suite()

    assert len(COMMERCIAL_CREDIBILITY_EVAL_SCENARIOS) == 32
    assert report.total_scenarios == 32
    assert report.passed_scenarios == 26
    assert report.commercial_credibility_safety_violations == 0
    assert report.passed is False


def test_commercial_credibility_suite_detects_injected_failure() -> None:
    """验证固定证据缺失会使 Suite 失败而非恒真通过。"""

    baseline = run_commercial_credibility_eval_suite()
    report = run_commercial_credibility_eval_suite(
        forced_failure="l2-internals-not-rendered"
    )

    assert report.passed is False
    assert report.passed_scenarios == baseline.passed_scenarios - 1
    failed = [result for result in report.results if not result.passed]
    assert "l2-internals-not-rendered" in {result.scenario_id for result in failed}
