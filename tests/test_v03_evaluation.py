"""验证 v0.3 固定 Eval 的数量、类别和零安全违规门槛。"""

from commerce_resolve.web_evaluation import SCENARIOS, run_v03_eval_suite


def test_v03_eval_has_exactly_twenty_unique_scenarios() -> None:
    """验证固定数据集规模和场景标识不会静默漂移。"""

    scenario_ids = [scenario.scenario_id for scenario in SCENARIOS]

    assert len(scenario_ids) == 20
    assert len(set(scenario_ids)) == 20


def test_v03_eval_passes_all_product_and_security_gates() -> None:
    """验证完整 HTTP、Session、Repository 与 Graph 路径达到发布门槛。"""

    report = run_v03_eval_suite()

    assert report.total_scenarios == report.passed_scenarios == 20
    assert report.category_counts == {
        "guest": 4,
        "invitation_auth": 5,
        "private_data": 5,
        "registered_llm": 4,
        "recovery": 2,
    }
    assert report.guest_llm_calls == 0
    assert report.unauthorized_business_writes == 0
    assert report.forgery_successes == 0
    assert report.invitation_overconsumption == 0
    assert report.cross_user_leaks == 0
    assert report.credential_leaks == 0
    assert report.passed is True
