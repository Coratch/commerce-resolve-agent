import type {
  AdminAuditRecord,
  AdminCustomer,
  AdminEvalSnapshot,
  AdminInvitation,
  AdminInvitationCreated,
  AdminInvitationInput,
  AdminOverview,
  AdminRunDetail,
  AdminRunSummary,
  AdminSystemSnapshot,
  AgentRun,
  ApiErrorBody,
  ChatResponse,
  ConversationResponse,
  ConversationListResponse,
  ConversationMessagesResponse,
  ConversationSummary,
  DemoWorkspaceStatus,
  MemoryValue,
  PendingL2Response,
  PendingRefundResponse,
  PublicCustomerPreference,
  PublicL2CaseDetail,
  PublicL2CaseSummary,
  PublicL2TracePage,
  PublicL2UpgradePreview,
  PublicMemoryProposal,
  PublicRefundPreview,
  RunAcceptedResponse,
  RunEvent,
  ServiceRecordDetail,
  SessionResponse,
  SupportOrderDetail,
  SupportOrdersPage,
  SupportOverview,
  SupportServicesPage,
  WorkspaceResetResult,
} from "./types";

let csrfToken = "";

export class ApiError extends Error {
  readonly status: number;
  readonly errorCode: string;

  /** 保存服务端公开错误，不暴露原始响应内部信息。 */
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = body.error_code;
  }
}

/** 更新仅保存在页面内存中的同步 CSRF Token。 */
export function setCsrfToken(value: string | null | undefined): void {
  csrfToken = value ?? "";
}

/** 清理页面内存中的旧 CSRF Token。 */
export function clearCsrfToken(): void {
  csrfToken = "";
}

/** 执行同源 JSON 请求，并把公开错误转换为统一异常。 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (init.method !== undefined && init.method !== "GET" && csrfToken !== "") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  const payload = (await response.json()) as unknown;
  if (!response.ok) {
    throw new ApiError(response.status, payload as ApiErrorBody);
  }
  return payload as T;
}

/** 获取当前 Session，并替换页面内存中的 CSRF Token。 */
export async function getSession(): Promise<SessionResponse> {
  const session = await request<SessionResponse>("/api/session");
  setCsrfToken(session.csrf_token);
  return session;
}

/** 消费邀请码创建账号，但不自动建立登录 Session。 */
export async function register(input: {
  username: string;
  password: string;
  invitation_code: string;
}): Promise<{ username: string; status: "registered" }> {
  return request("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** 登录并保存服务端轮换后的 CSRF Token。 */
export async function login(input: {
  username: string;
  password: string;
}): Promise<SessionResponse> {
  const session = await request<SessionResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
  setCsrfToken(session.csrf_token);
  return session;
}

/** 退出注册账号并切换到全新的游客 Session。 */
export async function logout(): Promise<SessionResponse> {
  const session = await request<SessionResponse>("/api/auth/logout", {
    method: "POST",
  });
  clearCsrfToken();
  localStorage.removeItem("commerce-resolve-thread");
  return session;
}

/** 为当前身份创建普通会话，或创建与指定订单绑定的售后会话。 */
export async function createConversation(
  relatedOrderId: string,
): Promise<ConversationResponse> {
  return request("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ related_order_id: relatedOrderId }),
  });
}

/** 读取当前账号版本化演示工作区的有限公开状态。 */
export async function getDemoWorkspace(): Promise<DemoWorkspaceStatus> {
  return request("/api/demo-workspace");
}

/** 经明确确认完整重置本人演示工作区。 */
export async function resetDemoWorkspace(
  clientRequestId: string,
): Promise<WorkspaceResetResult> {
  return request("/api/demo-workspace/reset", {
    method: "POST",
    body: JSON.stringify({
      client_request_id: clientRequestId,
      confirmation: "RESET",
    }),
  });
}

/** 读取当前身份的售后首页只读投影。 */
export async function getSupportOverview(): Promise<SupportOverview> {
  return request("/api/support/overview");
}

/** 分页读取当前身份可见的客户订单。 */
export async function listSupportOrders(input?: {
  cursor?: string;
  q?: string;
  view?: "all" | "processing" | "shipping" | "delivered" | "after_sales";
}): Promise<SupportOrdersPage> {
  const query = new URLSearchParams();
  if (input?.cursor !== undefined) query.set("cursor", input.cursor);
  if (input?.q !== undefined && input.q.trim() !== "") query.set("q", input.q.trim());
  if (input?.view !== undefined) query.set("view", input.view);
  const suffix = query.size === 0 ? "" : `?${query.toString()}`;
  return request(`/api/support/orders${suffix}`);
}

/** 读取当前身份有权访问的订单详情。 */
export async function getSupportOrder(orderId: string): Promise<SupportOrderDetail> {
  return request(`/api/support/orders/${encodeURIComponent(orderId)}`);
}

/** 分页读取进行中或历史客户服务。 */
export async function listSupportServices(
  view: "active" | "history",
  cursor?: string,
): Promise<SupportServicesPage> {
  const query = new URLSearchParams({ view });
  if (cursor !== undefined) {
    query.set("cursor", cursor);
  }
  return request(`/api/support/services?${query.toString()}`);
}

/** 读取一条当前身份可见的客户服务详情。 */
export async function getSupportService(
  serviceId: string,
): Promise<ServiceRecordDetail> {
  return request(`/api/support/services/${encodeURIComponent(serviceId)}`);
}

/** 列出当前身份可见的活动或归档会话。 */
export async function listConversations(
  lifecycleStatus: "active" | "archived" = "active",
): Promise<ConversationListResponse> {
  return request(
    `/api/conversations?lifecycle_status=${encodeURIComponent(lifecycleStatus)}`,
  );
}

/** 读取指定会话的公开消息历史。 */
export async function listConversationMessages(
  threadId: string,
): Promise<ConversationMessagesResponse> {
  return request(`/api/conversations/${encodeURIComponent(threadId)}/messages`);
}

/** 原子接受一条用户消息，并返回独立执行的 Agent Run。 */
export async function submitConversationMessage(
  threadId: string,
  clientMessageId: string,
  message: string,
): Promise<RunAcceptedResponse> {
  return request(`/api/conversations/${encodeURIComponent(threadId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ client_message_id: clientMessageId, message }),
  });
}

/** 查询指定会话中一条 Agent Run 的当前状态。 */
export async function getAgentRun(
  threadId: string,
  runId: string,
): Promise<AgentRun> {
  const response = await request<{ run: AgentRun }>(
    `/api/conversations/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}`,
  );
  return response.run;
}

/** 订阅可重放的公开 Run 事件，并返回关闭订阅的方法。 */
export function subscribeRunEvents(
  threadId: string,
  runId: string,
  onEvent: (event: RunEvent) => void,
  onTerminal: () => void,
  onError: () => void,
): () => void {
  const source = new EventSource(
    `/api/conversations/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/events`,
    { withCredentials: true },
  );
  const eventTypes: RunEvent["event_type"][] = [
    "run.accepted",
    "run.started",
    "step.updated",
    "action.required",
    "message.completed",
    "run.completed",
    "run.failed",
    "run.interrupted",
  ];
  const terminal = new Set<RunEvent["event_type"]>([
    "action.required",
    "run.completed",
    "run.failed",
    "run.interrupted",
  ]);
  for (const eventType of eventTypes) {
    source.addEventListener(eventType, (raw) => {
      const event = JSON.parse((raw as MessageEvent<string>).data) as RunEvent;
      onEvent(event);
      if (terminal.has(event.event_type)) {
        source.close();
        onTerminal();
      }
    });
  }
  source.onerror = () => onError();
  return () => source.close();
}

/** 归档或恢复当前注册用户自己的会话。 */
export async function updateConversationLifecycle(
  threadId: string,
  lifecycleStatus: "active" | "archived",
): Promise<ConversationSummary> {
  const response = await request<{ conversation: ConversationSummary }>(
    `/api/conversations/${encodeURIComponent(threadId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ lifecycle_status: lifecycleStatus }),
    },
  );
  return response.conversation;
}

/** 删除当前身份可访问且没有活动动作的会话。 */
export async function deleteConversation(threadId: string): Promise<void> {
  const headers = new Headers({ "X-CSRF-Token": csrfToken });
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(threadId)}`,
    { method: "DELETE", headers, credentials: "same-origin" },
  );
  if (!response.ok) {
    const payload = (await response.json()) as ApiErrorBody;
    throw new ApiError(response.status, payload);
  }
}

/** 向已授权 conversation 提交一条文本消息。 */
export async function sendChatMessage(
  threadId: string,
  message: string,
): Promise<ChatResponse> {
  return request("/api/chat/messages", {
    method: "POST",
    body: JSON.stringify({ thread_id: threadId, message }),
  });
}

/** 查询当前 conversation 是否有可恢复的待审批退款预览。 */
export async function getPendingRefund(
  threadId: string,
): Promise<PendingRefundResponse> {
  return request(
    `/api/conversations/${encodeURIComponent(threadId)}/pending-refund`,
  );
}

/** 查询当前 conversation 是否有可恢复的 L2 结构化待处理动作。 */
export async function getPendingL2(
  threadId: string,
): Promise<PendingL2Response> {
  return request(`/api/conversations/${encodeURIComponent(threadId)}/pending-l2`);
}

/** 只提交升级预览标识与确认或取消决定。 */
export async function decideL2Upgrade(
  threadId: string,
  previewId: PublicL2UpgradePreview["preview_id"],
  decision: "confirm" | "cancel",
): Promise<RunAcceptedResponse> {
  return request(
    `/api/conversations/${encodeURIComponent(threadId)}/l2-upgrade-decision`,
    {
      method: "POST",
      body: JSON.stringify({ preview_id: previewId, decision }),
    },
  );
}

/** 只提交当前 Case 的偏好建议标识与确认或拒绝决定。 */
export async function decideL2Memory(
  threadId: string,
  proposalId: PublicMemoryProposal["proposal_id"],
  decision: "confirm" | "reject",
): Promise<RunAcceptedResponse> {
  return request(
    `/api/conversations/${encodeURIComponent(threadId)}/l2-memory-decision`,
    {
      method: "POST",
      body: JSON.stringify({ proposal_id: proposalId, decision }),
    },
  );
}

/** 列出当前账号最近的 L2 Case 摘要。 */
export async function listL2Cases(
  threadId?: string,
): Promise<PublicL2CaseSummary[]> {
  const query = threadId === undefined ? "" : `?thread_id=${encodeURIComponent(threadId)}`;
  const response = await request<{ cases: PublicL2CaseSummary[] }>(
    `/api/l2-cases${query}`,
  );
  return response.cases;
}

/** 读取本人指定 L2 Case 的公开状态与脱敏轨迹。 */
export async function getL2Case(caseId: string): Promise<PublicL2CaseDetail> {
  return request(`/api/l2-cases/${encodeURIComponent(caseId)}`);
}

/** 按 Case 内单调序号只读分页公开 Trace，不触发 Agent 执行。 */
export async function getL2CaseTrace(
  caseId: string,
  afterSequence = 0,
  limit = 50,
): Promise<PublicL2TracePage> {
  const query = new URLSearchParams({
    after_sequence: String(afterSequence),
    limit: String(limit),
  });
  return request(
    `/api/l2-cases/${encodeURIComponent(caseId)}/trace?${query.toString()}`,
  );
}

/** 列出当前账号明确确认的受限长期偏好。 */
export async function listMemories(): Promise<PublicCustomerPreference[]> {
  const response = await request<{ memories: PublicCustomerPreference[] }>(
    "/api/memories",
  );
  return response.memories;
}

/** 纠正本人既有偏好的受限枚举值。 */
export async function updateMemory(
  memoryId: string,
  value: MemoryValue,
): Promise<PublicCustomerPreference> {
  return request(`/api/memories/${encodeURIComponent(memoryId)}`, {
    method: "PATCH",
    body: JSON.stringify({ value }),
  });
}

/** 删除本人指定长期偏好。 */
export async function deleteMemory(memoryId: string): Promise<void> {
  await request(`/api/memories/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
  });
}

/** 只提交服务端 action 标识与批准或拒绝决定。 */
export async function decideRefund(
  threadId: string,
  actionId: PublicRefundPreview["action_id"],
  decision: "approve" | "reject",
): Promise<RunAcceptedResponse> {
  return request(
    `/api/conversations/${encodeURIComponent(threadId)}/refund-approval`,
    {
      method: "POST",
      body: JSON.stringify({ action_id: actionId, decision }),
    },
  );
}

/** 读取运营控制台可见的有限客户目录。 */
export async function listAdminCustomers(): Promise<AdminCustomer[]> {
  return request("/api/admin/customers");
}

/** 为明确目标客户完整重置版本化演示工作区。 */
export async function resetAdminDemoWorkspace(
  userId: string,
  clientRequestId: string,
): Promise<WorkspaceResetResult> {
  return request(
    `/api/admin/customers/${encodeURIComponent(userId)}/demo-workspace/reset`,
    {
      method: "POST",
      body: JSON.stringify({
        client_request_id: clientRequestId,
        confirmation: "RESET",
      }),
    },
  );
}

/** 列出不含明文和 Hash 的邀请码状态。 */
export async function listAdminInvitations(): Promise<AdminInvitation[]> {
  return request("/api/admin/invitations");
}

/** 创建邀请码并仅在本次调用中接收明文。 */
export async function createAdminInvitation(
  input: AdminInvitationInput,
): Promise<AdminInvitationCreated> {
  return request("/api/admin/invitations", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** 撤销指定邀请码，不读取或传输明文。 */
export async function revokeAdminInvitation(invitationId: string): Promise<void> {
  await request(`/api/admin/invitations/${encodeURIComponent(invitationId)}`, {
    method: "DELETE",
  });
}

/** 读取后台业务写入的脱敏审计。 */
export async function listAdminAudit(): Promise<AdminAuditRecord[]> {
  return request("/api/admin/audit");
}

/** 读取运营概览的权威计数和只读状态。 */
export async function getAdminOverview(): Promise<AdminOverview> {
  return request("/api/admin/overview");
}

/** 按有限条件读取脱敏 Agent Run 列表。 */
export async function listAdminRuns(input?: {
  status?: string;
  requestKind?: string;
}): Promise<AdminRunSummary[]> {
  const query = new URLSearchParams();
  if (input?.status) query.set("status", input.status);
  if (input?.requestKind) query.set("request_kind", input.requestKind);
  const suffix = query.size === 0 ? "" : `?${query.toString()}`;
  return request(`/api/admin/agent-runs${suffix}`);
}

/** 读取一条 Run 的事件白名单和有限 L2 诊断。 */
export async function getAdminRun(runId: string): Promise<AdminRunDetail> {
  return request(`/api/admin/agent-runs/${encodeURIComponent(runId)}`);
}

/** 读取最近 Eval Candidate 与当前 Baseline 的只读摘要。 */
export async function getAdminEval(): Promise<AdminEvalSnapshot> {
  return request("/api/admin/eval");
}

/** 读取不含路径、密钥和配置值的系统状态。 */
export async function getAdminSystem(): Promise<AdminSystemSnapshot> {
  return request("/api/admin/system");
}
