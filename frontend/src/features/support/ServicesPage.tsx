import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { listSupportServices } from "../../api/client";
import { ProductThumbnail } from "./ProductThumbnail";
import { StatusBadge } from "./StatusBadge";
import { formatDate, serviceStatusLabel, SupportEmpty, SupportError, SupportLoading } from "./SupportStates";
import { statusTone } from "./supportPresenters";
import { customerFacingText } from "./customerCopy";
import styles from "./Support.module.css";

/** 渲染当前客户的进行中或历史服务记录。 */
export function ServicesPage() {
  const [view, setView] = useState<"active" | "history">("active");
  const services = useInfiniteQuery({
    queryKey: ["support", "services", view],
    queryFn: ({ pageParam }) => listSupportServices(view, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  const items = services.data?.pages.flatMap((page) => page.services) ?? [];

  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}><div><span className={styles.eyebrow}>售后记录</span><h1>服务进度</h1></div><p>查看退款审批、复杂售后的当前阶段、下一步和最终结果。</p></header>
      <div className={styles.segmented} aria-label="服务记录范围">
        <button type="button" className={view === "active" ? styles.selected : undefined} onClick={() => setView("active")}>进行中</button>
        <button type="button" className={view === "history" ? styles.selected : undefined} onClick={() => setView("history")}>历史记录</button>
      </div>
      {services.isPending && <SupportLoading label="正在读取服务进度…" />}
      {services.isError && <SupportError />}
      {!services.isPending && !services.isError && items.length === 0 && <SupportEmpty title={view === "active" ? "暂无进行中的服务" : "暂无历史服务"} message="通过订单详情发起的退款或复杂售后会出现在这里。" />}
      <section className={styles.serviceList} aria-label="客户服务记录">
        {items.map((service) => (
          <Link to={`/support/services/${service.service_id}`} key={service.service_id}>
            <ProductThumbnail
              src={service.product_preview?.image_url}
              alt={service.product_preview?.image_alt ?? service.title}
              size="medium"
            />
            <div><h2>{customerFacingText(service.title)}</h2><p>{customerFacingText(service.next_action ?? "查看最终处理结果")}</p><small>{service.order_id ?? "未关联订单"} · {formatDate(service.updated_at)}</small></div>
            <div className={styles.serviceMeta}>
              <span className={styles.serviceKind}>{service.kind === "refund" ? "退款" : "复杂售后"}</span>
              <StatusBadge label={serviceStatusLabel(service.status)} tone={statusTone(service.status)} />
            </div>
          </Link>
        ))}
      </section>
      {services.hasNextPage && <button className={styles.loadMore} type="button" disabled={services.isFetchingNextPage} onClick={() => void services.fetchNextPage()}>{services.isFetchingNextPage ? "正在加载…" : "加载更多服务"}</button>}
    </main>
  );
}
