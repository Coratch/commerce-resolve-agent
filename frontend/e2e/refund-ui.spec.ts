import { expect, test, type Page, type Route } from "@playwright/test";

const DIST_ROOT = "dist";
const ORDER_ID = "CR-7K4M-Q2PX";
const THREAD_ID = "11111111-1111-4111-8111-111111111111";
const ACTION_ID = "22222222-2222-4222-8222-222222222222";
const CHAT_RUN_ID = "33333333-3333-4333-8333-333333333333";
const DECISION_RUN_ID = "44444444-4444-4444-8444-444444444444";
const NOW = "2026-07-24T08:00:00Z";
let uiState: "empty" | "preview" | "completed" = "empty";
let sessionMode: "anonymous" | "registered" = "registered";
let anonymousBusinessCalls = 0;

const order = {
  order_id: ORDER_ID,
  status: "processing",
  item_count: 1,
  item_title_preview: "旅途降噪耳机",
  preview_items: [
    {
      title: "旅途降噪耳机",
      image_url: "/catalog/v1.3/headphones.webp",
      image_alt: "旅途降噪耳机",
    },
  ],
  shipment_status: "preparing",
  fulfillment_summary: "等待仓库处理",
  customer_stage: "待发货",
  estimated_delivery_at: "2026-07-27",
  payment_amount: "129.90",
  latest_service_status: null,
  latest_service_summary: null,
  created_at: NOW,
  updated_at: NOW,
};

const citation = {
  document_id: "refunds-v1",
  title: "退款政策",
  version: "1.0",
  effective_from: "2026-01-01",
  effective_to: null,
  section_id: "refund-eligibility-pre-fulfillment",
  heading: "发货前直接退款",
  source_relative_path: "refunds-v1.md",
  line_start: 10,
  line_end: 16,
  content_hash: "a".repeat(64),
};

/** 返回当前 V2.0 登录状态，不为匿名访问创建业务 Session。 */
function sessionPayload(): Record<string, unknown> {
  if (sessionMode === "anonymous") {
    return {
      mode: "anonymous",
      username: null,
      role: null,
      session_scope: "none",
      csrf_token: null,
      expires_at: null,
      capabilities: {
        can_manage_orders: false,
        can_manage_refunds: false,
        can_use_llm: false,
        can_access_admin: false,
      },
    };
  }
  return {
    mode: "registered",
    username: "browser.user",
    role: "customer",
    session_scope: "account",
    csrf_token: "browser-csrf-token",
    expires_at: "2030-01-01T00:00:00Z",
    capabilities: {
      can_manage_orders: false,
      can_manage_refunds: true,
      can_use_llm: true,
      can_access_admin: false,
    },
  };
}

/** 构造当前退款阶段对应的公开持久消息。 */
function conversationMessages(): Record<string, unknown>[] {
  if (uiState === "empty") {
    return [];
  }
  const previewPayload = {
    public_status: "refund_awaiting_approval",
    citations: [citation],
    refund_preview: {
      action_id: ACTION_ID,
      order_id: ORDER_ID,
      reason_code: "quality_issue",
      reason_detail: "商品存在质量问题",
      amount: "129.90",
      currency: "CNY",
      channel: "mock_card",
      order_status: "processing",
      shipment_status: "preparing",
      payment_status: "settled",
      risk: "R2",
      citations: [citation],
    },
    l2_trace_events: [],
  };
  const messages: Record<string, unknown>[] = [
    {
      message_id: "message-user-1",
      thread_id: THREAD_ID,
      run_id: CHAT_RUN_ID,
      sequence_no: 1,
      role: "user",
      kind: "text",
      content: "商品有质量问题，请退款",
      status: "accepted",
      payload_version: 1,
      payload: {},
      created_at: NOW,
      updated_at: NOW,
    },
    {
      message_id: "message-assistant-1",
      thread_id: THREAD_ID,
      run_id: CHAT_RUN_ID,
      sequence_no: 2,
      role: "assistant",
      kind: "action",
      content: "退款预览已生成；确认后才会写入本地演示退款记录。",
      status: "completed",
      payload_version: 1,
      payload: previewPayload,
      created_at: NOW,
      updated_at: NOW,
    },
  ];
  if (uiState === "completed") {
    messages.push(
      {
        message_id: "message-user-2",
        thread_id: THREAD_ID,
        run_id: DECISION_RUN_ID,
        sequence_no: 3,
        role: "user",
        kind: "action",
        content: "确认演示退款",
        status: "accepted",
        payload_version: 1,
        payload: {},
        created_at: NOW,
        updated_at: NOW,
      },
      {
        message_id: "message-assistant-2",
        thread_id: THREAD_ID,
        run_id: DECISION_RUN_ID,
        sequence_no: 4,
        role: "assistant",
        kind: "text",
        content: "演示退款已完成并验证：RFN-BROWSER-001。",
        status: "completed",
        payload_version: 1,
        payload: {
          public_status: "refund_completed",
          citations: [citation],
          refund_result: {
            action_id: ACTION_ID,
            refund_id: "RFN-BROWSER-001",
            amount: "129.90",
            status: "succeeded",
            verified: true,
            result_code: "verified",
          },
          l2_trace_events: [],
        },
        created_at: NOW,
        updated_at: NOW,
      },
    );
  }
  return messages;
}

/** 返回固定 JSON，以无端口 Chromium 验证 V2.0 产品契约。 */
async function fulfillCommerceResolveRoute(route: Route): Promise<void> {
  const request = route.request();
  const pathname = request
    .url()
    .replace("http://commerce-resolve.test", "")
    .split("?")[0];
  const businessRequest =
    pathname.startsWith("/api/support") ||
    pathname.startsWith("/api/conversations");
  if (sessionMode === "anonymous" && businessRequest) {
    anonymousBusinessCalls += 1;
  }
  if (pathname === "/api/session") {
    await route.fulfill({ json: sessionPayload() });
    return;
  }
  if (pathname === "/api/support/overview") {
    await route.fulfill({
      json: {
        active_services: [],
        recent_orders: [order],
        has_more_orders: false,
        has_more_services: false,
      },
    });
    return;
  }
  if (pathname === "/api/support/orders") {
    await route.fulfill({ json: { orders: [order], next_cursor: null } });
    return;
  }
  if (pathname === "/api/conversations" && request.method() === "POST") {
    await route.fulfill({
      status: 201,
      json: {
        thread_id: THREAD_ID,
        related_order_id: ORDER_ID,
        created: uiState === "empty",
      },
    });
    return;
  }
  if (pathname.endsWith("/messages") && request.method() === "GET") {
    await route.fulfill({
      json: {
        messages: conversationMessages(),
        history_state: "complete",
        next_after_sequence: null,
      },
    });
    return;
  }
  if (pathname.endsWith("/pending-refund")) {
    const preview = conversationMessages()[1]?.payload as
      | { refund_preview?: unknown }
      | undefined;
    await route.fulfill({
      json: {
        pending: uiState === "preview",
        public_status:
          uiState === "preview" ? "refund_awaiting_approval" : "none",
        refund_preview:
          uiState === "preview" ? preview?.refund_preview ?? null : null,
      },
    });
    return;
  }
  if (pathname.endsWith("/pending-l2")) {
    await route.fulfill({ json: { pending: false, public_status: "none" } });
    return;
  }
  if (pathname.endsWith("/messages") && request.method() === "POST") {
    uiState = "preview";
    await route.fulfill({
      status: 202,
      json: {
        run: {
          run_id: CHAT_RUN_ID,
          thread_id: THREAD_ID,
          client_request_id: "client-chat-1",
          request_kind: "chat_message",
          status: "accepted",
          created_at: NOW,
          updated_at: NOW,
        },
        user_message: conversationMessages()[0],
        reused: false,
      },
    });
    return;
  }
  if (pathname.endsWith("/refund-approval")) {
    uiState = "completed";
    await route.fulfill({
      status: 202,
      json: {
        run: {
          run_id: DECISION_RUN_ID,
          thread_id: THREAD_ID,
          client_request_id: "client-decision-1",
          request_kind: "refund_decision",
          status: "accepted",
          created_at: NOW,
          updated_at: NOW,
        },
        user_message: conversationMessages()[2],
        reused: false,
      },
    });
    return;
  }
  if (pathname.endsWith("/events")) {
    const runId = pathname.includes(CHAT_RUN_ID) ? CHAT_RUN_ID : DECISION_RUN_ID;
    const eventType = runId === CHAT_RUN_ID ? "action.required" : "run.completed";
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      body:
        `id: 1\nevent: ${eventType}\ndata: ` +
        JSON.stringify({
          event_id: 1,
          run_id: runId,
          event_type: eventType,
          payload_version: 1,
          payload: {},
          created_at: NOW,
        }) +
        "\n\n",
    });
    return;
  }
  if (pathname.startsWith("/assets/")) {
    await route.fulfill({ path: `${DIST_ROOT}${pathname}` });
    return;
  }
  await route.fulfill({ path: `${DIST_ROOT}/index.html` });
}

/** 打开全局 Agent，并选择预置订单建立或恢复唯一任务。 */
async function openOrderAgent(page: Page): Promise<void> {
  await page.getByRole("button", { name: "打开智能售后助手" }).click();
  const drawer = page.getByRole("complementary", { name: "智能售后助手" });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("button", { name: /旅途降噪耳机/ }).click();
  await expect(
    drawer.getByRole("region", { name: "订单售后助手" }),
  ).toBeVisible();
}

/** 验证订单优先入口、Enter 发送、退款审批和回读结果。 */
async function refundApprovalTest({ page }: { page: Page }): Promise<void> {
  uiState = "empty";
  sessionMode = "registered";
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("http://commerce-resolve.test/**", fulfillCommerceResolveRoute);
  await page.goto("/support");

  await expect(
    page.getByRole("heading", { name: /browser\.user/ }),
  ).toBeVisible();
  await expect(page.getByText("旅途降噪耳机")).toBeVisible();
  await openOrderAgent(page);

  const composer = page.getByLabel("输入售后问题");
  await composer.fill("商品有质量问题，请退款");
  await composer.press("Enter");
  const preview = page.getByRole("complementary", { name: "待审批退款预览" });
  await expect(preview.getByText("¥129.90")).toBeVisible();
  await expect(composer).toBeDisabled();
  await preview.getByRole("button", { name: "确认演示退款" }).click();
  await expect(page.getByText(/演示退款已完成并验证/)).toBeVisible();
  await expect(preview).toHaveCount(0);
  await expect(composer).toBeEnabled();
}

test("注册用户从订单进入 Agent 并完成演示退款", refundApprovalTest);

/** 验证未登录访问只到登录页，且不会触发业务或模型入口。 */
async function anonymousBoundaryTest({ page }: { page: Page }): Promise<void> {
  uiState = "empty";
  sessionMode = "anonymous";
  anonymousBusinessCalls = 0;
  await page.route("http://commerce-resolve.test/**", fulfillCommerceResolveRoute);
  await page.goto("/support");

  await expect(
    page.getByRole("heading", { name: "登录私有工作区" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "打开智能售后助手" }),
  ).toHaveCount(0);
  expect(anonymousBusinessCalls).toBe(0);
}

test("匿名访问不会读取订单或创建会话", anonymousBoundaryTest);

/** 在浏览器页内读取根元素的可视宽度与横向滚动宽度。 */
function readViewportWidth(element: unknown): {
  clientWidth: number;
  scrollWidth: number;
} {
  const root = element as { clientWidth: number; scrollWidth: number };
  return { clientWidth: root.clientWidth, scrollWidth: root.scrollWidth };
}

/** 在浏览器页内把元素首个 CSS 动画时长转换为毫秒。 */
function readAnimationDurationMs(element: unknown): number {
  const target = element as {
    ownerDocument: {
      defaultView: {
        getComputedStyle(node: unknown): { animationDuration: string };
      };
    };
  };
  const value = target.ownerDocument.defaultView
    .getComputedStyle(target)
    .animationDuration.split(",")[0];
  return value.endsWith("ms")
    ? Number.parseFloat(value)
    : Number.parseFloat(value) * 1000;
}

/** 验证移动端抽屉完整可用、无横向溢出并尊重减少动态效果。 */
async function mobileDrawerTest({ page }: { page: Page }): Promise<void> {
  uiState = "empty";
  sessionMode = "registered";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("http://commerce-resolve.test/**", fulfillCommerceResolveRoute);
  await page.goto("/support");
  await openOrderAgent(page);

  const viewport = await page.locator("html").evaluate(readViewportWidth);
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth);
  const composer = page.getByLabel("输入售后问题");
  await expect(composer).toBeInViewport();
  const drawer = page.getByRole("complementary", { name: "智能售后助手" });
  const animationDurationMs = await drawer.evaluate(readAnimationDurationMs);
  expect(animationDurationMs).toBeLessThanOrEqual(1);
}

test("移动端使用全屏 Agent 且无横向溢出", mobileDrawerTest);
