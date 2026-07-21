"""执行可持久化 Agent Run，并发布有限、可重放的公开进度。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command

from commerce_resolve.adapters.sqlite_business import BusinessDataError
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.conversation_models import AcceptedRun
from commerce_resolve.conversation_projection import (
    ConversationProjectionError,
    project_chat_response,
    public_message_payload,
)
from commerce_resolve.gateways import InterpreterUnavailableError
from commerce_resolve.l2_memory import open_sqlite_memory_store
from commerce_resolve.l2_models import L2RuntimeState
from commerce_resolve.state import RunContext
from commerce_resolve.web.dependencies import RequestAccess, WebServices
from commerce_resolve.workflow import build_workflow

STEP_PHASES: dict[str, tuple[str, str]] = {
    "bind_and_interpret": ("understanding", "正在理解你的问题…"),
    "query_order": ("loading_order", "正在查询订单信息…"),
    "query_shipment": ("loading_shipment", "正在查询物流进度…"),
    "prepare_policy_query": ("searching_policy", "正在整理政策检索条件…"),
    "retrieve_policy": ("searching_policy", "正在检索售后政策…"),
    "assess_policy_evidence": ("checking_evidence", "正在核对政策证据…"),
    "load_refund_context": ("checking_refund", "正在读取退款相关事实…"),
    "assess_refund": ("checking_refund", "正在核对退款资格…"),
    "build_refund_preview": ("preparing_action", "正在生成退款审批预览…"),
    "reject_refund_action": ("applying_decision", "正在记录退款决定…"),
    "revalidate_refund": ("checking_refund", "正在重新核对退款资格…"),
    "execute_refund": ("executing_refund", "正在执行 Mock 退款…"),
    "verify_refund": ("verifying_result", "正在验证 Mock 退款结果…"),
    "l2_prepare_upgrade": ("preparing_l2", "正在准备 AI 二线客服升级…"),
    "l2_cancel_upgrade": ("applying_decision", "正在记录升级决定…"),
    "l2_create_case": ("creating_case", "正在创建 AI 二线客服 Case…"),
    "l2_load_context": ("loading_context", "正在加载二线客服上下文…"),
    "l2_decide": ("l2_processing", "AI 二线客服正在处理…"),
    "l2_execute_tool": ("l2_tool", "AI 二线客服正在查询受控数据…"),
    "l2_bridge_refund": ("checking_refund", "正在核对退款候选…"),
    "l2_record_refund_result": ("verifying_result", "正在记录退款验证结果…"),
    "l2_finalize_resolved": ("finalizing", "正在整理处理结果…"),
    "l2_finalize_stopped": ("finalizing", "正在整理停止原因…"),
}


class RuntimeAccessError(ValueError):
    """表示后台执行开始后发现的稳定模型能力错误。"""

    def __init__(self, error_code: str) -> None:
        """保存可公开错误码，不包含 Provider 或配置明文。"""

        super().__init__(error_code)
        self.error_code = error_code


class ConversationRuntime:
    """协调 Run 持久化、Graph 执行、公开投影与失败收口。"""

    def __init__(self, services: WebServices) -> None:
        """保存应用级依赖，运行时对象本身不持久化到 State。"""

        self.services = services
        self.repository = services.require_conversation_repository()

    def execute_chat_run(
        self,
        access: RequestAccess,
        accepted: AcceptedRun,
        message_override: str | None = None,
    ) -> None:
        """在请求响应之后执行一轮 Graph，断开 SSE 不会取消此方法。"""

        run = accepted.run
        with self.services.thread_locks.acquire(run.thread_id) as acquired:
            if not acquired:
                self.repository.fail_run(
                    run_id=run.run_id,
                    error_code="thread_busy",
                    assistant_message="当前会话还有请求正在处理，请稍后重试。",
                )
                return
            try:
                self.repository.mark_run_started(run.run_id)
                self._execute_graph(access, accepted, message_override=message_override)
            except InterpreterUnavailableError:
                self._fail(run.run_id, "llm_temporarily_failed")
            except RuntimeAccessError as error:
                self._fail(run.run_id, error.error_code)
            except ConversationProjectionError:
                self._fail(run.run_id, "projection_failed")
            except (LookupError, ValueError):
                self._fail(run.run_id, "query_rejected")
            except Exception:
                self._fail(run.run_id, "run_failed")

    def execute_action_run(
        self,
        access: RequestAccess,
        accepted: AcceptedRun,
        resume_payload: dict[str, object],
    ) -> None:
        """在响应后恢复审批类中断，并把结果写入同一公开 Run。"""

        run = accepted.run
        with self.services.thread_locks.acquire(run.thread_id) as acquired:
            if not acquired:
                self.repository.fail_run(
                    run_id=run.run_id,
                    error_code="thread_busy",
                    assistant_message="当前会话还有请求正在处理，请稍后重试。",
                )
                return
            try:
                self.repository.mark_run_started(run.run_id)
                self._execute_action_graph(access, accepted, resume_payload)
            except InterpreterUnavailableError:
                self._fail(run.run_id, "llm_temporarily_failed")
            except RuntimeAccessError as error:
                self._fail(run.run_id, error.error_code)
            except BusinessDataError as error:
                self._fail(run.run_id, error.error_code)
            except ConversationProjectionError:
                self._fail(run.run_id, "projection_failed")
            except (LookupError, ValueError):
                self._fail(run.run_id, "query_rejected")
            except Exception:
                self._fail(run.run_id, "run_failed")

    def _execute_graph(
        self,
        access: RequestAccess,
        accepted: AcceptedRun,
        *,
        message_override: str | None = None,
    ) -> None:
        """选择新请求或追问恢复路径，并逐节点发布脱敏阶段。"""

        thread_id = accepted.run.thread_id
        input_message = message_override or accepted.message.content
        config = {"configurable": {"thread_id": thread_id}}
        with open_sqlite_memory_store(self.services.settings.memory_db_path) as store:
            with open_sqlite_checkpointer(
                self.services.settings.checkpoint_db_path
            ) as checkpointer:
                probe = build_workflow(
                    self.services.refund_resume_dependencies(),
                    checkpointer=checkpointer,
                    store=store,
                )
                snapshot = probe.get_state(config)
                resume_user_input = (
                    access.principal.mode == "registered"
                    and snapshot.interrupts
                    and snapshot.next == ("l2_await_user_input",)
                )
                if resume_user_input:
                    dependencies, remaining = self._l2_resume_dependencies(access)
                    graph_input: dict[str, Any] | Command = Command(
                        resume={"message": input_message}
                    )
                else:
                    dependencies, remaining = self._new_message_dependencies(access)
                    graph_input = {
                        "messages": [{"role": "user", "content": input_message}]
                    }
                graph = build_workflow(
                    dependencies,
                    checkpointer=checkpointer,
                    store=store,
                )
                self._stream_and_complete(
                    graph=graph,
                    graph_input=graph_input,
                    config=config,
                    context=self._run_context(
                        access,
                        thread_id,
                        l2_allowed=access.principal.mode == "registered",
                        l2_quota_remaining=remaining,
                    ),
                    access=access,
                    accepted=accepted,
                )

    def _execute_action_graph(
        self,
        access: RequestAccess,
        accepted: AcceptedRun,
        resume_payload: dict[str, object],
    ) -> None:
        """按动作类型装配恢复依赖，并执行受服务端绑定的 Command。"""

        thread_id = accepted.run.thread_id
        config = {"configurable": {"thread_id": thread_id}}
        with open_sqlite_memory_store(self.services.settings.memory_db_path) as store:
            with open_sqlite_checkpointer(
                self.services.settings.checkpoint_db_path
            ) as checkpointer:
                probe = build_workflow(
                    self.services.refund_resume_dependencies(),
                    checkpointer=checkpointer,
                    store=store,
                )
                snapshot = probe.get_state(config)
                runtime = snapshot.values.get("l2_runtime")
                uses_l2 = accepted.run.request_kind in {
                    "l2_upgrade_decision",
                    "memory_decision",
                } or (
                    accepted.run.request_kind == "refund_decision"
                    and isinstance(runtime, L2RuntimeState)
                    and runtime.case_id is not None
                )
                if uses_l2:
                    dependencies, remaining = self._l2_resume_dependencies(access)
                else:
                    dependencies = self.services.refund_resume_dependencies()
                    remaining = 0
                trusted_payload = dict(resume_payload)
                if accepted.run.request_kind == "refund_decision":
                    trusted_payload["actor_id"] = access.principal.actor_id
                graph = build_workflow(
                    dependencies,
                    checkpointer=checkpointer,
                    store=store,
                )
                self._stream_and_complete(
                    graph=graph,
                    graph_input=Command(resume=trusted_payload),
                    config=config,
                    context=self._run_context(
                        access,
                        thread_id,
                        l2_allowed=uses_l2,
                        l2_quota_remaining=remaining,
                    ),
                    access=access,
                    accepted=accepted,
                )

    def _stream_and_complete(
        self,
        *,
        graph: Any,
        graph_input: dict[str, Any] | Command,
        config: dict[str, Any],
        context: RunContext,
        access: RequestAccess,
        accepted: AcceptedRun,
    ) -> None:
        """流式执行 Graph、发布有限阶段并持久化最终公开投影。"""

        step_index = 0
        for update in graph.stream(
            graph_input,
            config=config,
            context=context,
            stream_mode="updates",
        ):
            if not isinstance(update, dict):
                continue
            for node_name in update:
                phase = STEP_PHASES.get(node_name)
                if phase is None:
                    continue
                step_index += 1
                self.repository.append_step_event(
                    run_id=accepted.run.run_id,
                    event_key=f"step:{step_index}",
                    phase=phase[0],
                    message=phase[1],
                )
        final_snapshot = graph.get_state(config)
        response = project_chat_response(
            self.services,
            access,
            accepted.run.thread_id,
            final_snapshot.values,
            next_nodes=final_snapshot.next,
        )
        checkpoint_id = (
            str(final_snapshot.config.get("configurable", {}).get("checkpoint_id", ""))
            or None
        )
        self.repository.complete_run(
            run_id=accepted.run.run_id,
            assistant_message=response.assistant_message,
            payload=public_message_payload(response),
            pending_action=response.l2_pending_action,
            checkpoint_id=checkpoint_id,
        )

    def _new_message_dependencies(
        self,
        access: RequestAccess,
    ) -> tuple[Any, int]:
        """为游客或注册用户装配一轮新消息依赖并执行额度门禁。"""

        if access.principal.mode == "guest":
            return self.services.guest_dependencies(access.principal), 0
        principal = access.principal
        if principal.user_id is None:
            raise RuntimeAccessError("llm_not_authorized")
        usage_date = datetime.now(UTC).date()
        decision = self.services.llm_access_policy.decide(
            principal,
            feature_enabled=self.services.settings.llm_feature_enabled,
            model_configured=self.services.model_configured,
            quota_available=self.services.repository.quota_available(
                principal.user_id,
                usage_date,
                self.services.settings.llm_daily_call_limit,
            ),
        )
        if not decision.allowed:
            raise RuntimeAccessError(decision.error_code or "llm_not_authorized")
        if not self.services.repository.accept_llm_call(
            principal.user_id,
            usage_date,
            self.services.settings.llm_daily_call_limit,
        ):
            raise RuntimeAccessError("llm_quota_exceeded")
        usage = self.services.repository.get_llm_usage(principal.user_id, usage_date)
        remaining = max(
            0,
            self.services.settings.llm_daily_call_limit - usage.accepted_calls,
        )
        try:
            dependencies = self.services.registered_dependencies()
        except (ModuleNotFoundError, ValueError):
            raise RuntimeAccessError("llm_not_configured") from None
        return dependencies, remaining

    def _l2_resume_dependencies(
        self,
        access: RequestAccess,
    ) -> tuple[Any, int]:
        """验证 L2 追问恢复仍有模型能力，但不重复预扣一线额度。"""

        principal = access.principal
        if (
            principal.user_id is None
            or not self.services.settings.llm_feature_enabled
            or not self.services.model_configured
        ):
            raise RuntimeAccessError("llm_not_authorized")
        usage = self.services.repository.get_llm_usage(
            principal.user_id,
            datetime.now(UTC).date(),
        )
        remaining = max(
            0,
            self.services.settings.llm_daily_call_limit - usage.accepted_calls,
        )
        return self.services.l2_resume_dependencies(), remaining

    def _run_context(
        self,
        access: RequestAccess,
        thread_id: str,
        *,
        l2_allowed: bool,
        l2_quota_remaining: int,
    ) -> RunContext:
        """从可信 Session 构造不能由客户端覆盖的 Graph Runtime Context。"""

        return RunContext(
            user_id=access.principal.actor_id,
            workspace_id=access.principal.workspace_id,
            access_mode=access.principal.mode,
            as_of=datetime.now(UTC).date(),
            task_id=thread_id,
            subject_id=access.identity.subject_id,
            l2_allowed=l2_allowed,
            l2_quota_remaining=l2_quota_remaining,
        )

    def _fail(self, run_id: str, error_code: str) -> None:
        """把所有执行失败收敛为普通助手消息和稳定可重试状态。"""

        messages = {
            "llm_temporarily_failed": "模型服务暂时不可用，请稍后重试。",
            "llm_not_authorized": "当前账号不能使用模型能力。",
            "llm_not_configured": "模型能力尚未配置，请联系管理员。",
            "llm_quota_exceeded": "今天的模型调用额度已用完，请稍后再试。",
            "query_rejected": (
                "暂时无法处理这个问题，请换一种方式描述或尝试查询订单、物流及售后政策。"
            ),
            "projection_failed": "处理已经结束，但公开结果生成失败，请重试本次请求。",
            "run_failed": "本次处理未能完成，请稍后重试。",
        }
        self.repository.fail_run(
            run_id=run_id,
            error_code=error_code,
            assistant_message=messages.get(error_code, "本次处理未能完成，请重试。"),
        )
