import { Link } from "react-router-dom";

import type { ServiceResolution } from "../../api/types";
import { customerFacingText } from "../support/customerCopy";
import styles from "./ServiceResolutionCard.module.css";

const actionLabels: Record<
  ServiceResolution["allowed_actions"][number],
  string
> = {
  view_order: "查看订单",
  view_policy: "查看售后政策",
  request_refund: "申请演示退款",
  upgrade_l2: "进入 AI 深度处理",
  provide_information: "补充信息",
};

/** 从已验证事实中提取可导航的订单号，不读取自由文本。 */
function orderIdFromResolution(
  resolution: ServiceResolution,
): string | undefined {
  const evidence = resolution.verified_facts.find(
    (item) => item.category === "order" && item.evidence_id.startsWith("order:"),
  );
  return evidence?.evidence_id.slice("order:".length);
}

/** 将组合咨询方案渲染为事实、证据、建议和受控动作卡片。 */
export function ServiceResolutionCard({
  resolution,
  onAction,
}: {
  resolution: ServiceResolution;
  onAction: (
    action: ServiceResolution["allowed_actions"][number],
  ) => void;
}) {
  const orderId = orderIdFromResolution(resolution);
  return (
    <aside className={styles.card} aria-label="智能服务方案">
      <header className={styles.header}>
        <div>
          <span>服务目标</span>
          <h3>{customerFacingText(resolution.goal)}</h3>
        </div>
        <span className={styles.stop}>
          {resolution.stop_reason === "completed" ? "事实已核对" : "需要继续处理"}
        </span>
      </header>

      <div className={styles.progress} aria-label="处理进度">
        {resolution.progress.map((step) => (
          <div key={step.key}>
            <strong>
              {step.state === "completed"
                ? "已完成"
                : step.state === "blocked"
                  ? "受阻"
                  : "已跳过"}
            </strong>
            <span>{customerFacingText(step.title)}</span>
          </div>
        ))}
      </div>

      {resolution.verified_facts.length > 0 && (
        <section className={styles.section}>
          <span>已验证事实</span>
          <ul className={styles.facts}>
            {resolution.verified_facts.map((fact) => (
              <li key={fact.evidence_id}>{customerFacingText(fact.statement)}</li>
            ))}
          </ul>
        </section>
      )}

      {resolution.missing_information.length > 0 && (
        <div className={styles.missing}>
          仍需确认：{resolution.missing_information.map(customerFacingText).join("、")}
        </div>
      )}

      <section className={styles.section}>
        <span>建议</span>
        <ul className={styles.recommendations}>
          {resolution.recommendations.map((item) => (
            <li key={item}>{customerFacingText(item)}</li>
          ))}
        </ul>
      </section>

      <div className={styles.next}>{customerFacingText(resolution.next_step)}</div>
      <div className={styles.actions} aria-label="允许的下一步操作">
        {resolution.allowed_actions.map((action) =>
          action === "view_order" && orderId ? (
            <Link to={`/orders/${encodeURIComponent(orderId)}`} key={action}>
              {actionLabels[action]}
            </Link>
          ) : action === "view_policy" ? (
            <Link to="/support" key={action}>
              {actionLabels[action]}
            </Link>
          ) : (
            <button type="button" key={action} onClick={() => onAction(action)}>
              {actionLabels[action]}
            </button>
          ),
        )}
      </div>
    </aside>
  );
}
