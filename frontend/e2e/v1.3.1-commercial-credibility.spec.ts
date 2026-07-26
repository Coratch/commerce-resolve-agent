import { expect, test, type Page, type TestInfo } from "@playwright/test";

const password = "v131 e2e correct horse battery";

const viewports = [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "tablet", width: 1024, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const;

interface ScreenshotTarget {
  name: string;
  route: string;
}

/** 注册并登录固定管理员账号，用于准备隔离的商业验收工作区。 */
async function registerCommercialReviewer(page: Page): Promise<void> {
  const invitationResponse = await page.request.post("/api/test/invitation");
  const invitation = (await invitationResponse.json()) as { code: string };
  const username = "v131.operator";
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

/** 通过运营控制台为当前账号逐个幂等初始化目录中的全部场景。 */
async function seedAllCatalogScenarios(page: Page): Promise<void> {
  await page.goto("/admin/data");
  const targetCustomer = page.getByLabel("目标客户").first();
  const targetOption = targetCustomer
    .locator("option")
    .filter({ hasText: /^v131\.operator ·/ });
  await expect(targetOption).toHaveCount(1);
  const targetUserId = await targetOption.getAttribute("value");
  if (targetUserId === null) throw new Error("未找到 v1.3.1 固定验收账号");
  await targetCustomer.selectOption(targetUserId);

  const scenarioSelect = page.getByLabel("预设场景");
  await expect(scenarioSelect.locator("option")).toHaveCount(10);
  const scenarioIds: string[] = [];
  for (const option of await scenarioSelect.locator("option").all()) {
    const value = await option.getAttribute("value");
    if (value !== null) scenarioIds.push(value);
  }
  expect(scenarioIds).toHaveLength(10);
  for (const scenarioId of scenarioIds) {
    await scenarioSelect.selectOption(scenarioId);
    const responsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/demo-scenarios"),
    );
    await page.getByRole("button", { name: "初始化场景" }).click();
    await expect((await responsePromise).ok()).toBeTruthy();
  }
}

/** 通过客户公开旅程创建一条待确认退款服务，并返回会话和服务路由。 */
async function createPublicRefundJourney(
  page: Page,
): Promise<{ chatRoute: string; serviceRoute: string }> {
  await page.goto("/chat");
  await page
    .getByLabel("输入售后问题")
    .fill("请退款 ORD-V13-001，商品有质量问题");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(
    page.getByRole("complementary", { name: "待审批退款预览" }),
  ).toBeVisible();
  const chatRoute = new URL(page.url()).pathname;

  await page.goto("/services");
  const serviceLink = page
    .getByRole("region", { name: "客户服务记录" })
    .getByRole("link")
    .first();
  await expect(serviceLink).toBeVisible();
  const serviceRoute = await serviceLink.getAttribute("href");
  if (serviceRoute === null) throw new Error("未找到公开退款服务详情路由");
  return { chatRoute, serviceRoute };
}

/** 在浏览器页内读取指定元素宽度，用于阻止横向布局溢出。 */
function readElementWidth(
  element: unknown,
): { clientWidth: number; scrollWidth: number } {
  const target = element as { clientWidth: number; scrollWidth: number };
  return { clientWidth: target.clientWidth, scrollWidth: target.scrollWidth };
}

/** 在三个固定视口验证页面标题、横向布局和可重复截图。 */
async function captureTarget(
  page: Page,
  testInfo: TestInfo,
  target: ScreenshotTarget,
): Promise<void> {
  for (const viewport of viewports) {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await page.goto(target.route);
    await expect(page.locator("main h1").first()).toBeVisible();
    const width = await page.locator("html").evaluate(readElementWidth);
    expect(width.scrollWidth).toBeLessThanOrEqual(width.clientWidth + 1);
    await page.screenshot({
      path: testInfo.outputPath(`${target.name}-${viewport.name}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
}

/** 准备完整工作区并生成八页面、三视口的二十四份产品评审证据。 */
async function commercialCredibilityScreenshots(
  { page }: { page: Page },
  testInfo: TestInfo,
): Promise<void> {
  test.setTimeout(180_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await registerCommercialReviewer(page);
  await seedAllCatalogScenarios(page);
  const routes = await createPublicRefundJourney(page);
  const targets: ScreenshotTarget[] = [
    { name: "support-home", route: "/support" },
    { name: "orders", route: "/orders" },
    { name: "order-detail", route: "/orders/ORD-V13-001" },
    { name: "services", route: "/services" },
    { name: "service-detail", route: routes.serviceRoute },
    { name: "chat", route: routes.chatRoute },
    { name: "admin-overview", route: "/admin" },
    { name: "admin-data", route: "/admin/data" },
  ];
  for (const target of targets) {
    await captureTarget(page, testInfo, target);
  }
}

test("v1.3.1 固定商业可信度截图", commercialCredibilityScreenshots);
