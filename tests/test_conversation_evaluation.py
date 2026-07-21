"""验证 v0.6 固定会话 Eval 的数量、类别和发布门槛。"""

from collections import Counter

from commerce_resolve.conversation_evaluation import (
    SCENARIOS,
    run_conversation_eval_suite,
)


def test_v06_eval_has_exactly_thirty_two_unique_scenarios() -> None:
    """验证固定数据集规模、唯一标识和已接受类别分布。"""

    identifiers = [scenario.scenario_id for scenario in SCENARIOS]

    assert len(identifiers) == len(set(identifiers)) == 32
    assert Counter(scenario.category for scenario in SCENARIOS) == {
        "history_recovery": 6,
        "lifecycle": 6,
        "identity_isolation": 5,
        "idempotency_concurrency": 5,
        "pending_action": 4,
        "sse_failure_recovery": 4,
        "data_compatibility": 2,
    }


def test_v06_eval_meets_all_release_gates() -> None:
    """验证 32 条场景全部通过且消息、身份和公开数据违规为零。"""

    report = run_conversation_eval_suite()

    assert report.total_scenarios == report.passed_scenarios == 32
    assert report.duplicate_messages == 0
    assert report.duplicate_runs == 0
    assert report.duplicate_events == 0
    assert report.cross_identity_leaks == 0
    assert report.public_data_leaks == 0
    assert report.lost_messages == 0
    assert report.passed is True
