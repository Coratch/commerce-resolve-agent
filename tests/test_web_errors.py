from datetime import datetime
from zoneinfo import ZoneInfo

from commerce_resolve.web.errors import llm_quota_exceeded_message


def test_llm_quota_message_uses_limit_and_local_reset_time() -> None:
    """验证每日额度提示包含真实上限和 UTC 周期对应的本地恢复时间。"""

    now = datetime(
        2026,
        7,
        24,
        16,
        0,
        tzinfo=ZoneInfo("Asia/Jakarta"),
    )

    assert llm_quota_exceeded_message(20, now=now) == (
        "今日对话次数已用完（20次），将于明日07:00恢复。"
    )
