import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import type { PublicL2UpgradePreview } from "../../api/types";
import { L2UpgradeCard } from "./L2Cards";

const PREVIEW: PublicL2UpgradePreview = {
  preview_id: "preview-1",
  issue_summary: "商品存在质量问题",
  related_order_id: "CR-TEST-0001",
  context_categories: ["conversation", "business_tools", "policy"],
  allowed_tools: ["get_order", "get_shipment"],
  max_steps: 6,
  reads_confirmed_preferences: true,
  agent_identity: "AI 深度处理助手，并非真人",
};

it("使用客户可理解的文案说明一次性 AI 处理确认", () => {
  render(
    <L2UpgradeCard preview={PREVIEW} pending={false} onDecision={vi.fn()} />,
  );

  expect(
    screen.getByRole("heading", { name: "需要进一步核对" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "由 AI 继续处理" }),
  ).toBeVisible();
  expect(screen.getByText(/不是真人客服；退款仍会单独确认/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "确认进入深度处理" }),
  ).not.toBeInTheDocument();
});
