import {
  expect,
  test,
  type Page,
  type TestInfo,
} from "@playwright/test";

const password = "v13 e2e correct horse battery";

/** 注册并登录一个隔离管理员，用于验证目录场景初始化。 */
async function registerAdmin(page: Page): Promise<void> {
  const invitationResponse = await page.request.post("/api/test/invitation");
  const invitation = (await invitationResponse.json()) as { code: string };
  const username = "v13.operator";
  await page.goto("/register");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码（至少 12 个字符）").fill(password);
  await page.getByLabel("邀请码").fill(invitation.code);
  await page.getByRole("button", { name: "创建账号" }).click();
  await expect(page.getByText("注册成功")).toBeVisible();
  await page.request.post(`/api/test/admin/${username}`);
  await page.goto("/login");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(/\/orders$/);
}

/** 验证游客在三种视口下可以完成商品化订单浏览和组合咨询恢复。 */
async function commercialGuestJourney(
  { page }: { page: Page },
  testInfo: TestInfo,
): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/support");
  await expect(
    page.getByRole("heading", { name: /订单售后.*由此继续/ }),
  ).toBeVisible();
  const orderCard = page.getByRole("link", {
    name: /FlowSip 随行保温杯/,
  });
  await expect(orderCard.locator('img[src^="/catalog/v1.3/"]')).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("support-1440.png"),
    fullPage: true,
  });

  await orderCard.click();
  await expect(page.getByText("顺丰速运")).toBeVisible();
  await expect(page.getByText(/SF-V13-0001/)).toBeVisible();
  await expect(page.getByText("商品快照小计")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("order-detail-1440.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/orders");
  await page.getByLabel("搜索订单号或商品名称").fill("FlowSip");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page).toHaveURL(/q=FlowSip/);
  await expect(page.getByText("FlowSip 随行保温杯")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("orders-1024.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/orders/ORD-001");
  await expect(page.getByText("420ml·雾霾蓝 · 普通商品")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("order-detail-390.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/chat");
  await page
    .getByLabel("输入售后问题")
    .fill("ORD-001 的物流到哪了，并且能不能退款？");
  await page.getByRole("button", { name: "发送" }).click();
  const resolution = page.getByRole("complementary", {
    name: "智能服务方案",
  });
  await expect(resolution).toContainText("政策咨询不会创建退款");
  await expect(
    resolution.getByRole("button", { name: "申请演示退款" }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("complementary", { name: "智能服务方案" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("chat-resolution-1024.png"),
    fullPage: true,
  });
}

/** 验证运营控制台可以从版本化目录初始化显式客户场景。 */
async function adminCatalogSeedJourney(
  { page }: { page: Page },
  testInfo: TestInfo,
): Promise<void> {
  await registerAdmin(page);
  await page.getByRole("link", { name: "运营控制台" }).click();
  await page.getByRole("link", { name: "演示数据", exact: true }).click();
  await expect(page.getByText("12 个商品")).toBeVisible();
  await expect(page.getByText("19 个 SKU")).toBeVisible();
  const targetCustomer = page.getByLabel("目标客户").first();
  const targetUserId = await targetCustomer
    .locator("option")
    .filter({ hasText: /^v13\.operator ·/ })
    .getAttribute("value");
  if (targetUserId === null) throw new Error("未找到 v1.3 E2E 目标客户");
  await targetCustomer.selectOption(targetUserId);
  await page.getByRole("button", { name: "初始化场景" }).click();
  await expect(page.getByText(/已创建订单 ORD-V13-001/)).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("admin-data-1440.png"),
    fullPage: true,
  });
}

test("v1.3 商业化客户旅程", commercialGuestJourney);
test("v1.3 管理员目录初始化", adminCatalogSeedJourney);
