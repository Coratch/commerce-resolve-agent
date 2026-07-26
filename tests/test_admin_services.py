"""验证运营 Eval Reader 的四态、固定根目录与路径安全。"""

from pathlib import Path

from commerce_resolve.admin_services import AdminEvalReader
from commerce_resolve.eval_runtime import (
    accept_baseline,
    run_offline_evaluation,
    write_run_artifact,
)


def _passing_report(project_root: Path, run_id: str):
    """运行最小真实离线 Suite，返回可持久化的通过 Candidate。"""

    return run_offline_evaluation(
        project_root,
        suite_versions=("v0.1",),
        run_id=run_id,
    )


def test_admin_eval_reader_distinguishes_four_states(tmp_path: Path) -> None:
    """验证缺失、不兼容、失败和通过均具有稳定且可重复的投影。"""

    runs = tmp_path / "runs"
    baseline_path = tmp_path / "baseline.json"
    reader = AdminEvalReader(runs, baseline_path)
    assert reader.latest().state == "missing"

    report = _passing_report(Path.cwd(), "candidate-pass")
    run_path = write_run_artifact(report, runs)
    incompatible = reader.read(run_path.name)
    assert incompatible.state == "incompatible"
    assert incompatible.compatibility_reasons == ("baseline_missing",)

    accept_baseline(report, baseline_path, reason="v1.2 Reader 测试基线")
    passed = reader.read(run_path.name)
    assert passed.state == "passed"
    assert passed.safety_violation_count == 0

    failed_report = report.model_copy(
        update={
            "manifest": report.manifest.model_copy(
                update={"run_id": "candidate-failed"}
            ),
            "status": "failed",
        }
    )
    failed_path = write_run_artifact(failed_report, runs)
    assert reader.read(failed_path.name).state == "failed"


def test_admin_eval_reader_rejects_path_and_corrupt_artifact(tmp_path: Path) -> None:
    """验证客户端不能越过固定根目录，损坏文件只返回不兼容状态。"""

    runs = tmp_path / "runs"
    corrupt = runs / "corrupt-run"
    corrupt.mkdir(parents=True)
    (corrupt / "results.json").write_text("not-json", encoding="utf-8")
    outside_report = _passing_report(Path.cwd(), "outside-run")
    outside_path = write_run_artifact(outside_report, tmp_path / "outside-runs")
    (runs / "linked-run").symlink_to(outside_path, target_is_directory=True)
    (runs / "loop-run").symlink_to("loop-run", target_is_directory=True)
    reader = AdminEvalReader(runs, tmp_path / "missing-baseline.json")

    escaped = reader.read("../outside")
    linked = reader.read("linked-run")
    looped = reader.read("loop-run")
    unreadable = reader.read("corrupt-run")

    assert escaped.state == "incompatible"
    assert escaped.compatibility_reasons == ("run_id_invalid",)
    assert linked.state == "incompatible"
    assert linked.compatibility_reasons == ("run_path_outside_root",)
    assert looped.state == "incompatible"
    assert looped.compatibility_reasons == ("run_path_outside_root",)
    assert unreadable.state == "incompatible"
    assert unreadable.compatibility_reasons == ("candidate_unreadable",)
