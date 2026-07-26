"""验证 v1.3 统一 Eval Catalog 和全部版本 Suite Adapter。"""

from dataclasses import replace

import pytest

from commerce_resolve.eval_catalog import (
    ACTIVE_RELEASE_SUITE_VERSIONS,
    ARCHIVED_SUITE_VERSIONS,
    active_release_adapters,
    build_eval_catalog,
    find_adapter,
    registered_adapters,
)


def test_catalog_preserves_all_versioned_scenarios() -> None:
    """验证历史场景和 v2.0 新增三十六条场景无丢失且全局唯一。"""

    catalog = build_eval_catalog()
    counts = {suite.suite_version: len(suite.scenarios) for suite in catalog.suites}
    scenario_ids = [
        scenario.scenario_id for suite in catalog.suites for scenario in suite.scenarios
    ]

    assert {
        key: value
        for key, value in counts.items()
        if key
        not in {
            "v0.8",
            "v1.0",
            "v1.1",
            "v1.2",
            "v1.3",
            "v1.3.1",
            "v1.3.2",
            "v2.0",
        }
    } == {
        "v0.1": 15,
        "v0.2": 20,
        "v0.3": 20,
        "v0.4": 24,
        "v0.5": 30,
        "v0.6": 32,
        "v0.7": 36,
    }
    assert counts["v0.8"] == 40
    assert counts["v1.0"] == 32
    assert counts["v1.1"] == 36
    assert counts["v1.2"] == 40
    assert counts["v1.3"] == 48
    assert counts["v1.3.1"] == 32
    assert counts["v1.3.2"] == 24
    assert counts["v2.0"] == 36
    assert sum(counts[f"v0.{version}"] for version in range(1, 8)) == 177
    assert len(scenario_ids) == 465
    assert len(scenario_ids) == len(set(scenario_ids))
    assert len(catalog.fingerprint) == 64


def test_current_release_gate_excludes_only_superseded_product_contracts() -> None:
    """验证 Catalog 保留历史 Suite，而 v2.0 发布门禁只运行兼容契约。"""

    active = tuple(adapter.suite_version for adapter in active_release_adapters())
    all_versions = tuple(adapter.suite_version for adapter in registered_adapters())

    assert active == ACTIVE_RELEASE_SUITE_VERSIONS
    assert set(active).isdisjoint(ARCHIVED_SUITE_VERSIONS)
    assert set(active) | set(ARCHIVED_SUITE_VERSIONS) == set(all_versions)


def test_catalog_rejects_duplicate_suite_and_scenario_ids() -> None:
    """验证重复 Suite 或场景在运行 Agent 前被拒绝。"""

    first = registered_adapters()[0]
    with pytest.raises(ValueError, match="suite_id"):
        build_eval_catalog((first, first))

    duplicate_scenario = replace(
        registered_adapters()[1],
        suite_id="duplicate-scenario-suite",
        suite_version=first.suite_version,
        scenarios=(first.scenarios[0],),
    )
    with pytest.raises(ValueError, match="scenario_id"):
        build_eval_catalog((first, duplicate_scenario))


def test_adapter_keeps_original_report_result_and_safety_metrics() -> None:
    """验证统一投影不会把旧 Suite 失败改成通过或遗漏安全指标。"""

    adapter = find_adapter("v0.1")
    outcome = adapter.run()

    assert outcome.suite_id == "v0.1-order-inquiry"
    assert outcome.total_scenarios == 15
    assert outcome.passed_scenarios == 15
    assert outcome.passed is True
    assert outcome.safety_violations == ()
    assert outcome.metrics["task_result_accuracy"] == 1.0
    assert len(outcome.scenarios) == 15
    assert all(item.scenario_id.startswith("v0.1/") for item in outcome.scenarios)


def test_find_adapter_rejects_unknown_suite() -> None:
    """验证未知 Suite 使用稳定配置错误而不是回退全部运行。"""

    with pytest.raises(ValueError, match="未知 Eval Suite"):
        find_adapter("v9.9")
