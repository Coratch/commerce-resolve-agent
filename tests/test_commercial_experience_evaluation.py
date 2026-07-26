"""验证 v1.3 商业化售后体验固定 Eval。"""

from commerce_resolve.commercial_experience_evaluation import (
    COMMERCIAL_EXPERIENCE_EVAL_SCENARIOS,
    run_commercial_experience_eval_suite,
)


def test_commercial_experience_suite_preserves_superseded_scenarios() -> None:
    """验证历史商业体验 Suite 如实识别 v2.0 数据与版本契约漂移。"""

    report = run_commercial_experience_eval_suite()

    assert len(COMMERCIAL_EXPERIENCE_EVAL_SCENARIOS) == 48
    assert report.total_scenarios == 48
    assert report.passed_scenarios == 45
    assert report.commercial_experience_safety_violations == 0
    assert not report.passed
    assert {result.scenario_id for result in report.results if not result.passed} == {
        "catalog-seed-idempotent",
        "admin-seed-audited",
        "release-contract-v13",
    }


def test_commercial_experience_suite_propagates_forced_failure() -> None:
    """验证任一场景失败会阻断 Suite 通过。"""

    baseline = run_commercial_experience_eval_suite()
    report = run_commercial_experience_eval_suite(
        forced_failure="guidance-no-refund-write"
    )

    assert report.passed_scenarios == baseline.passed_scenarios - 1
    assert not report.passed
