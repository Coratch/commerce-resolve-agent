import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import type { SessionResponse } from "../../api/types";
import { MemoriesPage } from "./MemoriesPage";

const api = vi.hoisted(() => ({
  deleteMemory: vi.fn(),
  listMemories: vi.fn(),
  updateMemory: vi.fn(),
}));

vi.mock("../../api/client", () => api);

const session: SessionResponse = {
  mode: "registered",
  username: "memory.user",
  session_scope: "account",
  csrf_token: "csrf",
  expires_at: "2026-07-20T12:00:00Z",
  capabilities: {
    can_manage_orders: true,
    can_manage_refunds: true,
    can_use_llm: true,
  },
};

/** 使用独立 QueryClient 渲染长期偏好页面。 */
function renderPage(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoriesPage session={session} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.deleteMemory.mockReset();
  api.listMemories.mockReset();
  api.updateMemory.mockReset();
});

it("列出已确认偏好并通过受限下拉值纠正", async () => {
  api.listMemories.mockResolvedValue([
    {
      memory_id: "memory-001",
      memory_type: "preferred_language",
      value: "zh-CN",
      source_case_id: "case-001",
      created_at: "2026-07-20T08:00:00Z",
      last_confirmed_at: "2026-07-20T08:00:00Z",
    },
  ]);
  api.updateMemory.mockResolvedValue({});
  const user = userEvent.setup();

  renderPage();

  const select = await screen.findByLabelText("修改 preferred_language");
  await user.selectOptions(select, "en");

  expect(api.updateMemory).toHaveBeenCalledWith("memory-001", "en");
  expect(screen.queryByText("friendly")).not.toBeInTheDocument();
});
