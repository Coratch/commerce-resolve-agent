import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createConversation,
  getSession,
  sendChatMessage,
} from "./client";

const sessionPayload = {
  mode: "registered" as const,
  username: null,
  session_scope: "account" as const,
  csrf_token: "csrf-memory-only",
  expires_at: "2026-07-17T12:00:00Z",
  capabilities: {
    can_manage_orders: false,
    can_manage_refunds: false,
    can_use_llm: false,
  },
};

/** 构造指定 JSON 和状态的最小 Fetch Response。 */
function jsonResponse(value: object, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API Client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("只在内存中同步 CSRF 并随 mutation 发送", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(sessionPayload))
      .mockResolvedValueOnce(
        jsonResponse(
          { thread_id: "00000000-0000-0000-0000-000000000001" },
          201,
        ),
      );

    await getSession();
    await createConversation("CR-7X2P-9K3M");

    const request = fetchMock.mock.calls[1];
    const headers = new Headers(request[1]?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-memory-only");
    expect(JSON.parse(String(request[1]?.body))).toEqual({
      related_order_id: "CR-7X2P-9K3M",
    });
    expect(localStorage.length).toBe(0);
  });

  it("Chat 请求只提交 thread_id 和 message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        thread_id: "00000000-0000-0000-0000-000000000001",
        assistant_message: "已查询",
        public_status: "completed",
        citations: [],
      }),
    );

    await sendChatMessage(
      "00000000-0000-0000-0000-000000000001",
      "查询 ORD-001",
    );

    const request = vi.mocked(fetch).mock.calls[0];
    const body = JSON.parse(String(request[1]?.body)) as Record<string, string>;
    expect(body).toEqual({
      thread_id: "00000000-0000-0000-0000-000000000001",
      message: "查询 ORD-001",
    });
    expect(body).not.toHaveProperty("user_id");
    expect(body).not.toHaveProperty("workspace_id");
    expect(body).not.toHaveProperty("interpreter");
  });
});
