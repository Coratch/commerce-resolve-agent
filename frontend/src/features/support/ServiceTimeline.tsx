import type { ServiceRecordDetail } from "../../api/types";
import { customerFacingText } from "./customerCopy";
import { formatDate } from "./SupportStates";
import styles from "./Support.module.css";

/** 渲染不包含内部 Agent 节点或诊断字段的客户服务时间线。 */
export function ServiceTimeline({
  steps,
}: {
  steps: ServiceRecordDetail["public_steps"];
}) {
  return (
    <ol className={styles.timeline} aria-label="服务处理进度">
      {steps.map((step) => (
        <li className={styles[step.state]} key={step.key}>
          <span aria-hidden="true" />
          <div>
            <strong>{customerFacingText(step.title)}</strong>
            {step.occurred_at !== null && step.occurred_at !== undefined && (
              <small>{formatDate(step.occurred_at)}</small>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
