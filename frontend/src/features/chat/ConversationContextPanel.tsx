import { ShieldCheck } from "lucide-react";

import type {
  ConversationSummary,
  PolicyCitation,
} from "../../api/types";
import styles from "./ConversationContextPanel.module.css";

export type ContextStatusTone = "active" | "attention" | "neutral";

interface ConversationContextPanelProps {
  conversation?: ConversationSummary;
  citations: PolicyCitation[];
  messageCount: number;
  statusDescription: string;
  statusLabel: string;
  statusTone: ContextStatusTone;
  isGuest: boolean;
}

/** 展示当前会话的处理状态、持久化范围、政策依据和审批边界。 */
export function ConversationContextPanel({
  conversation,
  citations,
  messageCount,
  statusDescription,
  statusLabel,
  statusTone,
  isGuest,
}: ConversationContextPanelProps) {
  return (
    <aside
      className={styles.panel}
      aria-label="会话上下文"
      data-testid="conversation-context"
    >
      <header className={styles.header}>
        <span>本次服务</span>
        <h2>处理上下文</h2>
      </header>

      <section className={styles.statusCard} data-tone={statusTone}>
        <div className={styles.statusHeading}>
          <span aria-hidden="true" />
          当前状态
        </div>
        <strong>{statusLabel}</strong>
        <p>{statusDescription}</p>
      </section>

      <dl className={styles.facts}>
        <div>
          <dt>会话</dt>
          <dd>{conversation?.title ?? "新售后会话"}</dd>
        </div>
        <div>
          <dt>消息</dt>
          <dd>{messageCount} 条</dd>
        </div>
        <div>
          <dt>记录</dt>
          <dd>
            {conversation?.history_state === "partial" ? "部分可恢复" : "服务端保存"}
          </dd>
        </div>
        <div>
          <dt>数据</dt>
          <dd>{isGuest ? "共享演示数据" : "当前账号数据"}</dd>
        </div>
      </dl>

      <section className={styles.sources}>
        <div className={styles.sectionHeading}>
          <h3>回答依据</h3>
          <span>{citations.length > 0 ? `${citations.length} 项` : "暂无"}</span>
        </div>
        {citations.length > 0 ? (
          <ul>
            {citations.slice(0, 3).map((citation) => (
              <li key={`${citation.document_id}-${citation.section_id}`}>
                <strong>{citation.heading}</strong>
                <small>
                  {citation.title} · {citation.version}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <p>政策问题回答后，引用会集中显示在这里。</p>
        )}
      </section>

      <section className={styles.guardrail}>
        <span aria-hidden="true">
          <ShieldCheck size={18} strokeWidth={1.8} />
        </span>
        <div>
          <strong>重要操作需确认</strong>
          <p>退款会先展示订单与金额，只有你明确批准后才执行。</p>
          <small>演示环境，不连接真实支付渠道。</small>
        </div>
      </section>
    </aside>
  );
}
