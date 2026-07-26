import { defineConfig } from "@playwright/test";

/** 配置本地 Chromium 端到端验证入口。 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "v2-product.spec.ts",
  use: {
    baseURL: "http://127.0.0.1:8011",
    channel: "chromium",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "PYTHONPATH=../src:.. conda run -n ecom-agent uvicorn tests.e2e_server:app --host 127.0.0.1 --port 8011",
    url: "http://127.0.0.1:8011/api/health",
    reuseExistingServer: false,
  },
});
