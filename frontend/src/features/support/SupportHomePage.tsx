import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Bot,
  CircleCheckBig,
  Clock3,
  PackageSearch,
  Radio,
  RotateCcw,
} from "lucide-react";
import { Link } from "react-router-dom";

import { getSupportOverview } from "../../api/client";
import type { SessionResponse } from "../../api/types";
import { useAgentDrawer } from "./AgentDrawer";
import { ProductThumbnail } from "./ProductThumbnail";
import { StatusBadge } from "./StatusBadge";
import {
  formatDate,
  orderStatusLabel,
  serviceStatusLabel,
  SupportEmpty,
  SupportError,
  SupportLoading,
} from "./SupportStates";
import { statusTone } from "./supportPresenters";
import { customerFacingText } from "./customerCopy";
import styles from "./Support.module.css";

/** 渲染订单优先的售后首页，不创建会话或调用模型。 */
export function SupportHomePage({ session }: { session: SessionResponse }) {
  const agent = useAgentDrawer();
  const overview = useQuery({
    queryKey: ["support", "overview", session.mode, session.username],
    queryFn: getSupportOverview,
  });

  return (
    <main className={styles.page}>
      <section className={styles.homeIntro}>
        <div className={styles.introSequence} aria-hidden="true">
          <span>01</span>
          <small>AFTER / CARE</small>
        </div>
        <div className={styles.introCopy}>
          <span className={styles.eyebrow}>CommerceResolve service flow</span>
          <h1>
            {session.username}，<em>服务正在继续。</em>
          </h1>
          <p>订单、物流与售后进度汇入同一条服务流。看清状态，然后完成下一步。</p>
        </div>
        <div className={styles.homeIdentity}>
          <span className={styles.environmentState}>
            <Radio aria-hidden="true" size={15} />
            独立演示工作区
          </span>
          <Link className={styles.primaryLink} to="/support/orders">
            查看全部订单
            <ArrowUpRight aria-hidden="true" size={17} />
          </Link>
        </div>
      </section>

      <section className={styles.taskStrip} aria-label="常用售后入口">
        <Link to="/support/orders?view=shipping">
          <span className={styles.taskIcon} aria-hidden="true">
            <PackageSearch size={20} strokeWidth={1.7} />
          </span>
          <span><strong>跟踪物流</strong><small>查看包裹进度与预计送达</small></span>
          <ArrowUpRight aria-hidden="true" size={20} />
        </Link>
        <Link to="/support/services">
          <span className={styles.taskIcon} aria-hidden="true">
            <RotateCcw size={20} strokeWidth={1.7} />
          </span>
          <span><strong>继续售后</strong><small>处理审批、补充信息或查看结果</small></span>
          <ArrowUpRight aria-hidden="true" size={20} />
        </Link>
        <button type="button" onClick={agent.open}>
          <span className={styles.taskIcon} aria-hidden="true">
            <Bot size={20} strokeWidth={1.7} />
          </span>
          <span><strong>咨询智能助手</strong><small>查询订单、物流与售后政策</small></span>
          <ArrowUpRight aria-hidden="true" size={20} />
        </button>
      </section>

      {overview.isPending && <SupportLoading />}
      {overview.isError && <SupportError />}
      {overview.data !== undefined && (
        <>
          <section className={styles.attentionSummary} aria-label="售后状态摘要">
            <div>
              <span><Clock3 aria-hidden="true" size={14} />进行中的服务</span>
              <strong>{overview.data.active_services.length}</strong>
              <small>{overview.data.active_services.length > 0 ? "有服务需要继续关注" : "当前没有待处理服务"}</small>
            </div>
            <div>
              <span><PackageSearch aria-hidden="true" size={14} />最近订单</span>
              <strong>{overview.data.recent_orders.length}</strong>
              <small>按最近更新时间展示</small>
            </div>
            <div>
              <span><CircleCheckBig aria-hidden="true" size={14} />服务保障</span>
              <strong>可恢复</strong>
              <small>刷新或切换页面后继续原任务</small>
            </div>
          </section>

          <div className={styles.homeGrid}>
            <section className={`${styles.sectionCard} ${styles.attentionCard}`}>
            <header className={styles.sectionHeader}>
              <div><span>待关注</span><h2>进行中的服务</h2></div>
              <Link to="/support/services">全部服务</Link>
            </header>
            {overview.data.active_services.length === 0 ? (
              <SupportEmpty title="当前没有待处理服务" message="退款审批或复杂售后产生后，会在这里显示下一步。" />
            ) : (
              <div className={styles.recordList}>
                {overview.data.active_services.map((service) => (
                  <Link to={`/support/services/${service.service_id}`} key={service.service_id}>
                    <ProductThumbnail
                      src={service.product_preview?.image_url}
                      alt={service.product_preview?.image_alt ?? service.title}
                      size="small"
                    />
                    <div><strong>{customerFacingText(service.title)}</strong><small>{customerFacingText(service.next_action ?? "查看最新处理结果")}</small></div>
                    <div><StatusBadge label={serviceStatusLabel(service.status)} tone={statusTone(service.status)} /><small>{formatDate(service.updated_at)}</small></div>
                  </Link>
                ))}
              </div>
            )}
            </section>
            <section className={styles.sectionCard}>
            <header className={styles.sectionHeader}>
              <div><span>最近更新</span><h2>我的订单</h2></div>
              <Link to="/support/orders">全部订单</Link>
            </header>
            {overview.data.recent_orders.length === 0 ? (
              <SupportEmpty title="还没有可显示的订单" message="订单准备完成后会自动出现在这里。" />
            ) : (
              <div className={styles.recordList}>
                {overview.data.recent_orders.map((order) => (
                  <Link to={`/support/orders/${order.order_id}`} key={order.order_id}>
                    <ProductThumbnail
                      src={order.preview_items?.[0]?.image_url}
                      alt={order.preview_items?.[0]?.image_alt ?? order.item_title_preview ?? "订单商品"}
                      size="small"
                    />
                    <div><strong>{order.item_title_preview ?? order.order_id}</strong><small>{order.order_id} · {order.item_count} 件商品</small></div>
                    <div><StatusBadge label={orderStatusLabel(order.status)} tone={statusTone(order.status)} /><small>{formatDate(order.updated_at)}</small></div>
                  </Link>
                ))}
              </div>
            )}
            </section>
          </div>
        </>
      )}
    </main>
  );
}
