import { useQuery } from "@tanstack/react-query";
import { MessageCircleMore } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { getSupportOrder } from "../../api/client";
import { useAgentDrawer } from "./AgentDrawer";
import { FulfillmentTimeline } from "./FulfillmentTimeline";
import { ProductThumbnail } from "./ProductThumbnail";
import { StatusBadge } from "./StatusBadge";
import { formatDate, orderStatusLabel, SupportError, SupportLoading } from "./SupportStates";
import { formatAmount, statusTone } from "./supportPresenters";
import styles from "./Support.module.css";

/** 将商品分类转换为客户可理解的有限标签。 */
function categoryLabel(category: "general" | "apparel" | "hygiene" | "digital"): string {
  return { general: "普通商品", apparel: "服饰", hygiene: "卫生用品", digital: "数字商品" }[category];
}

/** 渲染订单事实、物流里程碑、演示交易与全局 Agent 入口。 */
export function OrderDetailPage() {
  const { orderId = "" } = useParams<{ orderId: string }>();
  const agent = useAgentDrawer();
  const order = useQuery({
    queryKey: ["support", "order", orderId],
    queryFn: () => getSupportOrder(orderId),
    enabled: orderId !== "",
  });

  if (order.isPending) {
    return <main className={styles.page}><SupportLoading label="正在读取订单详情…" /></main>;
  }
  if (order.isError || order.data === undefined) {
    return <main className={styles.page}><SupportError message="该订单不存在，或当前账号无权查看。" /></main>;
  }

  const detail = order.data;
  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑">
        <Link to="/support/orders">我的订单</Link><span>/</span><span>{detail.summary.order_id}</span>
      </nav>
      <div className={styles.detailMain}>
          <header className={styles.detailHero}>
            <div><span className={styles.eyebrow}>订单 {detail.summary.order_id}</span><h1>{detail.summary.item_title_preview ?? "订单详情"}</h1><p>创建于 {formatDate(detail.summary.created_at)}</p></div>
            <div className={styles.detailActions}>
              <StatusBadge label={orderStatusLabel(detail.summary.status)} tone={statusTone(detail.summary.status)} />
              <button
                type="button"
                onClick={() =>
                  void agent.openForOrder(
                    detail.summary.order_id,
                    `${detail.summary.item_title_preview ?? "订单"} · ${detail.summary.order_id}`,
                  )
                }
              >
                <MessageCircleMore aria-hidden="true" size={17} />
                咨询此订单
              </button>
            </div>
          </header>

          <section className={styles.sectionCard}>
            <header className={styles.sectionHeader}><div><span>商品信息</span><h2>订单内容</h2></div><strong>{detail.summary.item_count} 件</strong></header>
            {detail.items.length === 0 ? (
              <div className={styles.inlineState}>这是升级前创建的订单，商品明细尚未录入；订单和物流事实仍可查询。</div>
            ) : (
              <div className={styles.itemList}>
                {detail.items.map((item) => (
                  <article key={item.sku}>
                    <ProductThumbnail
                      src={item.image_url}
                      alt={item.image_alt ?? item.title}
                    />
                    <div>
                      <strong>{item.title}</strong>
                      <small>
                        {item.variant_title ?? item.sku} · {categoryLabel(item.product_category)}
                      </small>
                      {item.snapshot_state !== "complete" && (
                        <small>历史订单快照不完整，未展示的规格或金额不会推测补齐。</small>
                      )}
                    </div>
                    <div className={styles.itemAmount}>
                      <strong>{item.unit_amount ? formatAmount(item.unit_amount) : "价格未记录"}</strong>
                      <span>× {item.quantity}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className={styles.sectionCard}>
            <header className={styles.sectionHeader}><div><span>配送进度</span><h2>物流状态</h2></div>{detail.shipment?.estimated_delivery_at !== null && detail.shipment?.estimated_delivery_at !== undefined && <strong>预计 {detail.shipment.estimated_delivery_at}</strong>}</header>
            {detail.shipment === null || detail.shipment === undefined ? (
              <div className={styles.inlineState}>当前还没有物流信息。</div>
            ) : (
              <>
                <ol className={styles.shipmentTimeline}>
                  {detail.shipment_milestones.map((step) => (
                    <li className={styles[step.state]} key={step.key}><span aria-hidden="true" /><div><strong>{step.title}</strong>{step.detail !== null && step.detail !== undefined && <small>{step.detail}</small>}</div></li>
                  ))}
                </ol>
                <p className={styles.shipmentEvent}>最新进展：{detail.shipment.last_event}</p>
                <FulfillmentTimeline packages={detail.packages ?? []} />
              </>
            )}
          </section>

          <section className={styles.sectionCard}>
            <header className={styles.sectionHeader}><div><span>交易信息</span><h2>支付与退款</h2></div><small>本地演示交易</small></header>
            {detail.payment === null || detail.payment === undefined ? (
              <div className={styles.inlineState}>当前订单没有可用支付记录，暂不能申请退款。</div>
            ) : (
              <dl className={styles.factGrid}><div><dt>支付金额</dt><dd>¥{detail.payment.amount}</dd></div><div><dt>退款方式</dt><dd>{detail.payment.channel === "mock_card" ? "原银行卡" : "原支付钱包"}</dd></div><div><dt>支付状态</dt><dd>{detail.payment.status}</dd></div></dl>
            )}
            {detail.refunds.map((refund) => (
              <div className={styles.refundRow} key={refund.refund_id}><span>退款 ¥{refund.amount}</span><strong>{refund.status}</strong><small>{formatDate(refund.updated_at)}</small></div>
            ))}
            {detail.amount_summary && (
              <dl className={styles.amountSummary}>
                <div><dt>商品快照小计</dt><dd>{formatAmount(detail.amount_summary.item_subtotal)}</dd></div>
                <div><dt>实付金额</dt><dd>{formatAmount(detail.amount_summary.paid_amount)}</dd></div>
                <div><dt>已退款</dt><dd>{formatAmount(detail.amount_summary.refunded_amount)}</dd></div>
              </dl>
            )}
          </section>
          {detail.next_step && (
            <section className={styles.nextStepCard}>
              <span>建议下一步</span>
              <strong>{detail.next_step}</strong>
            </section>
          )}
      </div>
    </main>
  );
}
