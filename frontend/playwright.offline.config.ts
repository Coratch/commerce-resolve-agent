import { defineConfig } from "@playwright/test";

/** 配置不监听本地端口的 Chromium 退款界面验收。 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "refund-ui.spec.ts",
  use: {
    baseURL: "http://commerce-resolve.test",
    channel: "chromium",
    trace: "retain-on-failure",
  },
});
