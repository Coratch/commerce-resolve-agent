import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { SessionResponse } from "../../api/types";
import { AgentDrawerProvider } from "./AgentDrawer";
import { OrderDetailPage } from "./OrderDetailPage";
import { OrdersListPage } from "./OrdersListPage";
import { ServiceDetailPage } from "./ServiceDetailPage";
import { SupportHomePage } from "./SupportHomePage";

const api = vi.hoisted(() => ({
  createConversation: vi.fn(),
  decideL2Memory: vi.fn(),
  decideL2Upgrade: vi.fn(),
  decideRefund: vi.fn(),
  getPendingL2: vi.fn(),
  getPendingRefund: vi.fn(),
  getSupportOrder: vi.fn(),
  getSupportOverview: vi.fn(),
  getSupportService: vi.fn(),
  listSupportOrders: vi.fn(),
  listConversationMessages: vi.fn(),
  submitConversationMessage: vi.fn(),
  subscribeRunEvents: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return { ...original, ...api };
});

const session: SessionResponse = {
  mode: "registered",
  username: "support.user",
  session_scope: "account",
  csrf_token: "csrf",
  expires_at: "2026-07-23T00:00:00Z",
  capabilities: {
    can_manage_orders: true,
    can_manage_refunds: true,
    can_use_llm: true,
    can_access_admin: false,
  },
};

/** 安装桌面媒体查询桩，确保测试只挂载一份对话面板。 */
function installDesktopMediaQuery(): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

/** 使用隔离 QueryClient 和真实路由渲染指定页面。 */
function renderRoute(path: string, element: React.ReactNode): void {
  const routePath = path.includes("services")
    ? "/support/services/:serviceId"
    : path.includes("orders/")
      ? "/support/orders/:orderId"
      : path === "/support/orders"
        ? "/support/orders"
        : "/support";
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AgentDrawerProvider session={session}>
          <Routes><Route path={routePath} element={element} /></Routes>
        </AgentDrawerProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  installDesktopMediaQuery();
  for (const mock of Object.values(api)) {
    mock.mockReset();
  }
  api.getPendingL2.mockResolvedValue({ pending: false, public_status: "none" });
  api.getPendingRefund.mockResolvedValue({ pending: false, public_status: "none" });
  api.listConversationMessages.mockResolvedValue({
    messages: [],
    history_state: "complete",
  });
  api.subscribeRunEvents.mockReturnValue(vi.fn());
});

afterEach(() => cleanup());

/** 验证售后首页只读取服务投影，不建立会话或调用聊天入口。 */
async function readOnlyOverviewTest(): Promise<void> {
  api.getSupportOverview.mockResolvedValue({
    active_services: [],
    recent_orders: [
      {
        order_id: "ORD-001",
        status: "shipped",
        item_count: 1,
        item_title_preview: "演示耳机",
        shipment_status: "in_transit",
        created_at: "2026-07-20T00:00:00Z",
        updated_at: "2026-07-22T00:00:00Z",
      },
    ],
    has_more_orders: false,
    has_more_services: false,
  });

  renderRoute("/support", <SupportHomePage session={session} />);

  expect(await screen.findByText("演示耳机")).toBeInTheDocument();
  expect(api.createConversation).not.toHaveBeenCalled();
  expect(api.submitConversationMessage).not.toHaveBeenCalled();
}

it("浏览售后首页不会创建会话", readOnlyOverviewTest);

/** 验证客户空订单状态不会再指向已取消的自助维护入口。 */
async function emptyOverviewExplainsAdminPreparationTest(): Promise<void> {
  api.getSupportOverview.mockResolvedValue({
    active_services: [],
    recent_orders: [],
    has_more_orders: false,
    has_more_services: false,
  });

  renderRoute("/support", <SupportHomePage session={session} />);

  expect(
    await screen.findByText("订单准备完成后会自动出现在这里。"),
  ).toBeInTheDocument();
  expect(screen.queryByText(/演示数据页创建/)).not.toBeInTheDocument();
}

it("售后首页空订单状态说明由管理员准备数据", emptyOverviewExplainsAdminPreparationTest);

/** 验证订单列表空状态与 v1.2 的客户只读权限一致。 */
async function emptyOrderListExplainsAdminPreparationTest(): Promise<void> {
  api.listSupportOrders.mockResolvedValue({ orders: [], next_cursor: null });

  renderRoute("/support/orders", <OrdersListPage />);

  expect(
    await screen.findByText("可以清除搜索条件或切换订单状态后再试。"),
  ).toBeInTheDocument();
  expect(screen.queryByText(/演示数据页创建/)).not.toBeInTheDocument();
}

it("订单列表空状态说明由管理员准备数据", emptyOrderListExplainsAdminPreparationTest);

/** 验证订单搜索与状态筛选作为服务端查询条件发送。 */
async function orderSearchAndFilterTest(): Promise<void> {
  api.listSupportOrders.mockResolvedValue({ orders: [], next_cursor: null });
  const user = userEvent.setup();

  renderRoute("/support/orders", <OrdersListPage />);

  await screen.findByText("没有找到符合条件的订单");
  await user.type(
    screen.getByLabelText("搜索订单号或商品名称"),
    "降噪耳机",
  );
  await user.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() =>
    expect(api.listSupportOrders).toHaveBeenCalledWith({
      cursor: undefined,
      q: "降噪耳机",
      view: "all",
    }),
  );
  await user.click(screen.getByRole("button", { name: "待收货" }));
  await waitFor(() =>
    expect(api.listSupportOrders).toHaveBeenCalledWith({
      cursor: undefined,
      q: "降噪耳机",
      view: "shipping",
    }),
  );
}

it("订单列表把搜索与筛选提交到服务端", orderSearchAndFilterTest);

/** 验证订单页仅在用户主动点击后创建绑定会话。 */
async function lazyBoundConversationTest(): Promise<void> {
  api.getSupportOrder.mockResolvedValue({
    summary: {
      order_id: "ORD-001",
      status: "shipped",
      item_count: 1,
      item_title_preview: "演示耳机",
      shipment_status: "in_transit",
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
    },
    items: [
      {
        sku: "SKU-1",
        title: "演示耳机",
        quantity: 1,
        product_category: "general",
        product_ref: "audio-001",
        variant_title: "曜石黑 · 标准版",
        unit_amount: "1299.00",
        currency: "CNY",
        image_url: "/catalog/v1.3/audio.webp",
        image_alt: "演示耳机",
        catalog_version: "v1.3",
        snapshot_state: "complete",
      },
    ],
    shipment: {
      status: "in_transit",
      last_event: "运输中",
      updated_at: "2026-07-22T00:00:00Z",
    },
    shipment_milestones: [],
    packages: [
      {
        package_id: "PKG-001",
        carrier: "顺丰速运",
        tracking_number: "SF-DEMO-001",
        status: "in_transit",
        last_event: "包裹已到达杭州转运中心",
        estimated_delivery_at: "2026-07-24",
        items: [{ sku: "SKU-1", title: "演示耳机", quantity: 1 }],
        updated_at: "2026-07-22T00:00:00Z",
      },
    ],
    payment: null,
    refunds: [],
    amount_summary: {
      item_subtotal: "1299.00",
      paid_amount: null,
      refunded_amount: "0.00",
      currency: "CNY",
    },
    next_step: "等待包裹送达后确认商品状态。",
    available_actions: ["ask_assistant"],
  });
  api.createConversation.mockResolvedValue({
    thread_id: "00000000-0000-0000-0000-000000000001",
    related_order_id: "ORD-001",
  });

  renderRoute("/support/orders/ORD-001", <OrderDetailPage />);

  expect(
    await screen.findByRole("heading", { level: 1, name: "演示耳机" }),
  ).toBeInTheDocument();
  expect(screen.getByText("曜石黑 · 标准版 · 普通商品")).toBeInTheDocument();
  expect(screen.getByText("顺丰速运")).toBeInTheDocument();
  expect(screen.getByText("运单 SF-DEMO-001", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("商品快照小计")).toBeInTheDocument();
  expect(screen.getByText("等待包裹送达后确认商品状态。")).toBeInTheDocument();
  expect(api.createConversation).not.toHaveBeenCalled();
  await userEvent.setup().click(screen.getByRole("button", { name: "咨询此订单" }));
  expect(api.createConversation).toHaveBeenCalledWith("ORD-001");
  expect(await screen.findByText("直接描述你的问题")).toBeInTheDocument();
}

it("订单助手按显式操作懒创建绑定会话", lazyBoundConversationTest);

/** 验证服务详情通过关联订单恢复唯一活动任务。 */
async function reuseServiceThreadTest(): Promise<void> {
  api.getSupportService.mockResolvedValue({
    summary: {
      service_id: "refund:RF-001",
      kind: "refund",
      status: "waiting_user",
      order_id: "ORD-001",
      thread_id: "00000000-0000-0000-0000-000000000002",
      title: "订单 ORD-001 退款",
      next_action: "确认或拒绝退款",
      updated_at: "2026-07-22T00:00:00Z",
    },
    public_steps: [
      { key: "preview", title: "退款方案已生成", state: "current", occurred_at: null },
    ],
    result_summary: null,
    citations: [],
  });

  api.createConversation.mockResolvedValue({
    thread_id: "00000000-0000-0000-0000-000000000002",
    related_order_id: "ORD-001",
    created: false,
  });

  renderRoute("/support/services/refund%3ARF-001", <ServiceDetailPage />);

  expect(await screen.findByText("退款方案已生成")).toBeInTheDocument();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "继续智能售后" }));
  expect(await screen.findByText("直接描述你的问题")).toBeInTheDocument();
  expect(api.createConversation).toHaveBeenCalledWith("ORD-001");
  expect(api.listConversationMessages).toHaveBeenCalledWith(
    "00000000-0000-0000-0000-000000000002",
  );
}

it("服务详情通过关联订单恢复活动会话", reuseServiceThreadTest);
