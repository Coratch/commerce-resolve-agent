import { describe, expect, it } from "vitest";

import type {
  PendingL2Response,
  PublicL2UpgradePreview,
} from "../../api/types";
import { selectPendingL2Cards } from "./useConversationSession";

const UPGRADE_PREVIEW = {
  preview_id: "preview-1",
} as PublicL2UpgradePreview;

describe("selectPendingL2Cards", () => {
  it("等待用户补充信息时忽略历史升级预览", () => {
    const pending = {
      pending: true,
      public_status: "l2_waiting_user",
      pending_action: "user_input",
      upgrade_preview: UPGRADE_PREVIEW,
      memory_proposal: null,
    } as PendingL2Response;

    expect(selectPendingL2Cards(pending)).toEqual({
      upgrade: null,
      memory: null,
    });
  });

  it("只有升级确认动作才展示升级预览", () => {
    const pending = {
      pending: true,
      public_status: "l2_awaiting_confirmation",
      pending_action: "upgrade_confirmation",
      upgrade_preview: UPGRADE_PREVIEW,
      memory_proposal: null,
    } as PendingL2Response;

    expect(selectPendingL2Cards(pending)).toEqual({
      upgrade: UPGRADE_PREVIEW,
      memory: null,
    });
  });
});
