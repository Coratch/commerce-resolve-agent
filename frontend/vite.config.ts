import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/** 配置 React 构建、测试环境与同源开发代理。 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "http://localhost/" },
    },
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
