import type { SupportShipmentPackage } from "../../api/types";
import { formatDate } from "./SupportStates";
import { StatusBadge } from "./StatusBadge";
import { shipmentStatusLabel, statusTone } from "./supportPresenters";
import styles from "./Support.module.css";

/** 按包裹展示履约事实和关联商品，不推测缺失物流。 */
export function FulfillmentTimeline({
  packages,
}: {
  packages: SupportShipmentPackage[];
}) {
  if (packages.length === 0) {
    return (
      <div className={styles.inlineState}>
        当前订单没有包裹明细，以下状态来自整单物流。
      </div>
    );
  }
  return (
    <div className={styles.packageList}>
      {packages.map((item, index) => (
        <article className={styles.packageCard} key={item.package_id}>
          <header>
            <div>
              <span>包裹 {index + 1}</span>
              <strong>{item.carrier ?? "承运方待更新"}</strong>
            </div>
            <StatusBadge
              label={shipmentStatusLabel(item.status)}
              tone={statusTone(item.status)}
            />
          </header>
          <p>{item.last_event}</p>
          <small>
            {item.tracking_number ? `运单 ${item.tracking_number}` : "暂无运单号"} ·
            更新于 {formatDate(item.updated_at)}
          </small>
          <ul>
            {item.items.map((product) => (
              <li key={`${item.package_id}-${product.sku}`}>
                <span>{product.title}</span>
                <strong>× {product.quantity}</strong>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}
