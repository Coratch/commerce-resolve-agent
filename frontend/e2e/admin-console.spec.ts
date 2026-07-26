import { expect, test, type Page } from "@playwright/test";

const password = "e2e correct horse battery";

/** 通过测试专用邀请码注册并登录账号，可选授予管理员角色。 */
async function registerAndLogin(
  page: Page,
  username: string,
  admin: boolean,
): Promise<void> {
  const invitationResponse = await page.request.post("/api/test/invitation");
  const invitation = (await invitationResponse.json()) as { code: string };
  await page.goto("/register");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码（至少 12 个字符）").fill(password);
  await page.getByLabel("邀请码").fill(invitation.code);
  await page.getByRole("button", { name: "创建账号" }).click();
  await expect(page.getByText("注册成功")).toBeVisible();
  if (admin) await page.request.post(`/api/test/admin/${username}`);
  await page.goto("/login");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(/\/orders$/);
}

/** 验证普通客户直接访问后台 URL 时只能返回客户售后中心。 */
async function customerCannotOpenAdmin({ page }: { page: Page }): Promise<void> {
  await registerAndLogin(page, "e2e.customer", false);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/support$/);
  await expect(page.getByRole("link", { name: "运营控制台" })).toHaveCount(0);
}

/** 验证管理员可进入独立控制台并创建只显示一次的邀请码。 */
async function adminCanUseConsole({ page }: { page: Page }): Promise<void> {
  await registerAndLogin(page, "e2e.operator", true);
  await page.getByRole("link", { name: "运营控制台" }).click();
  await expect(page.getByRole("heading", { name: "Agent 运营控制台" })).toBeVisible();
  await page.getByRole("link", { name: "邀请与账号", exact: true }).click();
  await page.getByRole("button", { name: "创建邀请码" }).click();
  await expect(page.getByText("请立即安全保存，本页刷新后无法再次读取：")).toBeVisible();
  await page.reload();
  await expect(page.getByText("请立即安全保存，本页刷新后无法再次读取：")).toHaveCount(0);
  await page.getByRole("link", { name: "运行监控" }).click();
  await expect(page.getByRole("heading", { name: "Agent 运行监控" })).toBeVisible();
}

test("客户不能访问运营控制台", customerCannotOpenAdmin);
test("管理员使用独立运营控制台", adminCanUseConsole);
