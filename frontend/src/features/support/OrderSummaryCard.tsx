import { ArrowUpRight, MessageCircleMore } from "lucide-react";
import { Link } from "react-router-dom";

import type { SupportOrderSummary } from "../../api/types";
import { formatDate, orderStatusLabel } from "./SupportStates";
import { ProductThumbnail } from "./ProductThumbnail";
import { StatusBadge } from "./StatusBadge";
import { customerFacingText } from "./customerCopy";
import { shipmentStatusLabel, statusTone } from "./supportPresenters";
import { useAgentDrawer } from "./AgentDrawer";
import styles from "./Support.module.css";

/** 展示订单列表使用的商品、履约、服务和下一步摘要。 */
export function OrderSummaryCard({ order }: { order: SupportOrderSummary }) {
  const agent = useAgentDrawer();
  const previews = order.preview_items ?? [];
  const preview = previews[0];
  return (
    <article className={styles.commercialOrderCard}>
      <Link
        className={styles.orderCardMain}
        to={`/support/orders/${order.order_id}`}
      >
        <ProductThumbnail
          src={preview?.image_url}
          alt={preview?.image_alt ?? order.item_title_preview ?? "订单商品"}
          size="large"
        />
        <div className={styles.commercialOrderBody}>
        <div className={styles.orderCardTop}>
          <span className={styles.orderNumber}>{order.order_id}</span>
          <StatusBadge
            label={orderStatusLabel(order.status)}
            tone={statusTone(order.status)}
          />
        </div>
        <h2>{order.item_title_preview ?? "商品信息暂未录入"}</h2>
        <p>
          {preview?.variant_title ?? `${order.item_count} 件商品`}
          {previews.length > 1
            ? ` · 另有 ${previews.length - 1} 种商品`
            : ""}
        </p>
        <div className={styles.orderSignals}>
          <span>{shipmentStatusLabel(order.shipment_status)}</span>
          <span>{order.fulfillment_summary ?? "履约信息待更新"}</span>
          <span>{order.customer_stage ?? "订单处理中"}</span>
        </div>
        {order.latest_service_summary && (
          <div className={styles.serviceSignal}>
            <strong>售后进展</strong>
            <span>{customerFacingText(order.latest_service_summary)}</span>
          </div>
        )}
        <footer>
          <small>更新于 {formatDate(order.updated_at)}</small>
          <strong>
            查看详情
            <ArrowUpRight aria-hidden="true" size={16} />
          </strong>
        </footer>
        </div>
      </Link>
      <button
        type="button"
        className={styles.consultOrderButton}
        onClick={() =>
          void agent.openForOrder(
            order.order_id,
            `${order.item_title_preview ?? "订单"} · ${order.order_id}`,
          )
        }
      >
        <MessageCircleMore aria-hidden="true" size={16} />
        咨询此订单
      </button>
    </article>
  );
}
