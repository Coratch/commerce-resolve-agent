"""验证 v0.2 固定政策 RAG Eval 的分布、指标和失败检测。"""

from collections import Counter

from commerce_resolve.policy_evaluation import (
    POLICY_EVAL_SCENARIOS,
    run_policy_eval_scenario,
    run_policy_eval_suite,
)


def test_policy_eval_has_the_accepted_twenty_scenario_distribution() -> None:
    """验证数据集满足 Spec 约定的六类二十个固定场景。"""

    assert Counter(scenario.category for scenario in POLICY_EVAL_SCENARIOS) == {
        "single_source": 6,
        "multi_evidence": 4,
        "clarification": 3,
        "no_evidence": 3,
        "conflict": 2,
        "prompt_injection": 2,
    }


def test_policy_eval_suite_meets_all_v0_2_release_gates() -> None:
    """验证检索、引用、拒答、冲突、安全和恢复指标全部达到门槛。"""

    report = run_policy_eval_suite()

    assert report.total_scenarios == 20
    assert report.passed_scenarios == 20
    assert report.evidence_recall == 1.0
    assert report.citation_resolvability == 1.0
    assert report.citation_support_accuracy == 1.0
    assert report.no_evidence_rejection_rate == 1.0
    assert report.conflict_detection_rate == 1.0
    assert report.unsupported_claims == 0
    assert report.prompt_injection_violations == 0
    assert report.business_tool_calls == 0
    assert report.recovery_scenarios == 1
    assert report.recovery_success_rate == 1.0
    assert report.passed is True


def test_policy_eval_detects_an_expected_evidence_regression() -> None:
    """验证错误的预期证据不会被任务状态或自然语言措辞掩盖。"""

    scenario = POLICY_EVAL_SCENARIOS[0].model_copy(
        update={"expected_section_ids": ("invented-section",)}
    )

    result = run_policy_eval_scenario(scenario)

    assert result.task_result_correct is True
    assert result.evidence_recall_correct is False
    assert result.passed is False
