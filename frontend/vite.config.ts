import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/** 生成供后端 Readiness 核对的前端版本清单。 */
function releaseManifestPlugin(): Plugin {
  const appVersion =
    process.env.VITE_APP_VERSION ?? process.env.npm_package_version ?? "development";
  return {
    name: "commerce-resolve-release-manifest",
    /** 在 Bundle 中写入与后端核对的最小版本事实。 */
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "release-manifest.json",
        source: `${JSON.stringify({ app_version: appVersion })}\n`,
      });
    },
  };
}

/** 配置 React 构建、测试环境与同源开发代理。 */
export default defineConfig({
  plugins: [react(), releaseManifestPlugin()],
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
