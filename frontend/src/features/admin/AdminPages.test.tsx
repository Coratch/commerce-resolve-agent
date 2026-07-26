import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { SessionResponse } from "../../api/types";
import { AdminLayout } from "../../app/AdminLayout";
import {
  AdminDataPage,
  AdminInvitationsPage,
  AdminOverviewPage,
  AdminSystemPage,
} from "./AdminPages";

const api = vi.hoisted(() => ({
  createAdminInvitation: vi.fn(),
  getAdminOverview: vi.fn(),
  getAdminSystem: vi.fn(),
  listAdminAudit: vi.fn(),
  listAdminCustomers: vi.fn(),
  listAdminInvitations: vi.fn(),
  resetAdminDemoWorkspace: vi.fn(),
  revokeAdminInvitation: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return { ...original, ...api };
});

const adminSession = {
  mode: "registered",
  username: "operator",
  role: "admin",
  session_scope: "account",
  csrf_token: "csrf",
  expires_at: "2026-07-23T00:00:00Z",
  capabilities: {
    can_manage_orders: false,
    can_manage_refunds: true,
    can_use_llm: true,
    can_access_admin: true,
  },
} satisfies SessionResponse;

/** 使用隔离 QueryClient 渲染一条运营控制台路由。 */
function renderAdminRoute(
  path: string,
  session: SessionResponse,
  element: React.ReactNode,
): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/admin"
            element={
              <AdminLayout session={session} logoutPending={false} onLogout={vi.fn()} />
            }
          >
            <Route index element={element} />
            <Route path="system" element={element} />
          </Route>
          <Route path="/support" element={<div>客户售后中心</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
});

afterEach(() => cleanup());

/** 验证客户即使直接输入后台 URL 也会返回客户表面。 */
function customerAdminRedirectTest(): void {
  const customer = {
    ...adminSession,
    role: "customer" as const,
    capabilities: { ...adminSession.capabilities, can_access_admin: false },
  };
  renderAdminRoute("/admin", customer, <AdminOverviewPage />);
  expect(screen.getByText("客户售后中心")).toBeInTheDocument();
  expect(api.getAdminOverview).not.toHaveBeenCalled();
}

it("客户直接访问运营路由会被重定向", customerAdminRedirectTest);

/** 验证运营概览只读取服务端聚合并展示真实计数。 */
async function overviewReadOnlyTest(): Promise<void> {
  api.getAdminOverview.mockResolvedValue({
    counts: { customers: 2, orders: 3, active_runs: 1, active_cases: 0 },
    recent_runs: [],
    evaluation: {
      state: "missing",
      baseline_id: null,
      candidate_run_id: null,
      suites: [],
      safety_violation_count: 0,
      compatibility_reasons: [],
    },
    system: {
      version: "1.3.0",
      migration_head: "20260722_0008",
      live: true,
      ready: true,
      ready_error_code: null,
      capabilities: {},
      storage: {},
    },
  });
  renderAdminRoute("/admin", adminSession, <AdminOverviewPage />);
  expect(await screen.findByRole("heading", { name: "Agent 运营控制台" })).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(api.getAdminOverview).toHaveBeenCalledTimes(1);
}

it("管理员概览读取权威聚合且不执行任务", overviewReadOnlyTest);

/** 验证用尽的邀请码不会继续显示为可用状态。 */
async function exhaustedInvitationStatusTest(): Promise<void> {
  api.listAdminInvitations.mockResolvedValue([
    {
      invitation_id: "invite-used",
      expires_at: "2026-07-29T00:00:00Z",
      max_uses: 1,
      used_count: 1,
      revoked: false,
      created_at: "2026-07-22T00:00:00Z",
    },
  ]);

  renderAdminRoute("/admin", adminSession, <AdminInvitationsPage />);

  expect(await screen.findByText("已用尽")).toBeInTheDocument();
  expect(screen.queryByText("有效", { exact: true })).not.toBeInTheDocument();
}

it("邀请码达到使用上限后显示已用尽", exhaustedInvitationStatusTest);

/** 验证管理员只能查看并整区重置显式目标客户的演示工作区。 */
async function resetCommercialWorkspaceTest(): Promise<void> {
  api.listAdminCustomers.mockResolvedValue([
    {
      user_id: "customer-1",
      username: "demo.customer",
      status: "active",
      role: "customer",
      workspace_id: "workspace-1",
      dataset_version: "portfolio-demo-v1",
      dataset_status: "ready",
      reset_generation: 0,
      order_count: 3,
      initialized_at: "2026-07-22T00:00:00Z",
      created_at: "2026-07-22T00:00:00Z",
    },
  ]);
  api.resetAdminDemoWorkspace.mockResolvedValue({
    workspace_id: "workspace-1",
    dataset_version: "portfolio-demo-v1",
    dataset_status: "ready",
    reset_generation: 1,
    order_ids: ["CR-7X2P-9K3M", "CR-4H8N-6T2R", "CR-9W3K-5M7Q"],
    completed_at: "2026-07-24T00:00:00Z",
    already_completed: false,
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const user = userEvent.setup();

  renderAdminRoute("/admin", adminSession, <AdminDataPage />);

  expect(await screen.findByText("portfolio-demo-v1")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重置工作区" }));
  expect(api.resetAdminDemoWorkspace).toHaveBeenCalledWith(
    "customer-1",
    expect.any(String),
  );
  expect(
    await screen.findByText(/保留了 3 个公开订单号/),
  ).toBeInTheDocument();
}

it("管理员只能整区重置版本化演示工作区", resetCommercialWorkspaceTest);

/** 验证系统页只展示有限状态和脱敏审计投影。 */
async function systemProjectionTest(): Promise<void> {
  api.getAdminSystem.mockResolvedValue({
    version: "1.3.0",
    migration_head: "20260722_0008",
    live: true,
    ready: false,
    ready_error_code: "policy_index_unavailable",
    capabilities: { model: false },
    storage: { business: "ready", evaluation: "missing" },
  });
  api.listAdminAudit.mockResolvedValue([]);
  renderAdminRoute("/admin/system", adminSession, <AdminSystemPage />);
  expect(await screen.findByText("当前未就绪：policy_index_unavailable")).toBeInTheDocument();
  expect(screen.getByText("后台写操作审计")).toBeInTheDocument();
  expect(api.getAdminSystem).toHaveBeenCalledTimes(1);
  expect(api.listAdminAudit).toHaveBeenCalledTimes(1);
}

it("系统页只读取有限状态与审计", systemProjectionTest);
