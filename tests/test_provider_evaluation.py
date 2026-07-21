"""验证 20 条 Provider 资格集、Fake Provider 与脱敏 Artifact。"""

from pathlib import Path

import pytest

from commerce_resolve.gateways import InterpreterOutputInvalidError
from commerce_resolve.models import Interpretation, InterpretationContext
from commerce_resolve.provider_evaluation import (
    FixtureInterpreter,
    FixtureL2Provider,
    load_provider_dataset,
    run_provider_qualification,
    write_provider_artifact,
)


class InvalidRefundQualityInterpreter:
    """仅让一条资格场景产生无效结构化输出。"""

    def __init__(self, delegate: FixtureInterpreter) -> None:
        """保存其余场景使用的确定性解释器。"""

        self._delegate = delegate

    def interpret(
        self,
        text: str,
        context: InterpretationContext | None = None,
    ) -> Interpretation:
        """对质量退款场景模拟无效输出，其余场景正常返回。"""

        if "质量问题" in text:
            raise InterpreterOutputInvalidError("invalid")
        return self._delegate.interpret(text, context)


def _dataset_path() -> Path:
    """返回仓库内固定 Provider 资格数据集。"""

    return Path(__file__).parents[1] / "data/eval/provider-qualification-v1.json"


def test_fake_provider_passes_two_repetitions_without_safety_violation(
    tmp_path: Path,
) -> None:
    """验证 Fake Provider 可重复证明全部资格规则和 Artifact 脱敏。"""

    dataset = load_provider_dataset(_dataset_path())
    report = run_provider_qualification(
        dataset,
        interpreter=FixtureInterpreter(dataset.scenarios),
        l2_provider=FixtureL2Provider(dataset.scenarios),
        model_name="fixture-provider",
        repetitions=2,
        run_id="provider-test-run",
    )

    assert len(dataset.scenarios) == 20
    assert report.status == "passed"
    assert report.task_passed == report.task_total == 40
    assert report.structured_valid_rate == 1.0
    assert report.tool_accuracy == 1.0
    assert report.evidence_recall == 1.0
    assert report.safety_violations == ()
    run_dir = write_provider_artifact(report, tmp_path)
    content = (run_dir / "qualification.json").read_text("utf-8")
    assert "LLM_API_KEY" not in content
    assert "base_url" not in content.lower()
    assert "provider-test-run" in (run_dir / "report.md").read_text("utf-8")


def test_provider_qualification_rejects_single_repetition() -> None:
    """验证一次随机 Provider 结果不能成为资格证据。"""

    dataset = load_provider_dataset(_dataset_path())
    with pytest.raises(ValueError, match="不能小于 2"):
        run_provider_qualification(
            dataset,
            interpreter=FixtureInterpreter(dataset.scenarios),
            l2_provider=FixtureL2Provider(dataset.scenarios),
            model_name="fixture-provider",
            repetitions=1,
        )


def test_provider_qualification_counts_invalid_output_against_quality_gate() -> None:
    """验证单条无效输出计入 95% 门槛，而不会误标环境 incomplete。"""

    dataset = load_provider_dataset(_dataset_path())
    report = run_provider_qualification(
        dataset,
        interpreter=InvalidRefundQualityInterpreter(
            FixtureInterpreter(dataset.scenarios)
        ),
        l2_provider=FixtureL2Provider(dataset.scenarios),
        model_name="fixture-provider",
        repetitions=2,
    )

    assert report.status == "passed"
    assert report.task_passed == 38
    assert report.structured_valid_rate == 0.95
    assert {
        item.failure_code for item in report.results if not item.structured_valid
    } == {"InterpreterOutputInvalidError"}
