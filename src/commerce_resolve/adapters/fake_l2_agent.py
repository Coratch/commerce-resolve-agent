"""提供离线图测试和 Eval 使用的脚本化 L2 Agent Model。"""

from __future__ import annotations

from collections.abc import Iterable

from commerce_resolve.l2_gateways import (
    L2_MODEL_UNAVAILABLE_MESSAGE,
    L2ModelUnavailableError,
)
from commerce_resolve.l2_models import (
    L2Decision,
    L2ModelRequest,
    L2ModelResult,
    L2ModelUsage,
)


class ScriptedL2Agent:
    """按固定顺序返回结构化决策并记录请求，避免测试访问网络。"""

    def __init__(
        self,
        decisions: Iterable[L2Decision],
        *,
        model_name: str = "fake-l2",
        tokens_per_call: int = 100,
    ) -> None:
        """复制决策脚本并设置固定模型名称和用量。"""

        self._decisions = list(decisions)
        self._model_name = model_name
        self._tokens_per_call = tokens_per_call
        self.requests: list[L2ModelRequest] = []

    @property
    def model_name(self) -> str:
        """返回测试和计量使用的稳定 Fake 模型名。"""

        return self._model_name

    def decide(self, request: L2ModelRequest) -> L2ModelResult:
        """消费下一条脚本决策；脚本不足时明确模拟模型不可用。"""

        self.requests.append(request)
        if not self._decisions:
            raise L2ModelUnavailableError(L2_MODEL_UNAVAILABLE_MESSAGE)
        return L2ModelResult(
            decision=self._decisions.pop(0),
            usage=L2ModelUsage(
                input_tokens=max(1, self._tokens_per_call - 20),
                output_tokens=20,
                estimated=False,
            ),
        )

    def replace_decisions(self, decisions: Iterable[L2Decision]) -> None:
        """替换尚未消费的脚本，供场景按运行时订单号装配确定性决策。"""

        self._decisions = list(decisions)
