import { expect, test, type Page, type Request } from "@playwright/test";

const USERNAME = "v2.portfolio";
const PASSWORD = "v2 portfolio correct horse";

/** 记录匿名访问期间是否错误触发客户业务接口。 */
function businessRequestCounter() {
  let count = 0;

  /** 统计受注册身份保护的售后与会话请求。 */
  function observe(request: Request): void {
    const path = new URL(request.url()).pathname;
    if (
      path.startsWith("/api/support") ||
      path.startsWith("/api/conversations") ||
      path.startsWith("/api/workspace")
    ) {
      count += 1;
    }
  }

  return {
    observe,
    /** 返回已经观察到的受保护业务请求数量。 */
    value(): number {
      return count;
    },
  };
}

/** 通过测试专用可信端点签发邀请码，再使用真实注册页面创建账号。 */
async function registerPortfolioUser(page: Page): Promise<void> {
  const invitationResponse = await page.request.post("/api/test/invitation");
  expect(invitationResponse.ok()).toBeTruthy();
  const invitation = (await invitationResponse.json()) as { code: string };

  await page.goto("/register");
  await page.getByLabel("用户名").fill(USERNAME);
  await page.getByLabel("密码（至少 12 个字符）").fill(PASSWORD);
  await page.getByLabel("邀请码").fill(invitation.code);
  await page.getByRole("button", { name: "创建账号" }).click();
  await expect(page.getByText("注册成功")).toBeVisible();
}

/** 使用真实登录页进入新账号自动初始化的独立演示工作区。 */
async function loginPortfolioUser(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(USERNAME);
  await page.getByLabel("密码").fill(PASSWORD);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(/\/support$/);
  await expect(page.getByText("独立演示工作区")).toBeVisible();
}

/** 从三笔基准订单中打开可退款物流延迟订单，并返回稳定公开订单号。 */
async function openRefundableOrder(page: Page): Promise<string> {
  await page.getByRole("link", { name: "我的订单" }).click();
  const orderLink = page
    .getByRole("link")
    .filter({ hasText: "Pulse S2 运动智能手表" })
    .first();
  await expect(orderLink).toBeVisible();
  const href = await orderLink.getAttribute("href");
  if (href === null) {
    throw new Error("质量问题订单缺少详情链接");
  }
  const orderId = href.split("/").at(-1);
  if (orderId === undefined || !/^CR-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(orderId)) {
    throw new Error(`公开订单号不符合 V2.0 规则：${orderId ?? "missing"}`);
  }
  await orderLink.click();
  await expect(page).toHaveURL(new RegExp(`/support/orders/${orderId}$`));
  return orderId;
}

/** 在订单绑定 Thread 中提交退款并完成显式审批与结果验证。 */
async function completeBoundRefund(page: Page, orderId: string): Promise<void> {
  await page.getByRole("button", { name: "咨询此订单" }).click();
  const drawer = page.getByRole("complementary", { name: "智能售后助手" });
  await expect(drawer).toBeVisible();
  await drawer
    .getByLabel("输入售后问题")
    .fill("物流延误七天仍未揽收，请申请退款");
  await drawer.getByLabel("输入售后问题").press("Enter");

  const preview = drawer.getByRole("complementary", {
    name: "待审批退款预览",
  });
  await expect(preview).toContainText(orderId);
  await preview.getByRole("button", { name: "确认演示退款" }).click();
  await expect(drawer.getByText(/演示退款已完成并验证/)).toBeVisible();
}

/** 从订单详情链接收集排序后的公开订单号，不依赖浏览器 DOM 类型。 */
async function collectPublicOrderIds(page: Page): Promise<string[]> {
  const links = await page.locator('a[href^="/support/orders/"]').all();
  const orderIds: string[] = [];
  for (const link of links) {
    const href = await link.getAttribute("href");
    const orderId = href?.split("/").at(-1);
    if (orderId !== undefined && !orderIds.includes(orderId)) {
      orderIds.push(orderId);
    }
  }
  return orderIds.sort();
}

/** 验证匿名访问只进入登录页，且不会创建 Session 或读取业务数据。 */
async function anonymousAccessTest({ page }: { page: Page }): Promise<void> {
  const counter = businessRequestCounter();
  page.on("request", counter.observe);
  await page.goto("/support");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "登录私有工作区" })).toBeVisible();
  expect(counter.value()).toBe(0);
}

/** 验证注册、三场景、订单会话、退款、刷新恢复、重置和管理员边界。 */
async function portfolioJourneyTest({ page }: { page: Page }): Promise<void> {
  await registerPortfolioUser(page);
  await loginPortfolioUser(page);

  await page.getByRole("link", { name: "我的订单" }).click();
  await expect(page.getByText("Pulse S2 运动智能手表")).toBeVisible();
  await expect(page.getByText("Craft75 无线机械键盘")).toBeVisible();
  await expect(page.getByText("FlowSip 随行保温杯")).toBeVisible();

  const originalOrderIds = await collectPublicOrderIds(page);
  expect(originalOrderIds).toHaveLength(3);

  const orderId = await openRefundableOrder(page);
  await completeBoundRefund(page, orderId);

  await page.reload();
  await page.getByRole("button", { name: "咨询此订单" }).click();
  const restoredDrawer = page.getByRole("complementary", {
    name: "智能售后助手",
  });
  await expect(restoredDrawer.getByText(/演示退款已完成并验证/)).toBeVisible();

  await page.goto("/support/settings");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "重置演示工作区" }).click();
  await expect(page.getByRole("status")).toContainText("公开订单号保持不变");

  await page.goto("/support/orders");
  const resetOrderIds = await collectPublicOrderIds(page);
  expect(resetOrderIds).toEqual(originalOrderIds);

  const adminResponse = await page.request.post(`/api/test/admin/${USERNAME}`);
  expect(adminResponse.ok()).toBeTruthy();
  await page.reload();
  await page.goto("/admin/data");
  await expect(page.getByRole("heading", { name: "演示工作区" })).toBeVisible();
  await expect(page.getByText(/只能整区重置/)).toBeVisible();
  await expect(page.getByRole("button", { name: "创建订单" })).toHaveCount(0);
}

test("匿名访问不会触发客户业务接口", anonymousAccessTest);
test("V2.0 面试演示主旅程", portfolioJourneyTest);
