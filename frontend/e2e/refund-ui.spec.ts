import { expect, test, type Page, type Route } from "@playwright/test";

const DIST_ROOT = "dist";
const THREAD_ID = "11111111-1111-4111-8111-111111111111";
const ACTION_ID = "22222222-2222-4222-8222-222222222222";
const CHAT_RUN_ID = "33333333-3333-4333-8333-333333333333";
const DECISION_RUN_ID = "44444444-4444-4444-8444-444444444444";
const NOW = "2026-07-21T00:00:00Z";
let uiState: "empty" | "preview" | "completed" = "empty";

const citation = {
  document_id: "refunds-v1",
  section_id: "refund-eligibility-pre-fulfillment",
  title: "退款政策",
  heading: "发货前直接退款",
  source_path: "refunds-v1.md",
  line_start: 10,
  line_end: 16,
};

/** 返回固定 JSON，以真实浏览器验证前端 API 契约和交互状态。 */
async function fulfillCommerceResolveRoute(route: Route): Promise<void> {
  const request = route.request();
  const pathname = request.url().replace("http://commerce-resolve.test", "").split("?")[0];
  if (pathname === "/api/session") {
    await route.fulfill({
      json: {
        mode: "registered",
        username: "browser.user",
        session_scope: "account",
        csrf_token: "browser-csrf-token",
        expires_at: "2030-01-01T00:00:00Z",
        capabilities: {
          can_manage_orders: true,
          can_manage_refunds: true,
          can_use_llm: true,
        },
      },
    });
    return;
  }
  if (pathname === "/api/conversations" && request.method() === "POST") {
    await route.fulfill({ json: { thread_id: THREAD_ID } });
    return;
  }
  if (pathname === "/api/conversations" && request.method() === "GET") {
    await route.fulfill({
      json: {
        conversations: [
          {
            thread_id: THREAD_ID,
            title: "退款咨询",
            lifecycle_status: "active",
            history_state: "complete",
            message_count: uiState === "empty" ? 0 : uiState === "preview" ? 2 : 4,
            last_message_preview:
              uiState === "completed"
                ? "Mock 退款已完成并验证：RFN-BROWSER-001。"
                : uiState === "preview"
                  ? "退款预览已生成；批准后才会写入本地 Mock 退款记录。"
                  : null,
            created_at: NOW,
            updated_at: NOW,
          },
        ],
        next_cursor: null,
      },
    });
    return;
  }
  if (pathname.endsWith("/messages") && request.method() === "GET") {
    const previewPayload = {
      public_status: "refund_awaiting_approval",
      citations: [citation],
      refund_preview: {
        action_id: ACTION_ID,
        order_id: "ORD-BROWSER-001",
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
    const messages =
      uiState === "empty"
        ? []
        : [
            {
              message_id: "message-user-1",
              thread_id: THREAD_ID,
              run_id: CHAT_RUN_ID,
              sequence_no: 1,
              role: "user",
              kind: "text",
              content: "请退款 ORD-BROWSER-001，商品有质量问题",
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
              content: "退款预览已生成；批准后才会写入本地 Mock 退款记录。",
              status: "completed",
              payload_version: 1,
              payload: previewPayload,
              created_at: NOW,
              updated_at: NOW,
            },
            ...(uiState === "completed"
              ? [
                  {
                    message_id: "message-user-2",
                    thread_id: THREAD_ID,
                    run_id: DECISION_RUN_ID,
                    sequence_no: 3,
                    role: "user",
                    kind: "action",
                    content: "批准 Mock 退款",
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
                    content: "Mock 退款已完成并验证：RFN-BROWSER-001。",
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
                ]
              : []),
          ];
    await route.fulfill({
      json: { messages, history_state: "complete", next_after_sequence: null },
    });
    return;
  }
  if (pathname.endsWith("/pending-refund")) {
    await route.fulfill({
      json: {
        pending: uiState === "preview",
        public_status:
          uiState === "preview" ? "refund_awaiting_approval" : "none",
        refund_preview:
          uiState === "preview"
            ? {
                action_id: ACTION_ID,
                order_id: "ORD-BROWSER-001",
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
              }
            : null,
      },
    });
    return;
  }
  if (pathname.endsWith("/pending-l2")) {
    await route.fulfill({
      json: { pending: false, public_status: "none" },
    });
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
        user_message: {
          message_id: "message-user-1",
          thread_id: THREAD_ID,
          run_id: CHAT_RUN_ID,
          sequence_no: 1,
          role: "user",
          kind: "text",
          content: "请退款 ORD-BROWSER-001，商品有质量问题",
          status: "accepted",
          payload_version: 1,
          payload: {},
          created_at: NOW,
          updated_at: NOW,
        },
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
        user_message: {
          message_id: "message-user-2",
          thread_id: THREAD_ID,
          run_id: DECISION_RUN_ID,
          sequence_no: 3,
          role: "user",
          kind: "action",
          content: "批准 Mock 退款",
          status: "accepted",
          payload_version: 1,
          payload: {},
          created_at: NOW,
          updated_at: NOW,
        },
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

/** 在真实 Chromium 中完成退款预览和批准交互。 */
async function runRefundApprovalUi(page: Page): Promise<void> {
  uiState = "empty";
  await page.route("http://commerce-resolve.test/**", fulfillCommerceResolveRoute);
  await page.goto("/chat");
  await expect(page.getByText("已登录 · LLM 模式")).toBeVisible();
  await page
    .getByLabel("输入售后问题")
    .fill("请退款 ORD-BROWSER-001，商品有质量问题");
  await page.getByRole("button", { name: "发送" }).click();

  const preview = page.getByRole("complementary", {
    name: "待审批退款预览",
  });
  await expect(preview.getByText("退款 ¥129.90")).toBeVisible();
  await expect(page.getByLabel("输入售后问题")).toBeDisabled();
  await preview.getByRole("button", { name: "批准 Mock 退款" }).click();

  await expect(
    page.getByRole("region", { name: "售后对话" }).getByText(/Mock 退款已完成并验证/),
  ).toBeVisible();
  await expect(preview).toHaveCount(0);
  await expect(page.getByLabel("输入售后问题")).toBeEnabled();
}

/** 适配 Playwright Fixture 并执行无端口退款界面场景。 */
async function refundApprovalTest({ page }: { page: Page }): Promise<void> {
  await runRefundApprovalUi(page);
}

test("注册用户查看并批准 Mock 退款", refundApprovalTest);
