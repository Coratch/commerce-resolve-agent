import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup, RotateCcw } from "lucide-react";

import {
  ApiError,
  getDemoWorkspace,
  resetDemoWorkspace,
} from "../../api/client";
import styles from "./Support.module.css";

/** 生成一次工作区重置请求的稳定客户端幂等标识。 */
function newResetRequestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `reset-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 展示本人演示数据集状态，并提供显式确认后的完整重置。 */
export function DemoSettingsPage() {
  const queryClient = useQueryClient();
  const workspace = useQuery({
    queryKey: ["demo-workspace"],
    queryFn: getDemoWorkspace,
  });
  const reset = useMutation({
    mutationFn: () => resetDemoWorkspace(newResetRequestId()),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["demo-workspace"] }),
        queryClient.invalidateQueries({ queryKey: ["support"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
    },
  });

  /** 展示完整影响范围，确认后才向服务端提交整区重置。 */
  function confirmReset(): void {
    const accepted = window.confirm(
      "确认恢复演示工作区吗？系统会保留三个公开订单号，但清除退款、服务记录、会话、Checkpoint 和长期偏好。此操作不会删除账号。",
    );
    if (accepted) reset.mutate();
  }

  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}>
        <div>
          <span className={styles.eyebrow}>演示环境</span>
          <h1>工作区设置</h1>
        </div>
        <p>版本化 Mock 数据让演示可以重复执行；重置不会连接真实订单或资金系统。</p>
      </header>
      <section className={styles.sectionCard}>
        <header className={styles.sectionHeader}>
          <div>
            <span>Dataset</span>
            <h2>当前演示工作区</h2>
          </div>
          <DatabaseBackup aria-hidden="true" size={24} />
        </header>
        {workspace.isPending && <p>正在读取工作区状态…</p>}
        {workspace.isError && (
          <p className={styles.inlineState}>
            {workspace.error instanceof ApiError
              ? workspace.error.message
              : "工作区状态暂时无法读取。"}
          </p>
        )}
        {workspace.data && (
          <dl className={styles.factGrid}>
            <div>
              <dt>数据版本</dt>
              <dd>{workspace.data.dataset_version}</dd>
            </div>
            <div>
              <dt>健康状态</dt>
              <dd>{workspace.data.dataset_status}</dd>
            </div>
            <div>
              <dt>重置代次</dt>
              <dd>{workspace.data.reset_generation}</dd>
            </div>
            <div>
              <dt>公开订单</dt>
              <dd>{workspace.data.order_count} 笔</dd>
            </div>
          </dl>
        )}
      </section>
      <section className={styles.resetCard}>
        <div>
          <span>完整重置</span>
          <h2>恢复三个标准售后场景</h2>
          <p>
            恢复物流延迟、质量问题和超期退款的基准事实；保留账号、角色与公开订单号。
          </p>
          <p>
            将清除由 Agent 产生的退款、服务记录、会话、Checkpoint 和长期偏好。
          </p>
        </div>
        <button type="button" disabled={reset.isPending} onClick={confirmReset}>
          <RotateCcw aria-hidden="true" size={17} />
          {reset.isPending ? "正在重置…" : "重置演示工作区"}
        </button>
      </section>
      {reset.data && (
        <p className={styles.resetNotice} role="status">
          已恢复 {reset.data.dataset_version}，当前为第{" "}
          {reset.data.reset_generation} 代，公开订单号保持不变。
        </p>
      )}
      {reset.error && (
        <p className={styles.resetNotice} role="status">
          {reset.error instanceof ApiError
            ? reset.error.message
            : "工作区暂时无法重置，请稍后重试。"}
        </p>
      )}
    </main>
  );
}
