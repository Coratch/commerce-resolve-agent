"""定义 Web 层稳定、脱敏且可测试的公开错误。"""

from datetime import UTC, datetime, time, timedelta


class ApiError(RuntimeError):
    """携带 HTTP 状态、公开错误码和安全消息。"""

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        """保存可返回给浏览器的信息，不接受内部异常正文。"""

        super().__init__(error_code)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


PUBLIC_ERROR_MESSAGES = {
    "authentication_required": "请先登录后再执行此操作。",
    "authentication_failed": "账号或密码不正确。",
    "csrf_failed": "请求验证失败，请刷新页面后重试。",
    "origin_not_allowed": "请求来源不受信任。",
    "invitation_unavailable": "邀请码不可用。",
    "account_unavailable": "该账号当前不可用。",
    "order_not_accessible": "订单不存在或当前账号无权访问。",
    "order_conflict": "当前工作区已存在相同订单号。",
    "order_has_transaction_data": "该订单已有交易记录，不能直接删除。",
    "service_not_accessible": "售后服务不存在或当前账号无权访问。",
    "invalid_cursor": "分页位置无效，请刷新后重试。",
    "payment_locked": "该支付已进入退款流程，不能再修改。",
    "refund_not_authorized": "当前身份不能执行退款操作。",
    "refund_approval_required": "当前会话有一笔退款正在等待批准或拒绝。",
    "refund_action_not_accessible": "退款动作不存在或当前账号无权访问。",
    "refund_action_closed": "该退款动作已经结束，不能更改决定。",
    "refund_preview_stale": "退款预览已失效，请重新申请。",
    "refund_conflict": "该订单已有待处理或已完成退款。",
    "conversation_not_accessible": "会话不存在或当前账号无权访问。",
    "thread_busy": "该会话正在处理另一条消息，请稍后重试。",
    "rate_limited": "请求过于频繁，请稍后重试。",
    "llm_not_authorized": "当前账号不能使用模型服务。",
    "llm_disabled": "模型服务当前已停用。",
    "llm_not_configured": "模型服务尚未配置。",
    "llm_quota_exceeded": "今日对话次数已用完，请稍后再试。",
    "llm_temporarily_failed": "抱歉，我现在暂时无法连接模型服务，请稍后再试。",
    "l2_upgrade_decision_required": "请先确认或取消 AI 深度处理。",
    "l2_memory_decision_required": "请先确认或拒绝当前长期偏好建议。",
    "l2_pending_action_not_accessible": "当前待处理动作不存在或无权访问。",
    "l2_case_not_accessible": "AI 二线 Case 不存在或当前账号无权访问。",
    "memory_not_accessible": "长期偏好不存在或当前账号无权访问。",
    "memory_value_invalid": "该值不适用于当前偏好类型。",
    "query_rejected": (
        "暂时无法处理这个问题，请换一种方式描述或尝试查询订单、物流及售后政策。"
    ),
    "internal_error": "抱歉，我现在暂时无法完成这个请求，请稍后再试。",
}


def llm_quota_exceeded_message(
    limit: int,
    *,
    now: datetime | None = None,
) -> str:
    """按 UTC 计数周期生成包含真实上限和本地恢复时间的额度提示。"""

    local_now = now or datetime.now().astimezone()
    if local_now.tzinfo is None:
        raise ValueError("quota message requires timezone-aware datetime")
    next_utc_date = local_now.astimezone(UTC).date() + timedelta(days=1)
    reset_at = datetime.combine(next_utc_date, time.min, tzinfo=UTC).astimezone(
        local_now.tzinfo
    )
    day_offset = (reset_at.date() - local_now.date()).days
    if day_offset == 0:
        day_label = "今日"
    elif day_offset == 1:
        day_label = "明日"
    else:
        day_label = reset_at.strftime("%m月%d日")
    return (
        f"今日对话次数已用完（{limit}次），"
        f"将于{day_label}{reset_at:%H:%M}恢复。"
    )


def api_error(
    status_code: int,
    error_code: str,
    message: str | None = None,
) -> ApiError:
    """按稳定错误码构造脱敏异常，并允许少量明确消息覆盖。"""

    public_message = message or PUBLIC_ERROR_MESSAGES.get(
        error_code,
        PUBLIC_ERROR_MESSAGES["internal_error"],
    )
    return ApiError(status_code, error_code, public_message)
