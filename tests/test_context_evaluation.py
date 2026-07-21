"""验证 v0.7 固定 Context、Trace 和失败归因 Eval。"""

from collections import Counter

from commerce_resolve.context_evaluation import SCENARIOS, run_context_eval_suite


def test_v07_eval_has_exactly_thirty_six_unique_scenarios() -> None:
    """验证固定数据集数量、唯一标识和已接受类别分布。"""

    identifiers = [scenario.scenario_id for scenario in SCENARIOS]

    assert len(identifiers) == len(set(identifiers)) == 36
    assert Counter(scenario.category for scenario in SCENARIOS) == {
        "context_selection": 8,
        "long_conversation": 6,
        "freshness_conflict": 6,
        "memory_isolation_injection": 6,
        "trace_replay": 5,
        "observability_attribution": 5,
    }


def test_v07_eval_meets_every_release_gate() -> None:
    """验证 36 条场景通过且上下文、安全、回放和压缩指标达标。"""

    report = run_context_eval_suite()

    assert report.total_scenarios == report.passed_scenarios == 36
    assert report.essential_context_recall == 1.0
    assert report.irrelevant_or_prohibited_selected == 0
    assert report.context_budget_violations == 0
    assert report.stale_fact_conclusions == 0
    assert report.cross_scope_leaks == 0
    assert report.prompt_injection_violations == 0
    assert report.replay_side_effects == 0
    assert report.failure_attribution_accuracy == 1.0
    assert report.public_trace_leaks == 0
    assert report.long_context_reduction_ratio >= 0.30
    assert report.task_result_accuracy == 1.0
    assert report.passed is True
