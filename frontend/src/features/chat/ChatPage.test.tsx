import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "../../api/client";
import type {
  ConversationMessage,
  ConversationSummary,
  SessionResponse,
} from "../../api/types";
import { ChatPage } from "./ChatPage";

const api = vi.hoisted(() => ({
  createConversation: vi.fn(),
  decideL2Memory: vi.fn(),
  decideL2Upgrade: vi.fn(),
  decideRefund: vi.fn(),
  deleteConversation: vi.fn(),
  getL2Case: vi.fn(),
  getL2CaseTrace: vi.fn(),
  getPendingL2: vi.fn(),
  getPendingRefund: vi.fn(),
  listConversationMessages: vi.fn(),
  listConversations: vi.fn(),
  listL2Cases: vi.fn(),
  submitConversationMessage: vi.fn(),
  subscribeRunEvents: vi.fn(),
  updateConversationLifecycle: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return { ...original, ...api };
});

const threadId = "00000000-0000-0000-0000-000000000001";
const guestSession: SessionResponse = {
  mode: "guest",
  username: null,
  session_scope: "browser",
  csrf_token: "csrf",
  expires_at: "2026-07-17T12:00:00Z",
  capabilities: {
    can_manage_orders: false,
    can_manage_refunds: false,
    can_use_llm: false,
  },
};
const registeredSession: SessionResponse = {
  ...guestSession,
  mode: "registered",
  username: "refund.user",
  session_scope: "account",
  capabilities: {
    can_manage_orders: true,
    can_manage_refunds: true,
    can_use_llm: true,
  },
};

/** 构造前端测试使用的完整会话摘要。 */
function conversation(id = threadId): ConversationSummary {
  return {
    thread_id: id,
    title: "订单查询",
    lifecycle_status: "active",
    history_state: "complete",
    message_count: 0,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:00Z",
  };
}

/** 构造一条符合服务端公开 Schema 的持久消息。 */
function persistedMessage(
  id: string,
  role: "user" | "assistant",
  content: string,
  sequence: number,
  payload: Record<string, unknown> = {},
): ConversationMessage {
  return {
    message_id: id,
    thread_id: threadId,
    sequence_no: sequence,
    role,
    kind: "text",
    content,
    status: "completed",
    payload_version: 1,
    payload,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:00Z",
  };
}

/** 使用隔离 QueryClient 和带 thread 参数的路由渲染 Chat 页面。 */
function renderChat(
  session: SessionResponse = guestSession,
  selectedThread = threadId,
): void {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/chat/${selectedThread}`]}>
        <Routes>
          <Route
            path="/chat/:threadId"
            element={<ChatPage session={session} />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const mock of Object.values(api)) {
    mock.mockReset();
  }
  api.listConversations.mockResolvedValue({
    conversations: [conversation()],
    next_cursor: null,
  });
  api.listConversationMessages.mockResolvedValue({
    messages: [],
    history_state: "complete",
    next_after_sequence: null,
  });
  api.getPendingL2.mockResolvedValue({ pending: false, public_status: "none" });
  api.getPendingRefund.mockResolvedValue({
    pending: false,
    public_status: "none",
  });
  api.listL2Cases.mockResolvedValue([]);
  api.subscribeRunEvents.mockImplementation(
    (_thread, _run, onEvent, onTerminal) => {
      onEvent({
        event_id: 2,
        run_id: "run-1",
        event_type: "step.updated",
        payload_version: 1,
        payload: { message: "正在查询受控数据…" },
        created_at: "2026-07-21T00:00:00Z",
      });
      queueMicrotask(onTerminal);
      return vi.fn();
    },
  );
  localStorage.clear();
});

afterEach(() => cleanup());

it("刷新后直接展示服务端持久历史并安全渲染纯文本", async () => {
  api.listConversationMessages.mockResolvedValue({
    messages: [
      persistedMessage("m1", "user", "你好", 1),
      persistedMessage("m2", "assistant", "<script>bad()</script>", 2),
    ],
    history_state: "complete",
  });

  renderChat();

  expect(await screen.findByText("你好")).toBeInTheDocument();
  expect(screen.getByText("<script>bad()</script>")).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
});

it("提交 202 Run 后订阅 SSE，并从服务端刷新最终助手消息", async () => {
  api.listConversationMessages
    .mockResolvedValueOnce({ messages: [], history_state: "complete" })
    .mockResolvedValue({
      messages: [
        persistedMessage("m1", "user", "查询 ORD-001", 1),
        persistedMessage("m2", "assistant", "订单正在运输中。", 2),
      ],
      history_state: "complete",
    });
  api.submitConversationMessage.mockResolvedValue({
    run: {
      run_id: "run-1",
      thread_id: threadId,
      client_request_id: "client-1",
      request_kind: "chat_message",
      status: "accepted",
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-21T00:00:00Z",
    },
    user_message: persistedMessage("m1", "user", "查询 ORD-001", 1),
    reused: false,
  });
  const user = userEvent.setup();
  renderChat();

  await user.type(screen.getByLabelText("输入售后问题"), "查询 ORD-001");
  await user.keyboard("{Enter}");

  expect(api.submitConversationMessage).toHaveBeenCalledWith(
    threadId,
    expect.any(String),
    "查询 ORD-001",
  );
  expect(api.subscribeRunEvents).toHaveBeenCalledWith(
    threadId,
    "run-1",
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
  );
  expect(await screen.findByText("订单正在运输中。")).toBeInTheDocument();
});

it("使用 Enter 发送并保留 Shift+Enter 换行", async () => {
  api.submitConversationMessage.mockResolvedValue({
    run: {
      run_id: "run-1",
      thread_id: threadId,
      client_request_id: "client-1",
      request_kind: "chat_message",
      status: "accepted",
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-21T00:00:00Z",
    },
    user_message: persistedMessage("m1", "user", "第一行\n第二行", 1),
    reused: false,
  });
  const user = userEvent.setup();
  renderChat();
  const textarea = screen.getByLabelText("输入售后问题");

  await user.type(textarea, "第一行");
  await user.keyboard("{Shift>}{Enter}{/Shift}");
  await user.type(textarea, "第二行");
  expect(textarea).toHaveValue("第一行\n第二行");
  await user.keyboard("{Enter}");

  expect(api.submitConversationMessage).toHaveBeenCalledWith(
    threadId,
    expect.any(String),
    "第一行\n第二行",
  );
});

it("把接受请求失败显示为普通助手对话而非红色异常", async () => {
  api.submitConversationMessage.mockRejectedValue(
    new ApiError(409, {
      error_code: "thread_busy",
      message: "当前会话还有请求正在处理，请稍后再试。",
    }),
  );
  const user = userEvent.setup();
  renderChat(registeredSession);

  await user.type(screen.getByLabelText("输入售后问题"), "转人工");
  await user.keyboard("{Enter}");

  const reply = await screen.findByText("当前会话还有请求正在处理，请稍后再试。");
  expect(reply.closest("article")?.className).toContain("assistant");
  expect(reply.closest("article")?.getAttribute("role")).not.toBe("alert");
});

it("刷新后恢复退款审批卡并只提交绑定动作", async () => {
  const preview = {
    action_id: "10000000-0000-0000-0000-000000000003",
    order_id: "ORD-REFUND",
    reason_code: "quality_issue",
    reason_detail: "",
    amount: "129.90",
    currency: "CNY" as const,
    channel: "mock_card" as const,
    order_status: "processing" as const,
    shipment_status: "preparing" as const,
    payment_status: "settled" as const,
    risk: "R2" as const,
    citations: [],
  };
  api.getPendingRefund.mockResolvedValue({
    pending: true,
    public_status: "refund_awaiting_approval",
    refund_preview: preview,
  });
  api.decideRefund.mockResolvedValue({
    run: {
      run_id: "decision-run-1",
      thread_id: threadId,
      client_request_id: "refund-decision-1",
      request_kind: "refund_decision",
      status: "accepted",
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-21T00:00:00Z",
    },
    user_message: persistedMessage("decision-user-1", "user", "拒绝退款", 3),
    reused: false,
  });
  const user = userEvent.setup();
  renderChat(registeredSession);

  expect(await screen.findByText("退款 ¥129.90")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "拒绝" }));

  expect(api.decideRefund).toHaveBeenCalledWith(
    threadId,
    preview.action_id,
    "reject",
  );
  expect(api.subscribeRunEvents).toHaveBeenCalledWith(
    threadId,
    "decision-run-1",
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
  );
});

it("注册用户可以从侧栏切换服务端会话", async () => {
  const another = "00000000-0000-0000-0000-000000000002";
  api.listConversations.mockResolvedValue({
    conversations: [conversation(), { ...conversation(another), title: "退款咨询" }],
  });
  const user = userEvent.setup();
  renderChat(registeredSession);

  await user.click(await screen.findByRole("button", { name: /退款咨询/ }));

  expect(localStorage.getItem("commerce-resolve-thread")).toBe(another);
});

it("注册用户刷新后从服务端恢复 Case 并按序分页公开 Trace", async () => {
  const caseId = "case-v07";
  const summary = {
    case_id: caseId,
    thread_id: threadId,
    related_order_id: "ORD-TRACE",
    issue_summary: "核对物流变化",
    status: "l2_resolved",
    stop_reason: null,
    trace_state: "complete",
    context_policy_version: "v0.7",
    failure_attribution: null,
    steps_used: 2,
    model_calls_used: 1,
    tool_calls_used: 1,
    final_response: "已核对最新物流。",
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:01Z",
  };
  const firstEvent = {
    sequence_no: 1,
    payload_version: 1,
    step_number: 1,
    event_type: "context.prepared",
    tool_category: null,
    risk: null,
    parameter_summary: null,
    result_code: "context_ready",
    evidence_ids: ["shipment:ORD-TRACE:in_transit"],
    duration_ms: 3,
    context_summary: {
      source_types: ["business_observation" as const],
      selected_count: 1,
      public_evidence_ids: ["shipment:ORD-TRACE:in_transit"],
      truncated: true,
      facts_refreshed: 1,
      state_changed: true,
      essential_complete: true,
    },
    created_at: "2026-07-21T00:00:00Z",
  };
  const secondEvent = {
    ...firstEvent,
    sequence_no: 2,
    event_type: "model.completed",
    result_code: "answer",
    context_summary: null,
  };
  api.listL2Cases.mockResolvedValue([summary]);
  api.getL2Case.mockResolvedValue({
    case: summary,
    events: [firstEvent],
    metrics: {
      steps: 2,
      model_calls: 1,
      tool_calls: 1,
      candidate_count: 20,
      selected_count: 5,
      duplicate_count: 1,
      stale_count: 1,
      conflict_count: 0,
      truncated_count: 14,
      candidate_estimated_tokens: 400,
      selected_estimated_tokens: 120,
      provider_input_tokens: 110,
      provider_output_tokens: 20,
      usage_sources: ["provider"],
      context_duration_ms: 3,
      model_duration_ms: 12,
      tool_duration_ms: 4,
      case_duration_ms: 25,
    },
    next_after_sequence: 1,
    has_more: true,
  });
  api.getL2CaseTrace.mockResolvedValue({
    case_id: caseId,
    trace_state: "complete",
    events: [firstEvent, secondEvent],
    next_after_sequence: null,
    has_more: false,
  });
  const user = userEvent.setup();

  renderChat(registeredSession);

  expect(await screen.findByText("l2_resolved")).toBeInTheDocument();
  expect(screen.getByText(/上下文 5\/20 项/)).toBeInTheDocument();
  expect(screen.getByText(/刷新 1 项/)).toBeInTheDocument();
  expect(screen.getByText(/已裁剪/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "加载更多处理记录" }));

  expect(api.getL2CaseTrace).toHaveBeenCalledWith(caseId, 1);
  expect(await screen.findByText("model.completed")).toBeInTheDocument();
  expect(screen.getAllByText(/#1/)).toHaveLength(1);
});
