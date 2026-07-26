import styles from "./Support.module.css";

/** 用统一颜色层级展示订单、履约或服务状态。 */
export function StatusBadge({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "info" | "success" | "warning";
}) {
  return (
    <span className={`${styles.statusBadge} ${styles[`statusBadge${tone}`]}`}>
      {label}
    </span>
  );
}
