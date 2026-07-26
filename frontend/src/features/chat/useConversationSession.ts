import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  decideL2Memory,
  decideL2Upgrade,
  decideRefund,
  getPendingL2,
  getPendingRefund,
  listConversationMessages,
  submitConversationMessage,
  subscribeRunEvents,
} from "../../api/client";
import type {
  ChatResponse,
  ConversationMessage,
  PendingL2Response,
  PublicL2UpgradePreview,
  PublicMemoryProposal,
  PublicRefundPreview,
  RunEvent,
  SessionResponse,
} from "../../api/types";

export interface ConversationDisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;
}

interface UseConversationSessionResult {
  messages: ConversationDisplayMessage[];
  pendingRefund: PublicRefundPreview | null;
  pendingUpgrade: PublicL2UpgradePreview | null;
  pendingMemory: PublicMemoryProposal | null;
  progress: string | null;
  isLoading: boolean;
  isSubmitting: boolean;
  submit: (message: string) => void;
  decideRefundAction: (decision: "approve" | "reject") => void;
  decideUpgradeAction: (decision: "confirm" | "cancel") => void;
  decideMemoryAction: (decision: "confirm" | "reject") => void;
}

interface PendingL2Cards {
  upgrade: PublicL2UpgradePreview | null;
  memory: PublicMemoryProposal | null;
}

/** 只展示与服务端当前待处理动作一致的 L2 卡片，忽略历史 State 残留。 */
export function selectPendingL2Cards(response: PendingL2Response): PendingL2Cards {
  if (!response.pending) {
    return { upgrade: null, memory: null };
  }
  return {
    upgrade:
      response.pending_action === "upgrade_confirmation"
        ? response.upgrade_preview ?? null
        : null,
    memory:
      response.pending_action === "memory_confirmation"
        ? response.memory_proposal ?? null
        : null,
  };
}

/** 生成消息提交的幂等客户端标识。 */
function newClientMessageId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 将公开 API 错误转换为普通客服回复。 */
function assistantMessageForError(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "抱歉，我现在暂时无法完成这个请求，请稍后再试。";
}

/** 从持久消息白名单恢复公开卡片与政策引用。 */
function responseFromMessage(message: ConversationMessage): ChatResponse | undefined {
  if (message.role !== "assistant") {
    return undefined;
  }
  if (message.payload_version !== 1 && message.payload_version !== 2) {
    return {
      thread_id: message.thread_id,
      assistant_message: message.content,
      public_status: message.status,
      citations: [],
      l2_trace_events: [],
    };
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
    service_resolution:
      message.payload_version === 2
        ? (message.payload.service_resolution as
            | ChatResponse["service_resolution"]
            | undefined)
        : undefined,
  };
}

/** 将服务端持久消息转换为对话面板展示模型。 */
function toDisplayMessage(message: ConversationMessage): ConversationDisplayMessage {
  return {
    id: message.message_id,
    role: message.role,
    content: message.content,
    response: responseFromMessage(message),
  };
}

/** 管理一条会话的消息、SSE Run 和可恢复审批动作。 */
export function useConversationSession(
  threadId: string | undefined,
  session: SessionResponse,
): UseConversationSessionResult {
  const queryClient = useQueryClient();
  const closeRef = useRef<(() => void) | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [optimistic, setOptimistic] = useState<ConversationDisplayMessage[]>([]);
  const [transient, setTransient] = useState<ConversationDisplayMessage[]>([]);
  const [pendingRefund, setPendingRefund] = useState<PublicRefundPreview | null>(null);
  const [pendingUpgrade, setPendingUpgrade] =
    useState<PublicL2UpgradePreview | null>(null);
  const [pendingMemory, setPendingMemory] = useState<PublicMemoryProposal | null>(null);

  const history = useQuery({
    queryKey: ["conversation-messages", threadId],
    queryFn: () => listConversationMessages(threadId ?? ""),
    enabled: threadId !== undefined,
  });

  /** 刷新持久历史和注册用户的待处理动作。 */
  async function refresh(): Promise<void> {
    await queryClient.invalidateQueries({
      queryKey: ["conversation-messages", threadId],
    });
    await queryClient.invalidateQueries({ queryKey: ["support"] });
    setOptimistic([]);
    setTransient([]);
    if (threadId !== undefined && session.mode === "registered") {
      const [refund, l2] = await Promise.all([
        getPendingRefund(threadId),
        getPendingL2(threadId),
      ]);
      const l2Cards = selectPendingL2Cards(l2);
      setPendingRefund(refund.pending ? refund.refund_preview ?? null : null);
      setPendingUpgrade(l2Cards.upgrade);
      setPendingMemory(l2Cards.memory);
    }
  }

  /** 关闭当前 Run 事件订阅。 */
  function stopFollowing(): void {
    closeRef.current?.();
    closeRef.current = null;
    setProgress(null);
  }

  /** 订阅指定 Run，并在终态重新读取服务端事实。 */
  function follow(runId: string): void {
    if (threadId === undefined) {
      return;
    }
    stopFollowing();
    setProgress("正在处理你的请求…");
    closeRef.current = subscribeRunEvents(
      threadId,
      runId,
      (event: RunEvent) => {
        const message = event.payload.message;
        if (event.event_type === "step.updated" && typeof message === "string") {
          setProgress(message);
        }
      },
      () => {
        closeRef.current = null;
        setProgress(null);
        void refresh();
      },
      () => setProgress("连接暂时中断，正在恢复处理进度…"),
    );
  }

  useEffect(() => stopFollowing, [threadId]);

  useEffect(() => {
    /** 会话切换或刷新后从服务端恢复待审批动作。 */
    async function restorePendingActions(): Promise<void> {
      setOptimistic([]);
      setTransient([]);
      setPendingRefund(null);
      setPendingUpgrade(null);
      setPendingMemory(null);
      if (threadId === undefined || session.mode !== "registered") {
        return;
      }
      try {
        const [refund, l2] = await Promise.all([
          getPendingRefund(threadId),
          getPendingL2(threadId),
        ]);
        const l2Cards = selectPendingL2Cards(l2);
        setPendingRefund(refund.pending ? refund.refund_preview ?? null : null);
        setPendingUpgrade(l2Cards.upgrade);
        setPendingMemory(l2Cards.memory);
      } catch {
        setTransient([
          {
            id: `restore-error-${Date.now()}`,
            role: "assistant",
            content: "这段服务记录暂时无法恢复，请返回服务中心后重试。",
          },
        ]);
      }
    }

    void restorePendingActions();
  }, [session.mode, threadId]);

  useEffect(() => {
    const latest = [...(history.data?.messages ?? [])]
      .reverse()
      .find((item) => item.role === "assistant");
    const response = latest === undefined ? undefined : responseFromMessage(latest);
    if (response !== undefined) {
      setPendingRefund(response.refund_preview ?? null);
      setPendingUpgrade(
        response.l2_pending_action === "upgrade_confirmation"
          ? response.l2_upgrade_preview ?? null
          : null,
      );
      setPendingMemory(
        response.l2_pending_action === "memory_confirmation"
          ? response.memory_proposal ?? null
          : null,
      );
    }
  }, [history.data]);

  /** 把失败追加为普通客服消息。 */
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

  const messageMutation = useMutation({
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
    },
    onSuccess: (accepted) => follow(accepted.run.run_id),
    onError: (error) => {
      setOptimistic([]);
      appendAssistantError(error);
    },
  });
  const refundMutation = useMutation({
    mutationFn: async (decision: "approve" | "reject") => {
      if (threadId === undefined || pendingRefund === null) {
        throw new Error("当前没有待审批退款");
      }
      return decideRefund(threadId, pendingRefund.action_id, decision);
    },
    onSuccess: (accepted) => {
      setPendingRefund(null);
      follow(accepted.run.run_id);
    },
    onError: appendAssistantError,
  });
  const upgradeMutation = useMutation({
    mutationFn: async (decision: "confirm" | "cancel") => {
      if (threadId === undefined || pendingUpgrade === null) {
        throw new Error("当前没有待确认升级");
      }
      return decideL2Upgrade(threadId, pendingUpgrade.preview_id, decision);
    },
    onSuccess: (accepted) => {
      setPendingUpgrade(null);
      follow(accepted.run.run_id);
    },
    onError: appendAssistantError,
  });
  const memoryMutation = useMutation({
    mutationFn: async (decision: "confirm" | "reject") => {
      if (threadId === undefined || pendingMemory === null) {
        throw new Error("当前没有待确认偏好");
      }
      return decideL2Memory(threadId, pendingMemory.proposal_id, decision);
    },
    onSuccess: (accepted) => {
      setPendingMemory(null);
      follow(accepted.run.run_id);
    },
    onError: appendAssistantError,
  });

  const messages = [
    ...(history.data?.messages.map(toDisplayMessage) ?? []),
    ...optimistic,
    ...transient,
  ];

  return {
    messages,
    pendingRefund,
    pendingUpgrade,
    pendingMemory,
    progress,
    isLoading: history.isPending && threadId !== undefined,
    isSubmitting:
      messageMutation.isPending ||
      refundMutation.isPending ||
      upgradeMutation.isPending ||
      memoryMutation.isPending,
    submit: (message) =>
      messageMutation.mutate({ id: newClientMessageId(), content: message }),
    decideRefundAction: (decision) => refundMutation.mutate(decision),
    decideUpgradeAction: (decision) => upgradeMutation.mutate(decision),
    decideMemoryAction: (decision) => memoryMutation.mutate(decision),
  };
}
