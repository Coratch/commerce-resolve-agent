"""验证统一 Eval Run、Artifact、Baseline 和 Candidate 比较。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from commerce_resolve.eval_catalog import registered_adapters
from commerce_resolve.eval_models import EvalBaseline, EvalRunReport
from commerce_resolve.eval_runtime import (
    accept_baseline,
    assert_no_sensitive_artifact,
    compare_with_baseline,
    read_baseline,
    read_run_report,
    result_fingerprint,
    run_offline_evaluation,
    source_fingerprint,
    status_exit_code,
    write_run_artifact,
)


def _report(project_root: Path) -> EvalRunReport:
    """只运行最小 v0.1 Suite，构造快速且真实的统一报告。"""

    return run_offline_evaluation(
        project_root,
        suite_versions=("v0.1",),
        run_id="test-run",
    )


def _baseline(report: EvalRunReport, path: Path) -> EvalBaseline:
    """为比较测试创建固定时间的有效 Baseline。"""

    return accept_baseline(
        report,
        path,
        reason="测试初始基线",
        accepted_at=datetime(2026, 7, 21, tzinfo=UTC),
    )


def test_offline_run_is_repeatable_and_artifact_is_readable(tmp_path: Path) -> None:
    """验证运行元数据变化不会改变规范化业务结果。"""

    project_root = Path(__file__).parents[1]
    first = _report(project_root)
    second = run_offline_evaluation(
        project_root,
        suite_versions=("v0.1",),
        run_id="another-run",
    )
    assert first.result_fingerprint == second.result_fingerprint
    assert first.manifest.source_fingerprint == second.manifest.source_fingerprint
    run_dir = write_run_artifact(first, tmp_path)
    assert read_run_report(run_dir) == first
    assert "test-run" in (run_dir / "report.md").read_text("utf-8")


def test_all_runs_the_v20_active_release_profile() -> None:
    """验证 all 不会重新启用已被 v2.0 明确取代的历史产品契约。"""

    report = run_offline_evaluation(Path(__file__).parents[1])

    assert report.status == "passed"
    assert report.manifest.profile_version == "v2.0"
    assert tuple(suite.suite_version for suite in report.suites) == (
        "v0.1",
        "v0.2",
        "v0.4",
        "v0.5",
        "v0.6",
        "v0.7",
        "v0.8",
        "v1.0",
        "v2.0",
    )
    assert report.aggregate_metrics["scenario_total"] == 265


def test_source_fingerprint_tracks_demo_catalog_and_public_assets(
    tmp_path: Path,
) -> None:
    """验证目录事实与本地商品资源变化会使离线发布指纹失效。"""

    catalog = tmp_path / "data/demo/v1.3/catalog.json"
    asset = tmp_path / "frontend/public/catalog/v1.3/product.webp"
    catalog.parent.mkdir(parents=True)
    asset.parent.mkdir(parents=True)
    catalog.write_text('{"version":"v1.3"}', encoding="utf-8")
    asset.write_bytes(b"first")
    initial = source_fingerprint(tmp_path)
    asset.write_bytes(b"changed")
    assert source_fingerprint(tmp_path) != initial


def test_artifact_rejects_sensitive_fields_and_existing_run(tmp_path: Path) -> None:
    """验证 Artifact 不泄露密钥类字段且不覆盖既有 Run。"""

    report = _report(Path(__file__).parents[1])
    write_run_artifact(report, tmp_path)
    with pytest.raises(FileExistsError):
        write_run_artifact(report, tmp_path)
    with pytest.raises(ValueError, match="禁止字段"):
        assert_no_sensitive_artifact({"llm_api_key": "not-written"})
    with pytest.raises(ValueError, match="敏感值"):
        assert_no_sensitive_artifact({"value": "/Users/example/private"})


def test_baseline_requires_explicit_passing_candidate(tmp_path: Path) -> None:
    """验证失败 Run、空原因和隐式覆盖都不能移动 Baseline。"""

    report = _report(Path(__file__).parents[1])
    output = tmp_path / "baseline.json"
    with pytest.raises(ValueError, match="非空原因"):
        accept_baseline(report, output, reason=" ")
    failed = report.model_copy(update={"status": "failed"})
    with pytest.raises(ValueError, match="完整通过"):
        accept_baseline(failed, output, reason="失败候选")
    baseline = _baseline(report, output)
    assert read_baseline(output) == baseline
    with pytest.raises(FileExistsError):
        accept_baseline(report, output, reason="不能隐式覆盖")


def test_replace_baseline_records_superseded_id(tmp_path: Path) -> None:
    """验证显式替换会保留前一 Baseline 标识和新原因。"""

    report = _report(Path(__file__).parents[1])
    output = tmp_path / "baseline.json"
    first = _baseline(report, output)
    second = accept_baseline(
        report,
        output,
        reason="已审核的新基线",
        replace=True,
        accepted_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert second.supersedes_baseline_id == first.baseline_id
    assert second.acceptance_reason == "已审核的新基线"


def test_compare_identifies_regression_new_fixed_and_removed(tmp_path: Path) -> None:
    """验证比较按全局 ID 对齐，不依赖场景数组位置。"""

    report = _report(Path(__file__).parents[1])
    baseline = _baseline(report, tmp_path / "baseline.json")
    suite = report.suites[0]
    first = suite.scenarios[0].model_copy(update={"passed": False})
    regression_suite = suite.model_copy(
        update={
            "passed": False,
            "passed_scenarios": suite.passed_scenarios - 1,
            "scenarios": (first, *suite.scenarios[1:]),
        }
    )
    regression = report.model_copy(
        update={
            "status": "failed",
            "suites": (regression_suite,),
            "result_fingerprint": result_fingerprint((regression_suite,)),
        }
    )
    comparison = compare_with_baseline(regression, baseline)
    assert comparison.status == "failed"
    assert any(item.change == "regression" for item in comparison.scenario_changes)

    removed_suite = suite.model_copy(
        update={
            "total_scenarios": suite.total_scenarios - 1,
            "passed_scenarios": suite.passed_scenarios - 1,
            "scenarios": suite.scenarios[1:],
        }
    )
    removed = report.model_copy(update={"suites": (removed_suite,)})
    assert compare_with_baseline(removed, baseline).status == "incomparable"


def test_comparison_and_status_codes_cover_all_terminal_states(tmp_path: Path) -> None:
    """验证 Profile 不兼容和四种稳定退出码。"""

    report = _report(Path(__file__).parents[1])
    baseline = _baseline(report, tmp_path / "baseline.json")
    incompatible_manifest = report.manifest.model_copy(
        update={"profile_id": "different-profile"}
    )
    incompatible = report.model_copy(update={"manifest": incompatible_manifest})
    assert compare_with_baseline(incompatible, baseline).status == "incomparable"
    assert status_exit_code("passed") == 0
    assert status_exit_code("failed") == 1
    assert status_exit_code("incomparable") == 2
    assert status_exit_code("incomplete") == 3
    assert status_exit_code("unknown") == 4


def test_result_fingerprint_ignores_time_and_duration_metrics() -> None:
    """验证时间和效率抖动不会伪造离线业务结果差异。"""

    adapter = registered_adapters()[0]
    suite = adapter.run()
    first = suite.scenarios[0]
    changed = first.model_copy(
        update={"metrics": {**first.metrics, "duration_ms": 99999}}
    )
    changed_suite = suite.model_copy(
        update={"scenarios": (changed, *suite.scenarios[1:])}
    )
    assert result_fingerprint((suite,)) == result_fingerprint((changed_suite,))
