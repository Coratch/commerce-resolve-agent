"""验证 v0.8 Eval 通用 Pydantic 契约。"""

import pytest
from pydantic import ValidationError

from commerce_resolve.eval_models import EvalMetricDefinition


def test_eval_models_reject_extra_fields() -> None:
    """验证统一契约拒绝未声明字段，防止报告静默漂移。"""

    with pytest.raises(ValidationError):
        EvalMetricDefinition(
            metric_id="safety_violations",
            kind="safety",
            unit="count",
            direction="zero",
            threshold=0,
            unknown=True,
        )
