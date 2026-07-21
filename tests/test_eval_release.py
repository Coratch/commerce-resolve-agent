"""验证固定 Release Check、失败传播和环境脱敏。"""

from __future__ import annotations

from pathlib import Path

from commerce_resolve.eval_models import EvalReleaseCheckResult
from commerce_resolve.eval_release import (
    ReleaseCheck,
    _allowed_environment,
    run_release_gate,
)


class FakeReleaseExecutor:
    """按 Check ID 返回固定结果，不启动真实子进程。"""

    def __init__(self, failed_id: str | None = None) -> None:
        """保存需要故意失败的唯一检查。"""

        self.failed_id = failed_id
        self.executed: list[str] = []

    def execute(
        self,
        check: ReleaseCheck,
        *,
        project_root: Path,
        log_path: Path,
    ) -> EvalReleaseCheckResult:
        """记录稳定顺序并返回结构化通过或失败。"""

        del project_root, log_path
        self.executed.append(check.check_id)
        failed = check.check_id == self.failed_id
        return EvalReleaseCheckResult(
            check_id=check.check_id,
            status="failed" if failed else "passed",
            exit_code=1 if failed else 0,
            duration_ms=1,
        )


def _checks() -> tuple[ReleaseCheck, ...]:
    """返回不包含离线 Artifact 依赖的两个测试检查。"""

    return (
        ReleaseCheck("first", ("python", "-m", "first")),
        ReleaseCheck("second", ("python", "-m", "second")),
    )


def test_release_gate_passes_only_when_all_required_checks_pass(tmp_path: Path) -> None:
    """验证全部强制检查通过才生成 passed 发布结论。"""

    executor = FakeReleaseExecutor()
    report, run_dir = run_release_gate(
        Path(__file__).parents[1],
        tmp_path,
        executor=executor,
        checks=_checks(),
        run_id="release-passed",
    )
    assert report.status == "passed"
    assert report.passed_checks == report.required_checks == 2
    assert executor.executed == ["first", "second"]
    assert (run_dir / "release.json").is_file()


def test_release_gate_propagates_nonzero_required_check(tmp_path: Path) -> None:
    """验证任一强制命令失败都会使总体门禁失败。"""

    report, _ = run_release_gate(
        Path(__file__).parents[1],
        tmp_path,
        executor=FakeReleaseExecutor("second"),
        checks=_checks(),
        run_id="release-failed",
    )
    assert report.status == "failed"
    assert report.passed_checks == 1


def test_release_environment_never_forwards_llm_secrets(monkeypatch) -> None:
    """验证即使误写入允许列表，LLM 密钥也不会传给子进程。"""

    monkeypatch.setenv("PATH", "/test/bin")
    monkeypatch.setenv("LLM_API_KEY", "never-forward")
    check = ReleaseCheck(
        "environment",
        ("python", "-m", "check"),
        allowed_environment_keys=("PATH", "LLM_API_KEY"),
    )
    environment = _allowed_environment(check)
    assert environment == {"PATH": "/test/bin"}
