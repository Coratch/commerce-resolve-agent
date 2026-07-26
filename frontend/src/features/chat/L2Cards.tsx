import type {
  PublicL2CaseSummary,
  PublicL2TraceEvent,
  PublicL2UpgradePreview,
  PublicMemoryProposal,
} from "../../api/types";
import { customerFacingText } from "../support/customerCopy";
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
  cases: PublicL2CaseSummary[];
  hasMore: boolean;
  loadingMore: boolean;
  traceError: string | null;
  onCaseChange: (caseId: string) => void;
  onLoadMore: () => void;
}

const CASE_STATUS_LABELS: Record<string, string> = {
  l2_running: "处理中",
  l2_resolved: "已完成",
  awaiting_tool_approval: "等待确认",
  awaiting_memory_approval: "等待偏好确认",
  stopped: "已停止",
  failed: "暂未完成",
  cancelled: "已取消",
};

const EVENT_LABELS: Record<string, string> = {
  "context.prepared": "已准备相关订单与服务信息",
  "model.started": "正在分析问题",
  "model.completed": "已生成处理建议",
  "tool.started": "正在核对业务状态",
  "tool.completed": "已完成业务核对",
  "answer.completed": "已形成处理结论",
  "case.completed": "本次处理已完成",
  "case.stopped": "本次处理已停止",
  case_created: "已创建二线服务",
  context_loaded: "已读取会话与订单信息",
  context_prepared: "已准备相关订单与服务信息",
  model_decision: "已完成服务判断",
  tool_result: "已完成业务核对",
  user_input_received: "已收到补充信息",
  memory_decision: "已处理服务偏好",
  refund_proposed: "已生成退款建议",
  refund_result: "已核对退款结果",
  case_completed: "本次处理已完成",
};

const SOURCE_LABELS: Record<string, string> = {
  business_observation: "订单与服务状态",
  policy_document: "售后政策",
  confirmed_memory: "已确认服务偏好",
  conversation: "当前对话",
};

const MEMORY_TYPE_LABELS: Record<string, string> = {
  preferred_language: "回复语言",
  response_detail: "回复详细程度",
  communication_tone: "沟通语气",
};

const MEMORY_VALUE_LABELS: Record<string, string> = {
  "zh-CN": "中文",
  en: "英文",
  concise: "简洁",
  standard: "标准",
  detailed: "详细",
  neutral: "中性",
  friendly: "友好",
};

/** 将内部 Case 状态转换为客户可以理解的服务状态。 */
function caseStatusLabel(status: string): string {
  return CASE_STATUS_LABELS[status] ?? "处理中";
}

/** 将内部轨迹事件转换为不暴露实现细节的处理说明。 */
function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? "已更新处理进度";
}

/** 将上下文来源类型转换为客户可以理解的证据类别。 */
function sourceLabel(sourceType: string): string {
  return SOURCE_LABELS[sourceType] ?? "相关服务信息";
}

/** 展示 AI 身份和受控服务边界，不公开内部工具名称或预算指标。 */
export function L2UpgradeCard({
  preview,
  pending,
  onDecision,
}: UpgradeCardProps) {
  return (
    <aside className={styles.card} aria-label="AI 售后处理确认">
      <span className={styles.label}>AI 售后助手</span>
      <h2>需要进一步核对</h2>
      <dl>
        <div>
          <dt>本次问题</dt>
          <dd>{customerFacingText(preview.issue_summary)}</dd>
        </div>
        <div>
          <dt>核对范围</dt>
          <dd>订单、物流、退款状态与售后政策核对</dd>
        </div>
        <div>
          <dt>处理方式</dt>
          <dd>逐步核对并可能继续向你询问补充信息</dd>
        </div>
        <div>
          <dt>身份说明</dt>
          <dd>AI 售后助手，不是真人客服；退款仍会单独确认</dd>
        </div>
      </dl>
      <div className={styles.actions}>
        <button type="button" disabled={pending} onClick={() => onDecision("cancel")}>
          暂不继续
        </button>
        <button
          type="button"
          className={styles.primary}
          disabled={pending}
          onClick={() => onDecision("confirm")}
        >
          由 AI 继续处理
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
      <span className={styles.label}>服务偏好建议</span>
      <h2>是否保存这条偏好？</h2>
      <p>
        {MEMORY_TYPE_LABELS[proposal.memory_type] ?? "服务偏好"}：
        <strong>{MEMORY_VALUE_LABELS[proposal.value] ?? proposal.value}</strong>
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

/** 展示不含工具、模型、Token 和内部错误码的客户侧处理记录。 */
export function L2CasePanel({
  summary,
  events,
  cases,
  hasMore,
  loadingMore,
  traceError,
  onCaseChange,
  onLoadMore,
}: CasePanelProps) {
  return (
    <aside className={styles.panel} aria-label="AI 深度处理记录">
      <div className={styles.panelHeader}>
        <div>
          <span className={styles.label}>AI 深度处理记录</span>
          <h2>{caseStatusLabel(summary.status)}</h2>
        </div>
        <p>{customerFacingText(summary.issue_summary)}</p>
      </div>
      {cases.length > 1 && (
        <label className={styles.caseSelector}>
          历史服务记录
          <select
            value={summary.case_id}
            onChange={(event) => onCaseChange(event.target.value)}
          >
            {cases.map((item) => (
              <option key={item.case_id} value={item.case_id}>
                {customerFacingText(item.issue_summary)} ·{" "}
                {caseStatusLabel(item.status)}
              </option>
            ))}
          </select>
        </label>
      )}
      {summary.trace_state === "partial" && (
        <p className={styles.traceNotice}>早期服务仅保留了部分处理记录。</p>
      )}
      {summary.failure_attribution !== null &&
        summary.failure_attribution !== undefined && (
          <p className={styles.traceNotice}>
            本次处理未完整结束，你可以重新描述问题后继续咨询。
          </p>
        )}
      <ol>
        {events.map((event) => (
          <li key={event.sequence_no}>
            <div>
              <strong>{eventLabel(event.event_type)}</strong>
              {event.context_summary !== null &&
                event.context_summary !== undefined && (
                  <details>
                    <summary>查看处理依据</summary>
                    <small>
                      {event.context_summary.source_types
                        .map(sourceLabel)
                        .join("、") || "基础服务信息"}{" "}
                      · 已读取 {event.context_summary.selected_count} 项信息
                      {event.context_summary.facts_refreshed > 0
                        ? " · 已同步最新状态"
                        : ""}
                      {event.context_summary.truncated ? " · 已控制展示范围" : ""}
                    </small>
                  </details>
                )}
            </div>
            <span>第 {event.sequence_no} 步</span>
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
