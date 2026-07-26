import { expect, test, type Page, type TestInfo } from "@playwright/test";

const password = "v132 e2e correct horse battery";
const viewports = [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "tablet", width: 1024, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const;

/** 注册并登录固定管理员，供运营控制台三视口截图复用。 */
async function registerImmersiveReviewer(page: Page): Promise<void> {
  const invitationResponse = await page.request.post("/api/test/invitation");
  const invitation = (await invitationResponse.json()) as { code: string };
  const username = "v132.operator";
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

/** 读取根元素宽度，阻止实验性版式产生横向页面溢出。 */
function readDocumentWidth(
  element: unknown,
): { clientWidth: number; scrollWidth: number } {
  const target = element as { clientWidth: number; scrollWidth: number };
  return { clientWidth: target.clientWidth, scrollWidth: target.scrollWidth };
}

/** 在浏览器上下文滚动到文档底部，用于复现跨路由滚动继承。 */
function scrollToDocumentEnd(): void {
  const browser = globalThis as unknown as {
    document: { body: { scrollHeight: number } };
    scrollTo: (x: number, y: number) => void;
  };
  browser.scrollTo(0, browser.document.body.scrollHeight);
}

/** 在浏览器上下文回到文档顶部，恢复截图的首屏叙事。 */
function scrollToDocumentStart(): void {
  (globalThis as unknown as { scrollTo: (x: number, y: number) => void }).scrollTo(
    0,
    0,
  );
}

/** 汇总图片加载状态，避免全页截图把未触发的懒加载误判为商业成品。 */
function readImageHealth(
  elements: unknown[],
): { loaded: number; total: number } {
  const images = elements as Array<{ complete: boolean; naturalWidth: number }>;
  return {
    loaded: images.filter((image) => image.complete && image.naturalWidth > 0)
      .length,
    total: images.length,
  };
}

/** 在浏览器上下文读取当前纵向滚动位置。 */
function readWindowScrollY(): number {
  return (globalThis as unknown as { scrollY: number }).scrollY;
}

/** 在固定视口验证 Canvas、Lucide、标题和页面宽度并生成截图。 */
async function captureSurface(
  page: Page,
  testInfo: TestInfo,
  target: { name: string; route: string },
): Promise<void> {
  for (const viewport of viewports) {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await page.goto(target.route);
    await expect(page.locator("main h1").first()).toBeVisible();
    await expect(page.getByTestId("interactive-field")).toHaveCount(1);
    expect(await page.locator("svg.lucide").count()).toBeGreaterThan(3);
    const width = await page.locator("html").evaluate(readDocumentWidth);
    expect(width.scrollWidth).toBeLessThanOrEqual(width.clientWidth + 1);
    await page.waitForLoadState("networkidle");
    const images = page.locator("img");
    if ((await images.count()) > 0) {
      await page.evaluate(scrollToDocumentEnd);
      await expect
        .poll(() => images.evaluateAll(readImageHealth))
        .toEqual({ loaded: await images.count(), total: await images.count() });
      await page.evaluate(scrollToDocumentStart);
    }
    await page.screenshot({
      path: testInfo.outputPath(`${target.name}-${viewport.name}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
}

/** 验证客户沉浸界面、路由滚动恢复和管理员控制室三类固定证据。 */
async function immersiveInterfaceJourney(
  { page }: { page: Page },
  testInfo: TestInfo,
): Promise<void> {
  test.setTimeout(120_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await captureSurface(page, testInfo, {
    name: "support-home",
    route: "/support",
  });
  await captureSurface(page, testInfo, {
    name: "assistant-workspace",
    route: "/chat",
  });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/support");
  await page.evaluate(scrollToDocumentEnd);
  await page.getByRole("link", { name: /咨询智能助手/ }).click();
  await expect.poll(() => page.evaluate(readWindowScrollY)).toBeLessThan(2);

  await registerImmersiveReviewer(page);
  await captureSurface(page, testInfo, {
    name: "operations-control-room",
    route: "/admin",
  });
}

test("v1.3.2 沉浸式客户与运营界面", immersiveInterfaceJourney);
