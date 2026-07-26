import { expect, test, type Page, type Request } from "@playwright/test";

/** 统计页面只读浏览期间主动创建会话的请求数量。 */
function countConversationCreates(page: Page): { value: number } {
  const count = { value: 0 };
  page.on("request", (request: Request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/conversations") {
      count.value += 1;
    }
  });
  return count;
}

/** 验证游客从售后首页进入订单并用绑定上下文省略订单号查询。 */
async function guestServiceCenterTest({ page }: { page: Page }): Promise<void> {
  const creates = countConversationCreates(page);
  await page.goto("/support");
  await expect(
    page.getByRole("heading", { name: /订单售后.*由此继续/ }),
  ).toBeVisible();
  await page.getByRole("link", { name: /FlowSip 随行保温杯/ }).click();
  await expect(page).toHaveURL(/\/orders\/ORD-001$/);
  expect(creates.value).toBe(0);

  await page.getByRole("button", { name: "咨询此订单" }).click();
  expect(creates.value).toBe(1);
  await page.getByLabel("输入售后问题").fill("它现在到哪里了？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(
    page.getByRole("complementary", { name: "订单售后助手" }).getByText(
      /包裹已离开上海转运中心/,
    ),
  ).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "咨询此订单" }).click();
  await expect(
    page.getByRole("complementary", { name: "订单售后助手" }).getByText(
      /包裹已离开上海转运中心/,
    ),
  ).toBeVisible();
}

/** 验证移动端使用原生模态助手，并可用 Esc 关闭和恢复焦点。 */
async function mobileAssistantDialogTest({ page }: { page: Page }): Promise<void> {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/orders/ORD-001");
  const trigger = page.getByRole("button", { name: "咨询售后助手" });
  await trigger.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(trigger).toBeFocused();
}

test("游客订单优先服务中心", guestServiceCenterTest);
test("移动端上下文助手可关闭", mobileAssistantDialogTest);
