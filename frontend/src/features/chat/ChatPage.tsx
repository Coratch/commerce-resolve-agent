import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  createConversation,
  decideL2Memory,
  decideL2Upgrade,
  decideRefund,
  deleteConversation,
  getL2Case,
  getL2CaseTrace,
  getPendingL2,
  getPendingRefund,
  listConversationMessages,
  listConversations,
  listL2Cases,
  submitConversationMessage,
  subscribeRunEvents,
  updateConversationLifecycle,
} from "../../api/client";
import type {
  ChatResponse,
  ConversationMessage,
  ConversationSummary,
  PublicL2TraceEvent,
  PublicL2UpgradePreview,
  PublicMemoryProposal,
  PublicRefundPreview,
  RunEvent,
  SessionResponse,
} from "../../api/types";
import styles from "./ChatPage.module.css";
import { L2CasePanel, L2UpgradeCard, MemoryProposalCard } from "./L2Cards";

interface ChatPageProps {
  session: SessionResponse;
}

interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;
}

/** 生成刷新或响应丢失后仍可安全重试的客户端请求标识。 */
function newClientMessageId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 将公开 API 错误转换为普通助手对话，不暴露内部异常。 */
function assistantMessageForError(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "抱歉，我现在暂时无法完成这个请求，请稍后再试。";
}

/** 从持久消息白名单 Payload 还原卡片和引用所需公开响应。 */
function responseFromMessage(message: ConversationMessage): ChatResponse | undefined {
  if (message.role !== "assistant") {
    return undefined;
  }
  return {
    thread_id: message.thread_id,
    assistant_message: message.content,
    public_status:
      typeof message.payload.public_status === "string"
        ? message.payload.public_status
        : message.status,
    citations: Array.isArray(message.payload.citations)
      ? (message.payload.citations as ChatResponse["citations"])
      : [],
    refund_preview: message.payload.refund_preview as
      | ChatResponse["refund_preview"]
      | undefined,
    refund_result: message.payload.refund_result as
      | ChatResponse["refund_result"]
      | undefined,
    l2_upgrade_preview: message.payload.l2_upgrade_preview as
      | ChatResponse["l2_upgrade_preview"]
      | undefined,
    l2_case_summary: message.payload.l2_case_summary as
      | ChatResponse["l2_case_summary"]
      | undefined,
    l2_pending_action: message.payload.l2_pending_action as
      | ChatResponse["l2_pending_action"]
      | undefined,
    l2_trace_events: Array.isArray(message.payload.l2_trace_events)
      ? (message.payload.l2_trace_events as ChatResponse["l2_trace_events"])
      : [],
    memory_proposal: message.payload.memory_proposal as
      | ChatResponse["memory_proposal"]
      | undefined,
  };
}

/** 把服务端消息转换为 React 只读展示模型。 */
function toDisplayMessage(message: ConversationMessage): DisplayMessage {
  return {
    id: message.message_id,
    role: message.role,
    content: message.content,
    response: responseFromMessage(message),
  };
}

/** 订阅一个 Run 的持久 SSE 事件，并在终态刷新服务端历史。 */
function useRunEvents(
  threadId: string | undefined,
  onTerminal: () => Promise<void>,
): {
  progress: string | null;
  follow: (runId: string) => void;
  stop: () => void;
} {
  const [progress, setProgress] = useState<string | null>(null);
  const closeRef = useRef<(() => void) | null>(null);

  /** 主动关闭旧 SSE，避免切换会话后串入事件。 */
  function stop(): void {
    closeRef.current?.();
    closeRef.current = null;
    setProgress(null);
  }

  /** 从事件 Payload 中读取有限公开阶段文案。 */
  function handleEvent(event: RunEvent): void {
    if (event.event_type === "step.updated") {
      const message = event.payload.message;
      if (typeof message === "string") {
        setProgress(message);
      }
    }
  }

  /** 连接指定 Run；SSE 会自动使用 Last-Event-ID 重放缺失事件。 */
  function follow(runId: string): void {
    if (threadId === undefined) {
      return;
    }
    stop();
    setProgress("请求已接受，正在开始处理…");
    closeRef.current = subscribeRunEvents(
      threadId,
      runId,
      handleEvent,
      () => {
        setProgress(null);
        closeRef.current = null;
        void onTerminal();
      },
      () => {
        setProgress("连接暂时中断，正在自动恢复进度…");
      },
    );
  }

  useEffect(() => stop, [threadId]);
  return { progress, follow, stop };
}

/** 渲染持久会话列表、公开历史、SSE 进度和待处理动作。 */
export function ChatPage({ session }: ChatPageProps) {
  const { threadId } = useParams<{ threadId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const creatingRef = useRef(false);
  const [message, setMessage] = useState("");
  const [conversationFilter, setConversationFilter] = useState<
    "active" | "archived"
  >("active");
  const [optimistic, setOptimistic] = useState<DisplayMessage[]>([]);
  const [transient, setTransient] = useState<DisplayMessage[]>([]);
  const [pendingRefund, setPendingRefund] = useState<PublicRefundPreview | null>(null);
  const [pendingUpgrade, setPendingUpgrade] =
    useState<PublicL2UpgradePreview | null>(null);
  const [pendingMemory, setPendingMemory] =
    useState<PublicMemoryProposal | null>(null);
  const [selectedL2CaseId, setSelectedL2CaseId] = useState<string | null>(null);
  const [l2Trace, setL2Trace] = useState<PublicL2TraceEvent[]>([]);
  const [traceCursor, setTraceCursor] = useState<number | null>(null);
  const [traceHasMore, setTraceHasMore] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [loadingMoreTrace, setLoadingMoreTrace] = useState(false);

  const conversations = useQuery({
    queryKey: ["conversations", conversationFilter, session.mode, session.username],
    queryFn: () => listConversations(conversationFilter),
  });
  const history = useQuery({
    queryKey: ["conversation-messages", threadId],
    queryFn: () => listConversationMessages(threadId ?? ""),
    enabled: threadId !== undefined,
  });
  const l2Cases = useQuery({
    queryKey: ["l2-cases", threadId],
    queryFn: () => listL2Cases(threadId),
    enabled: threadId !== undefined && session.mode === "registered",
  });
  const l2Detail = useQuery({
    queryKey: ["l2-case-detail", selectedL2CaseId],
    queryFn: () => getL2Case(selectedL2CaseId ?? ""),
    enabled: selectedL2CaseId !== null && session.mode === "registered",
  });

  /** 刷新当前历史、列表和待处理动作。 */
  async function refreshConversation(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["conversation-messages", threadId] }),
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["l2-cases", threadId] }),
      queryClient.invalidateQueries({ queryKey: ["l2-case-detail"] }),
    ]);
    setOptimistic([]);
    setTransient([]);
    if (threadId !== undefined && session.mode === "registered") {
      const [refund, l2] = await Promise.all([
        getPendingRefund(threadId),
        getPendingL2(threadId),
      ]);
      setPendingRefund(refund.pending ? refund.refund_preview ?? null : null);
      setPendingUpgrade(l2.pending ? l2.upgrade_preview ?? null : null);
      setPendingMemory(l2.pending ? l2.memory_proposal ?? null : null);
    }
  }

  const runEvents = useRunEvents(threadId, refreshConversation);

  useEffect(() => {
    /** 没有 URL 会话时优先恢复服务端列表，再创建唯一空会话。 */
    async function selectInitialConversation(): Promise<void> {
      if (threadId !== undefined || conversations.isPending || creatingRef.current) {
        return;
      }
      const items = conversations.data?.conversations ?? [];
      const hint = localStorage.getItem("commerce-resolve-thread");
      const selected = items.find((item) => item.thread_id === hint) ?? items[0];
      if (selected !== undefined) {
        navigate(`/chat/${selected.thread_id}`, { replace: true });
        return;
      }
      creatingRef.current = true;
      try {
        const created = await createConversation();
        localStorage.setItem("commerce-resolve-thread", created.thread_id);
        await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        navigate(`/chat/${created.thread_id}`, { replace: true });
      } finally {
        creatingRef.current = false;
      }
    }

    void selectInitialConversation();
  }, [conversations.data, conversations.isPending, navigate, queryClient, threadId]);

  useEffect(() => {
    /** URL 切换后恢复卡片；Local Storage 仅记录已验证的导航提示。 */
    async function restorePendingActions(): Promise<void> {
      setOptimistic([]);
      setTransient([]);
      setPendingRefund(null);
      setPendingUpgrade(null);
      setPendingMemory(null);
      if (threadId === undefined) {
        return;
      }
      localStorage.setItem("commerce-resolve-thread", threadId);
      if (session.mode !== "registered") {
        return;
      }
      try {
        const [refund, l2] = await Promise.all([
          getPendingRefund(threadId),
          getPendingL2(threadId),
        ]);
        setPendingRefund(refund.pending ? refund.refund_preview ?? null : null);
        setPendingUpgrade(l2.pending ? l2.upgrade_preview ?? null : null);
        setPendingMemory(l2.pending ? l2.memory_proposal ?? null : null);
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.errorCode === "conversation_not_accessible"
        ) {
          localStorage.removeItem("commerce-resolve-thread");
          navigate("/chat", { replace: true });
        }
      }
    }

    void restorePendingActions();
  }, [navigate, session.mode, threadId]);

  useEffect(() => {
    const assistant = [...(history.data?.messages ?? [])]
      .reverse()
      .find((item) => item.role === "assistant");
    if (assistant === undefined) {
      return;
    }
    const response = responseFromMessage(assistant);
    setPendingRefund(response?.refund_preview ?? null);
    setPendingUpgrade(response?.l2_upgrade_preview ?? null);
    setPendingMemory(response?.memory_proposal ?? null);
  }, [history.data]);

  useEffect(() => {
    /** thread 切换后只从服务端 Case 列表选择最近一条，不信任本地消息推断。 */
    const cases = l2Cases.data ?? [];
    if (cases.length === 0) {
      setSelectedL2CaseId(null);
      setL2Trace([]);
      return;
    }
    if (!cases.some((item) => item.case_id === selectedL2CaseId)) {
      setSelectedL2CaseId(cases[0].case_id);
    }
  }, [l2Cases.data, selectedL2CaseId]);

  useEffect(() => {
    /** Case 详情变化后以服务端首屏和 keyset 游标替换页面内 Trace。 */
    if (l2Detail.data === undefined) {
      return;
    }
    setL2Trace(l2Detail.data.events);
    setTraceCursor(l2Detail.data.next_after_sequence ?? null);
    setTraceHasMore(l2Detail.data.has_more);
    setTraceError(null);
  }, [l2Detail.data]);

  /** 通过 React Query 缓存读取下一页 Trace，并按 sequence_no 去重。 */
  async function loadMoreL2Trace(): Promise<void> {
    if (selectedL2CaseId === null || traceCursor === null || loadingMoreTrace) {
      return;
    }
    setLoadingMoreTrace(true);
    setTraceError(null);
    try {
      const page = await queryClient.fetchQuery({
        queryKey: ["l2-trace", selectedL2CaseId, traceCursor],
        queryFn: () => getL2CaseTrace(selectedL2CaseId, traceCursor),
      });
      setL2Trace((current) => {
        const bySequence = new Map(
          [...current, ...page.events].map((event) => [event.sequence_no, event]),
        );
        return [...bySequence.values()].sort(
          (left, right) => left.sequence_no - right.sequence_no,
        );
      });
      setTraceCursor(page.next_after_sequence ?? null);
      setTraceHasMore(page.has_more);
    } catch {
      setTraceError("处理记录暂时无法读取，请稍后重试。");
    } finally {
      setLoadingMoreTrace(false);
    }
  }

  const displayMessages = useMemo(
    () => [
      ...(history.data?.messages.map(toDisplayMessage) ?? []),
      ...optimistic,
      ...transient,
    ],
    [history.data, optimistic, transient],
  );
  const selectedConversation = conversations.data?.conversations.find(
    (item) => item.thread_id === threadId,
  );

  const mutation = useMutation({
    mutationFn: async (input: { id: string; content: string }) => {
      if (threadId === undefined) {
        throw new Error("会话尚未建立");
      }
      return submitConversationMessage(threadId, input.id, input.content);
    },
    onMutate: (input) => {
      setOptimistic((current) => [
        ...current,
        { id: input.id, role: "user", content: input.content },
      ]);
      setMessage("");
    },
    onSuccess: (accepted) => runEvents.follow(accepted.run.run_id),
    onError: (error) => {
      setOptimistic([]);
      setTransient((current) => [
        ...current,
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          content: assistantMessageForError(error),
        },
      ]);
    },
  });

  /** 把审批失败作为普通助手消息展示。 */
  function appendAssistantError(error: unknown): void {
    setTransient((current) => [
      ...current,
      {
        id: `error-${Date.now()}`,
        role: "assistant",
        content: assistantMessageForError(error),
      },
    ]);
  }

  const approvalMutation = useMutation({
    mutationFn: async (decision: "approve" | "reject") => {
      if (pendingRefund === null || threadId === undefined) {
        throw new Error("当前没有待审批退款");
      }
      return decideRefund(threadId, pendingRefund.action_id, decision);
    },
    onSuccess: (accepted) => {
      setPendingRefund(null);
      runEvents.follow(accepted.run.run_id);
    },
    onError: appendAssistantError,
  });
  const upgradeMutation = useMutation({
    mutationFn: async (decision: "confirm" | "cancel") => {
      if (pendingUpgrade === null || threadId === undefined) {
        throw new Error("当前没有待确认升级");
      }
      return decideL2Upgrade(threadId, pendingUpgrade.preview_id, decision);
    },
    onSuccess: (accepted) => {
      setPendingUpgrade(null);
      runEvents.follow(accepted.run.run_id);
    },
    onError: appendAssistantError,
  });
  const memoryMutation = useMutation({
    mutationFn: async (decision: "confirm" | "reject") => {
      if (pendingMemory === null || threadId === undefined) {
        throw new Error("当前没有待确认偏好");
      }
      return decideL2Memory(threadId, pendingMemory.proposal_id, decision);
    },
    onSuccess: (accepted) => {
      setPendingMemory(null);
      runEvents.follow(accepted.run.run_id);
    },
    onError: appendAssistantError,
  });

  /** 创建新会话并把其稳定 ID 写入 URL。 */
  async function startNewConversation(): Promise<void> {
    setConversationFilter("active");
    const created = await createConversation();
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    navigate(`/chat/${created.thread_id}`);
  }

  /** 归档当前会话并返回会话选择入口。 */
  async function archiveCurrentConversation(): Promise<void> {
    if (threadId === undefined) {
      return;
    }
    await updateConversationLifecycle(threadId, "archived");
    localStorage.removeItem("commerce-resolve-thread");
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    navigate("/chat");
  }

  /** 恢复当前归档会话并切回活动列表。 */
  async function restoreCurrentConversation(): Promise<void> {
    if (threadId === undefined) {
      return;
    }
    await updateConversationLifecycle(threadId, "active");
    setConversationFilter("active");
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  /** 删除当前无活动动作会话并返回会话选择入口。 */
  async function deleteCurrentConversation(): Promise<void> {
    if (threadId === undefined) {
      return;
    }
    await deleteConversation(threadId);
    setConversationFilter("active");
    localStorage.removeItem("commerce-resolve-thread");
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    navigate("/chat");
  }

  /** 在没有并发 Run 或待确认动作时提交当前非空消息。 */
  function submitCurrentMessage(): void {
    const normalized = message.trim();
    const blocked =
      pendingRefund !== null ||
      pendingUpgrade !== null ||
      pendingMemory !== null;
    if (
      normalized === "" ||
      mutation.isPending ||
      runEvents.progress !== null ||
      blocked ||
      selectedConversation?.lifecycle_status === "archived"
    ) {
      return;
    }
    mutation.mutate({ id: newClientMessageId(), content: normalized });
  }

  /** 处理发送按钮对应的表单提交。 */
  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    submitCurrentMessage();
  }

  /** 使用 Enter 发送，保留 Shift+Enter 换行并避免打断输入法组合。 */
  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    submitCurrentMessage();
  }

  const hasBlockingDecision =
    pendingRefund !== null || pendingUpgrade !== null || pendingMemory !== null;
  const modeLabel =
    session.mode === "guest"
      ? "游客使用确定性 Fake，不产生模型费用"
      : session.capabilities.can_use_llm
        ? "已登录 · LLM 模式"
        : "已登录 · LLM 当前不可用";

  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>售后问题，交给可验证的工作流</span>
          <h1>先查事实，再给答案。</h1>
          <p>对话、审批和 Agent 进度均由服务端持久化，可在刷新后继续。</p>
        </div>
        <div className={styles.mode}>{modeLabel}</div>
      </section>
      <section className={styles.workspace}>
        {session.mode === "registered" && (
          <aside className={styles.sidebar} aria-label="会话列表">
            <button type="button" onClick={() => void startNewConversation()}>
              新建会话
            </button>
            <div className={styles.filterActions}>
              <button
                type="button"
                className={conversationFilter === "active" ? styles.selected : undefined}
                onClick={() => setConversationFilter("active")}
              >
                当前
              </button>
              <button
                type="button"
                className={conversationFilter === "archived" ? styles.selected : undefined}
                onClick={() => setConversationFilter("archived")}
              >
                已归档
              </button>
            </div>
            <nav>
              {conversations.data?.conversations.map((item: ConversationSummary) => (
                <button
                  type="button"
                  className={item.thread_id === threadId ? styles.selected : undefined}
                  key={item.thread_id}
                  onClick={() => navigate(`/chat/${item.thread_id}`)}
                >
                  <strong>{item.title}</strong>
                  <small>{item.last_message_preview ?? "尚无消息"}</small>
                </button>
              ))}
            </nav>
            {threadId !== undefined && (
              <div className={styles.lifecycleActions}>
                {selectedConversation?.lifecycle_status === "archived" ? (
                  <button type="button" onClick={() => void restoreCurrentConversation()}>
                    恢复
                  </button>
                ) : (
                  <button type="button" onClick={() => void archiveCurrentConversation()}>
                    归档
                  </button>
                )}
                <button type="button" onClick={() => void deleteCurrentConversation()}>
                  删除
                </button>
              </div>
            )}
          </aside>
        )}
        <section className={styles.chat} aria-label="售后对话">
          {history.data?.history_state === "partial" && (
            <div className={styles.historyNotice}>
              此会话创建于历史持久化功能之前，仅显示升级后的新消息。
            </div>
          )}
          <div className={styles.messages} aria-live="polite">
            {history.isPending && threadId !== undefined && (
              <div className={styles.thinking}>正在恢复会话历史…</div>
            )}
            {displayMessages.length === 0 && !history.isPending && (
              <div className={styles.empty}>
                <strong>可以这样问</strong>
                <span>“帮我看看 ORD-001 到哪里了”</span>
                <span>“普通商品退货期限是几天？”</span>
              </div>
            )}
            {displayMessages.map((item) => (
              <article
                className={item.role === "user" ? styles.user : styles.assistant}
                key={item.id}
              >
                <p>{item.content}</p>
                {item.response?.citations.map((citation) => (
                  <small key={`${citation.document_id}-${citation.section_id}`}>
                    来源：{citation.title} · {citation.heading} · 第 {citation.line_start}–
                    {citation.line_end} 行
                  </small>
                ))}
              </article>
            ))}
            {runEvents.progress !== null && (
              <article className={styles.assistant}>
                <p>{runEvents.progress}</p>
              </article>
            )}
            {pendingUpgrade !== null && (
              <L2UpgradeCard
                preview={pendingUpgrade}
                pending={upgradeMutation.isPending}
                onDecision={(decision) => upgradeMutation.mutate(decision)}
              />
            )}
            {pendingMemory !== null && (
              <MemoryProposalCard
                proposal={pendingMemory}
                pending={memoryMutation.isPending}
                onDecision={(decision) => memoryMutation.mutate(decision)}
              />
            )}
            {pendingRefund !== null && (
              <aside className={styles.refundPreview} aria-label="待审批退款预览">
                <span>R2 · MOCK REFUND</span>
                <h2>退款 ¥{pendingRefund.amount}</h2>
                <p>
                  订单 {pendingRefund.order_id} · {pendingRefund.currency} · 原路退回{" "}
                  {pendingRefund.channel}
                </p>
                <div className={styles.approvalActions}>
                  <button
                    type="button"
                    className={styles.reject}
                    disabled={approvalMutation.isPending}
                    onClick={() => approvalMutation.mutate("reject")}
                  >
                    拒绝
                  </button>
                  <button
                    type="button"
                    disabled={approvalMutation.isPending}
                    onClick={() => approvalMutation.mutate("approve")}
                  >
                    批准 Mock 退款
                  </button>
                </div>
              </aside>
            )}
            {l2Detail.data !== undefined && (
              <L2CasePanel
                summary={l2Detail.data.case}
                events={l2Trace}
                metrics={l2Detail.data.metrics}
                cases={l2Cases.data ?? []}
                hasMore={traceHasMore}
                loadingMore={loadingMoreTrace}
                traceError={traceError}
                onCaseChange={setSelectedL2CaseId}
                onLoadMore={() => void loadMoreL2Trace()}
              />
            )}
          </div>
          <form className={styles.composer} onSubmit={handleSubmit}>
            <label className="sr-only" htmlFor="chat-message">
              输入售后问题
            </label>
            <textarea
              id="chat-message"
              value={message}
              maxLength={2000}
              rows={2}
              placeholder="输入订单号或售后政策问题…"
              disabled={
                hasBlockingDecision ||
                threadId === undefined ||
                selectedConversation?.lifecycle_status === "archived"
              }
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <button
              type="submit"
              disabled={
                mutation.isPending ||
                runEvents.progress !== null ||
                hasBlockingDecision ||
                selectedConversation?.lifecycle_status === "archived" ||
                message.trim() === ""
              }
            >
              发送
            </button>
          </form>
        </section>
      </section>
    </main>
  );
}
