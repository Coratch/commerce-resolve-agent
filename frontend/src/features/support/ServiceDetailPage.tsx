import { useQuery } from "@tanstack/react-query";
import { ArrowUpLeft, MessageCircleMore } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { getSupportService } from "../../api/client";
import { useAgentDrawer } from "./AgentDrawer";
import { ProductThumbnail } from "./ProductThumbnail";
import { ServiceTimeline } from "./ServiceTimeline";
import { StatusBadge } from "./StatusBadge";
import { formatDate, serviceStatusLabel, SupportError, SupportLoading } from "./SupportStates";
import { statusTone } from "./supportPresenters";
import { customerFacingText } from "./customerCopy";
import styles from "./Support.module.css";

/** 渲染一条服务的客户时间线，并从关联订单恢复全局 Agent。 */
export function ServiceDetailPage() {
  const { serviceId = "" } = useParams<{ serviceId: string }>();
  const agent = useAgentDrawer();
  const service = useQuery({
    queryKey: ["support", "service", serviceId],
    queryFn: () => getSupportService(serviceId),
    enabled: serviceId !== "",
  });

  if (service.isPending) {
    return <main className={styles.page}><SupportLoading label="正在读取服务详情…" /></main>;
  }
  if (service.isError || service.data === undefined) {
    return <main className={styles.page}><SupportError message="该服务记录不存在，或当前账号无权查看。" /></main>;
  }

  const detail = service.data;
  return (
    <main className={styles.page}>
      <nav className={styles.breadcrumb} aria-label="面包屑"><Link to="/support/services">服务进度</Link><span>/</span><span>{detail.summary.service_id}</span></nav>
      <div className={styles.detailMain}>
          <header className={styles.detailHero}>
            <div className={styles.serviceHeroIdentity}>
              <ProductThumbnail
                src={detail.summary.product_preview?.image_url}
                alt={detail.summary.product_preview?.image_alt ?? detail.summary.title}
                size="large"
              />
              <div><span className={styles.eyebrow}>{detail.summary.kind === "refund" ? "退款服务" : "复杂售后服务"}</span><h1>{customerFacingText(detail.summary.title)}</h1><p>最后更新于 {formatDate(detail.summary.updated_at)}</p></div>
            </div>
            <StatusBadge label={serviceStatusLabel(detail.summary.status)} tone={statusTone(detail.summary.status)} />
          </header>
          <section className={styles.sectionCard}>
            <header className={styles.sectionHeader}><div><span>当前进展</span><h2>服务处理时间线</h2></div></header>
            <ServiceTimeline steps={detail.public_steps} />
            {detail.summary.next_action !== null && detail.summary.next_action !== undefined && <div className={styles.nextAction}><strong>下一步</strong><p>{customerFacingText(detail.summary.next_action)}</p></div>}
          </section>
          {detail.result_summary !== null && detail.result_summary !== undefined && (
            <section className={styles.sectionCard}><header className={styles.sectionHeader}><div><span>处理结果</span><h2>服务结论</h2></div></header><p className={styles.resultSummary}>{customerFacingText(detail.result_summary)}</p></section>
          )}
          {detail.citations.length > 0 && (
            <section className={styles.sectionCard}><header className={styles.sectionHeader}><div><span>政策依据</span><h2>本次处理参考</h2></div></header><ul className={styles.citations}>{detail.citations.map((citation) => <li key={`${citation.source}-${citation.locator}`}><strong>{citation.source}</strong><span>{citation.version} · {citation.locator}</span></li>)}</ul></section>
          )}
          {detail.summary.order_id !== null && detail.summary.order_id !== undefined && (
            <Link className={styles.inlineLink} to={`/support/orders/${detail.summary.order_id}`}>
              <ArrowUpLeft aria-hidden="true" size={16} />
              返回关联订单 {detail.summary.order_id}
            </Link>
          )}
          {detail.summary.order_id !== null && detail.summary.order_id !== undefined && (
            <button
              type="button"
              className={styles.inlineAgentButton}
              onClick={() =>
                void agent.openForOrder(
                  detail.summary.order_id as string,
                  customerFacingText(detail.summary.title),
                )
              }
            >
              <MessageCircleMore aria-hidden="true" size={17} />
              继续智能售后
            </button>
          )}
      </div>
    </main>
  );
}
