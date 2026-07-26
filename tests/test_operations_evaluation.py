"""验证 v1.0 单机交付 Suite 固定包含 32 条真实运维场景。"""

from commerce_resolve.operations_evaluation import run_operations_eval_suite


def test_operations_eval_suite_has_exactly_32_passing_scenarios() -> None:
    """验证场景数量、结果和零安全违规达到发布门槛。"""

    report = run_operations_eval_suite()

    assert report.total_scenarios == 32
    assert report.passed_scenarios == 32
    assert report.operational_safety_violations == 0
    assert report.passed is True


def test_operations_eval_suite_propagates_forced_failure() -> None:
    """验证任一失败不能被其他通过场景平均抵消。"""

    report = run_operations_eval_suite(forced_failure="single-instance-lock-enforced")

    assert report.passed is False
    assert report.passed_scenarios == 31
