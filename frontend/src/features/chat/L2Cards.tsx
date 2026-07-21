import type {
  PublicL2CaseMetrics,
  PublicL2CaseSummary,
  PublicL2TraceEvent,
  PublicL2UpgradePreview,
  PublicMemoryProposal,
} from "../../api/types";
import styles from "./L2Cards.module.css";

interface UpgradeCardProps {
  preview: PublicL2UpgradePreview;
  pending: boolean;
  onDecision: (decision: "confirm" | "cancel") => void;
}

interface MemoryCardProps {
  proposal: PublicMemoryProposal;
  pending: boolean;
  onDecision: (decision: "confirm" | "reject") => void;
}

interface CasePanelProps {
  summary: PublicL2CaseSummary;
  events: PublicL2TraceEvent[];
  metrics?: PublicL2CaseMetrics;
  cases: PublicL2CaseSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  traceError: string | null;
  onCaseChange: (caseId: string) => void;
  onLoadMore: () => void;
}

/** 展示 AI 身份、允许读取的上下文和固定步骤预算。 */
export function L2UpgradeCard({
  preview,
  pending,
  onDecision,
}: UpgradeCardProps) {
  return (
    <aside className={styles.card} aria-label="AI 二线客服升级预览">
      <span className={styles.label}>L2 · AI SUPPORT</span>
      <h2>{preview.agent_identity}</h2>
      <p>{preview.issue_summary}</p>
      <dl>
        <div>
          <dt>可用工具</dt>
          <dd>{preview.allowed_tools.join("、")}</dd>
        </div>
        <div>
          <dt>最大步骤</dt>
          <dd>{preview.max_steps}</dd>
        </div>
        <div>
          <dt>长期偏好</dt>
          <dd>{preview.reads_confirmed_preferences ? "读取已确认偏好" : "不读取"}</dd>
        </div>
      </dl>
      <div className={styles.actions}>
        <button type="button" disabled={pending} onClick={() => onDecision("cancel")}>
          取消升级
        </button>
        <button
          type="button"
          className={styles.primary}
          disabled={pending}
          onClick={() => onDecision("confirm")}
        >
          确认进入 AI 二线
        </button>
      </div>
    </aside>
  );
}

/** 展示模型提出但尚未写入 Store 的受限长期偏好。 */
export function MemoryProposalCard({
  proposal,
  pending,
  onDecision,
}: MemoryCardProps) {
  return (
    <aside className={styles.card} aria-label="长期偏好建议">
      <span className={styles.label}>MEMORY PROPOSAL</span>
      <h2>是否保存这条偏好？</h2>
      <p>
        {proposal.memory_type} = <strong>{proposal.value}</strong>
      </p>
      <small>{proposal.purpose}</small>
      <div className={styles.actions}>
        <button type="button" disabled={pending} onClick={() => onDecision("reject")}>
          不保存
        </button>
        <button
          type="button"
          className={styles.primary}
          disabled={pending}
          onClick={() => onDecision("confirm")}
        >
          确认保存
        </button>
      </div>
    </aside>
  );
}

/** 展示当前 Case 的公开预算用量和不含隐藏推理的动作轨迹。 */
export function L2CasePanel({
  summary,
  events,
  metrics,
  cases,
  hasMore,
  loadingMore,
  traceError,
  onCaseChange,
  onLoadMore,
}: CasePanelProps) {
  return (
    <aside className={styles.panel} aria-label="AI 二线 Case 状态">
      <div className={styles.panelHeader}>
        <div>
          <span className={styles.label}>AI L2 CASE</span>
          <h2>{summary.status}</h2>
        </div>
        <p>
          {summary.steps_used} 步 · {summary.model_calls_used} 次模型 ·{" "}
          {summary.tool_calls_used} 次工具
        </p>
      </div>
      {cases.length > 1 && (
        <label className={styles.caseSelector}>
          历史 Case
          <select
            value={summary.case_id}
            onChange={(event) => onCaseChange(event.target.value)}
          >
            {cases.map((item) => (
              <option key={item.case_id} value={item.case_id}>
                {item.issue_summary} · {item.status}
              </option>
            ))}
          </select>
        </label>
      )}
      {summary.trace_state === "partial" && (
        <p className={styles.traceNotice}>早期 Case 仅有部分处理记录。</p>
      )}
      {summary.failure_attribution !== null &&
        summary.failure_attribution !== undefined && (
          <p className={styles.traceNotice}>
            停止归因：{summary.failure_attribution}
          </p>
        )}
      {metrics !== undefined && (
        <p className={styles.metrics}>
          上下文 {metrics.selected_count}/{metrics.candidate_count} 项 · 输入{" "}
          {metrics.provider_input_tokens} Token · 上下文准备{" "}
          {metrics.context_duration_ms} ms
        </p>
      )}
      <ol>
        {events.map((event) => (
          <li key={event.sequence_no}>
            <div>
              <strong>{event.event_type}</strong>
              {event.context_summary !== null &&
                event.context_summary !== undefined && (
                  <details>
                    <summary>处理依据</summary>
                    <small>
                      {event.context_summary.source_types.join("、") || "基础上下文"} ·{" "}
                      {event.context_summary.selected_count} 项
                      {event.context_summary.facts_refreshed > 0
                        ? ` · 刷新 ${event.context_summary.facts_refreshed} 项`
                        : ""}
                      {event.context_summary.truncated ? " · 已裁剪" : ""}
                    </small>
                  </details>
                )}
            </div>
            <span>
              #{event.sequence_no} · {event.tool_category ?? "Harness"} ·{" "}
              {event.result_code}
            </span>
          </li>
        ))}
      </ol>
      {traceError !== null && <p className={styles.traceNotice}>{traceError}</p>}
      {hasMore && (
        <button
          type="button"
          className={styles.loadMore}
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? "正在读取…" : "加载更多处理记录"}
        </button>
      )}
    </aside>
  );
}
