import type {
  ServiceRecordSummary,
  SupportOrderSummary,
} from "../../api/types";
import styles from "./Support.module.css";

/** 将 ISO 时间转换为稳定的中文本地日期。 */
export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

/** 返回客户可理解的订单状态。 */
export function orderStatusLabel(status: SupportOrderSummary["status"]): string {
  return {
    processing: "处理中",
    shipped: "运输中",
    delivered: "已送达",
    cancelled: "已取消",
  }[status];
}

/** 返回客户可理解的服务状态。 */
export function serviceStatusLabel(
  status: ServiceRecordSummary["status"],
): string {
  return {
    waiting_user: "等待你的确认",
    in_progress: "处理中",
    completed: "已完成",
    needs_attention: "需要继续处理",
    cancelled: "已取消",
  }[status];
}

/** 渲染页面级加载状态。 */
export function SupportLoading({ label = "正在读取服务信息…" }: { label?: string }) {
  return <div className={styles.stateCard}>{label}</div>;
}

/** 渲染不会暴露内部错误的页面级失败状态。 */
export function SupportError({ message }: { message?: string }) {
  return (
    <div className={styles.stateCard} role="status">
      <strong>暂时无法读取这部分信息</strong>
      <p>{message ?? "请稍后重试，或返回售后首页选择其他服务。"}</p>
    </div>
  );
}

/** 渲染带明确下一步的空业务状态。 */
export function SupportEmpty({ title, message }: { title: string; message: string }) {
  return (
    <div className={styles.stateCard}>
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}
