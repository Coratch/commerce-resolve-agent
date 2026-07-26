import { type FormEvent, type KeyboardEvent, useState } from "react";
import { Bot, Send } from "lucide-react";

import type { SessionResponse } from "../../api/types";
import { L2UpgradeCard, MemoryProposalCard } from "./L2Cards";
import { ServiceResolutionCard } from "./ServiceResolutionCard";
import styles from "./ConversationPanel.module.css";
import { useConversationSession } from "./useConversationSession";
import { customerFacingText } from "../support/customerCopy";

interface ConversationPanelProps {
  threadId: string;
  session: SessionResponse;
  contextLabel?: string;
}

/** 渲染可嵌入订单页或服务页的公开对话、审批卡片和输入框。 */
export function ConversationPanel({
  threadId,
  session,
  contextLabel,
}: ConversationPanelProps) {
  const [message, setMessage] = useState("");
  const conversation = useConversationSession(threadId, session);
  const blocked =
    conversation.pendingRefund !== null ||
    conversation.pendingUpgrade !== null ||
    conversation.pendingMemory !== null;

  /** 提交当前非空消息，并立即清空编辑框。 */
  function submitCurrentMessage(): void {
    const normalized = message.trim();
    if (
      normalized === "" ||
      conversation.isSubmitting ||
      conversation.progress !== null ||
      blocked
    ) {
      return;
    }
    conversation.submit(normalized);
    setMessage("");
  }

  /** 处理表单发送并阻止浏览器整页提交。 */
  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    submitCurrentMessage();
  }

  /** 使用 Enter 发送，保留 Shift+Enter 换行和输入法组合。 */
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    submitCurrentMessage();
  }

  /** 将公开方案动作转换为既有受控消息入口，不直接产生业务副作用。 */
  function handleResolutionAction(
    action:
      | "view_order"
      | "view_policy"
      | "request_refund"
      | "upgrade_l2"
      | "provide_information",
  ): void {
    const prompts = {
      request_refund: "请发起退款",
      upgrade_l2: "请进入 AI 深度处理继续处理",
      provide_information: "请告诉我还需要补充哪些信息",
      view_order: "请查看当前订单",
      view_policy: "请说明适用的售后政策",
    };
    conversation.submit(prompts[action]);
  }

  return (
    <section className={styles.panel} aria-label="订单售后助手">
      <header className={styles.header}>
        <div>
          <span className={styles.mark} aria-hidden="true">
            <Bot size={18} strokeWidth={1.7} />
          </span>
          <div>
            <strong>智能售后助手</strong>
            <small>{contextLabel ?? "订单、物流与售后政策"}</small>
          </div>
        </div>
        <span className={styles.state}>
          {conversation.progress !== null ? "处理中" : blocked ? "等待确认" : "在线"}
        </span>
      </header>
      <div className={styles.messages} aria-live="polite">
        {conversation.isLoading && <p className={styles.muted}>正在恢复服务记录…</p>}
        {!conversation.isLoading && conversation.messages.length === 0 && (
          <div className={styles.empty}>
            <strong>直接描述你的问题</strong>
            <p>当前订单已安全关联，无需重复输入订单号。</p>
          </div>
        )}
        {conversation.messages.map((item) => (
          <div
            className={item.role === "user" ? styles.userGroup : styles.assistantGroup}
            key={item.id}
          >
            <article
              className={item.role === "user" ? styles.user : styles.assistant}
            >
              <p>{customerFacingText(item.content)}</p>
              {item.response?.citations.map((citation) => (
                <small key={`${citation.document_id}-${citation.section_id}`}>
                  来源：{citation.title} · {citation.heading} · 第 {citation.line_start}–
                  {citation.line_end} 行
                </small>
              ))}
            </article>
            {item.response?.service_resolution && (
              <ServiceResolutionCard
                resolution={item.response.service_resolution}
                onAction={handleResolutionAction}
              />
            )}
          </div>
        ))}
        {conversation.progress !== null && (
          <article className={styles.assistant}>
            <p>{customerFacingText(conversation.progress)}</p>
          </article>
        )}
        {conversation.pendingUpgrade !== null && (
          <L2UpgradeCard
            preview={conversation.pendingUpgrade}
            pending={conversation.isSubmitting}
            onDecision={conversation.decideUpgradeAction}
          />
        )}
        {conversation.pendingMemory !== null && (
          <MemoryProposalCard
            proposal={conversation.pendingMemory}
            pending={conversation.isSubmitting}
            onDecision={conversation.decideMemoryAction}
          />
        )}
        {conversation.pendingRefund !== null && (
          <aside className={styles.refund} aria-label="待审批退款预览">
            <span>演示退款确认</span>
            <h3>¥{conversation.pendingRefund.amount}</h3>
            <p>
              订单 {conversation.pendingRefund.order_id} · 原路退回{
                customerFacingText(conversation.pendingRefund.channel)
              }
            </p>
            <div>
              <button
                type="button"
                className={styles.secondary}
                disabled={conversation.isSubmitting}
                onClick={() => conversation.decideRefundAction("reject")}
              >
                暂不退款
              </button>
              <button
                type="button"
                disabled={conversation.isSubmitting}
                onClick={() => conversation.decideRefundAction("approve")}
              >
                确认演示退款
              </button>
            </div>
          </aside>
        )}
      </div>
      <form className={styles.composer} onSubmit={handleSubmit}>
        <label className="sr-only" htmlFor={`assistant-message-${threadId}`}>
          输入售后问题
        </label>
        <textarea
          id={`assistant-message-${threadId}`}
          value={message}
          rows={2}
          maxLength={2000}
          placeholder="例如：它现在到哪里了？"
          disabled={blocked || conversation.isSubmitting}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="submit"
          disabled={message.trim() === "" || blocked || conversation.isSubmitting}
        >
          <Send aria-hidden="true" size={15} />
          发送
        </button>
        <small>Enter 发送 · Shift + Enter 换行</small>
      </form>
    </section>
  );
}
