"""验证 v0.8 统一 Eval CLI 不破坏旧入口。"""

from pathlib import Path

from commerce_resolve.cli import main
from commerce_resolve.provider_evaluation import (
    FixtureInterpreter,
    FixtureL2Provider,
    load_provider_dataset,
)


def test_versioned_eval_run_compare_and_baseline_cli(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证 Run、显式接受 Baseline 与无回归比较的完整 CLI 路径。"""

    runs = tmp_path / "runs"
    assert (
        main(
            [
                "eval",
                "run",
                "--suite",
                "v0.1",
                "--output-root",
                str(runs),
            ]
        )
        == 0
    )
    run_dir = next(runs.iterdir())
    baseline = tmp_path / "baseline.json"
    assert (
        main(
            [
                "eval",
                "baseline",
                "accept",
                "--run",
                str(run_dir),
                "--output",
                str(baseline),
                "--reason",
                "CLI 集成测试",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "eval",
                "compare",
                "--candidate",
                str(run_dir),
                "--baseline",
                str(baseline),
            ]
        )
        == 0
    )
    assert '"status": "passed"' in capsys.readouterr().out


def test_versioned_eval_rejects_all_mixed_with_specific_suite(tmp_path: Path) -> None:
    """验证含糊的 Suite 选择在执行任何 Eval 前失败。"""

    assert (
        main(
            [
                "eval",
                "run",
                "--suite",
                "all",
                "--suite",
                "v0.1",
                "--output-root",
                str(tmp_path),
            ]
        )
        == 4
    )
    assert list(tmp_path.iterdir()) == []


def test_eval_cli_rejects_legacy_suite_mixed_with_subcommand(capsys: object) -> None:
    """验证旧 Suite 参数不会被新子命令静默忽略。"""

    exit_code = main(["eval", "--suite", "v0.1", "run"])

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""
    assert "不能与 Eval 子命令混用" in captured.err


def test_provider_qualify_cli_uses_explicit_dataset_and_two_repetitions(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """验证 qualify 是显式入口，并可通过同一契约注入 Fake Provider。"""

    from commerce_resolve.adapters.openai_interpreter import OpenAIQueryInterpreter
    from commerce_resolve.adapters.openai_l2_agent import OpenAIL2Agent

    dataset_path = (
        Path(__file__).parents[1] / "data/eval/provider-qualification-v1.json"
    )
    dataset = load_provider_dataset(dataset_path)
    monkeypatch.setenv("LLM_MODEL", "fixture-provider")
    monkeypatch.setattr(
        OpenAIQueryInterpreter,
        "from_env",
        lambda: FixtureInterpreter(dataset.scenarios),
    )
    monkeypatch.setattr(
        OpenAIL2Agent,
        "from_env",
        lambda: FixtureL2Provider(dataset.scenarios),
    )

    exit_code = main(
        [
            "eval",
            "qualify",
            "--dataset",
            str(dataset_path),
            "--repetitions",
            "2",
            "--output-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    run_dir = next(tmp_path.iterdir())
    assert (run_dir / "qualification.json").is_file()
