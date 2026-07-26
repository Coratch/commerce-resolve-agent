import { expect, test, type Page } from "@playwright/test";

/** 验证游客能够在真实浏览器通过 Fake 查询共享演示订单。 */
async function runGuestChat(page: Page): Promise<void> {
  await page.goto("/chat");
  await expect(page.getByText("访客体验 · 使用演示数据")).toBeVisible();
  await page.getByLabel("输入售后问题").fill("帮我看看 ORD-001 到哪里了");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/包裹已离开上海转运中心/)).toBeVisible();

  const fallbackReply =
    "暂时无法处理这个问题，请换一种方式描述或尝试查询订单、物流及售后政策。";
  await page.getByLabel("输入售后问题").fill("你好");
  await page.getByRole("button", { name: "发送" }).click();
  const latestAssistant = page
    .locator('section[aria-label="售后对话"] article')
    .last();
  await expect(latestAssistant).toBeVisible();
  await expect(latestAssistant).not.toContainText(fallbackReply);
  await expect(latestAssistant).toContainText(fallbackReply);
  await expect(latestAssistant).not.toHaveClass(/assistantError/);
  const chatRegion = page.getByRole("region", { name: "售后对话" });
  await expect(
    chatRegion.getByText("CommerceResolve", { exact: true }),
  ).toHaveCount(0);
  await expect(chatRegion.getByText("你", { exact: true })).toHaveCount(0);
}

/** 验证邀请注册、私有数据、退款审批与 AI 二线升级的完整浏览器路径。 */
async function runRegisteredFlow(page: Page): Promise<void> {
  const invitationResponse = await page.request.post("/api/test/invitation");
  const invitation = (await invitationResponse.json()) as { code: string };
  await page.goto("/register");
  await page.getByLabel("用户名").fill("e2e.user");
  await page.getByLabel("密码（至少 12 个字符）").fill("e2e correct horse battery");
  await page.getByLabel("邀请码").fill(invitation.code);
  await page.getByRole("button", { name: "创建账号" }).click();
  await expect(page.getByText("注册成功")).toBeVisible();
  await page.request.post("/api/test/admin/e2e.user");
  await page.goto("/login");
  await page.getByLabel("用户名").fill("e2e.user");
  await page.getByLabel("密码").fill("e2e correct horse battery");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(/\/orders$/);

  await page.getByRole("link", { name: "运营控制台" }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await page.getByRole("link", { name: "演示数据", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/data$/);

  const targetCustomer = page.getByLabel("目标客户").last();
  const targetUserId = await targetCustomer
    .locator("option")
    .filter({ hasText: /^e2e\.user ·/ })
    .getAttribute("value");
  if (targetUserId === null) throw new Error("未找到 E2E 目标客户");
  await targetCustomer.selectOption(targetUserId);
  await page.getByLabel("订单号").fill("ORD-E2E-001");
  await page.getByLabel("商品名称（可选）").fill("E2E 演示商品");
  await page.getByLabel("初始物流事件").fill("E2E 包裹等待揽收");
  await page.getByRole("button", { name: "创建订单" }).click();
  await expect(page.getByText("ORD-E2E-001")).toBeVisible();
  await page.getByLabel("支付金额").fill("129.90");
  await page.getByRole("button", { name: "保存演示支付" }).click();
  await page.getByRole("link", { name: "返回客户售后中心" }).click();
  await expect(page).toHaveURL(/\/support$/);
  await page.goto("/chat");
  const chatRegion = page.getByRole("region", { name: "售后对话" });
  await page.getByLabel("输入售后问题").fill("查询 ORD-E2E-001 的物流");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(
    chatRegion.getByText(/E2E 包裹等待揽收/),
  ).toBeVisible();

  await page
    .getByLabel("输入售后问题")
    .fill("请退款 ORD-E2E-001，商品有质量问题");
  await page.getByRole("button", { name: "发送" }).click();
  const preview = page.getByRole("complementary", {
    name: "待审批退款预览",
  });
  await expect(preview.getByText("退款 ¥129.90")).toBeVisible();
  await preview.getByRole("button", { name: "确认演示退款" }).click();
  await expect(chatRegion.getByText(/演示退款已完成并验证/)).toBeVisible();

  await page
    .getByLabel("输入售后问题")
    .fill("请升级二线客服处理 ORD-E2E-001 的复杂售后问题");
  await page.getByRole("button", { name: "发送" }).click();
  const upgrade = page.getByRole("complementary", {
    name: "AI 二线客服升级预览",
  });
  await expect(upgrade.getByText("AI 二线客服，并非真人")).toBeVisible();
  await upgrade.getByRole("button", { name: "确认进入 AI 二线" }).click();
  await expect(
    chatRegion.getByText(
      /已核对订单和物流，当前退款记录与运输状态均已纳入处理结论/,
    ),
  ).toBeVisible();
  const l2Case = page.getByRole("complementary", {
    name: "AI 二线服务记录",
  });
  await expect(
    l2Case.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible();
  await expect(l2Case.getByText(/已完成业务核对/).first()).toBeVisible();
  await expect(l2Case.locator("details").first()).toBeVisible();

  await page.reload();
  const restoredL2Case = page.getByRole("complementary", {
    name: "AI 二线服务记录",
  });
  await expect(
    restoredL2Case.getByRole("heading", { name: "已完成", exact: true }),
  ).toBeVisible();
  await expect(restoredL2Case.getByText(/已完成业务核对/).first()).toBeVisible();
  const businessBasis = restoredL2Case
    .locator("details")
    .filter({ hasText: "订单与服务状态" })
    .first();
  await businessBasis.locator("summary").click();
  await expect(businessBasis.getByText(/订单与服务状态/)).toBeVisible();

  await page.goto("/orders/ORD-E2E-001");
  await expect(page.getByText("退款 ¥129.90")).toBeVisible();
  await expect(page.getByText("succeeded", { exact: true })).toBeVisible();
}

/** 适配 Playwright Fixture 并执行游客路径。 */
async function guestTest({ page }: { page: Page }): Promise<void> {
  await runGuestChat(page);
}

/** 适配 Playwright Fixture 并执行注册用户路径。 */
async function registeredTest({ page }: { page: Page }): Promise<void> {
  await runRegisteredFlow(page);
}

test("游客共享演示对话", guestTest);
test("邀请账号私有数据与对话", registeredTest);
