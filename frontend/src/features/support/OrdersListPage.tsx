import { useInfiniteQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { listSupportOrders } from "../../api/client";
import { OrderSummaryCard } from "./OrderSummaryCard";
import { SupportEmpty, SupportError, SupportLoading } from "./SupportStates";
import styles from "./Support.module.css";

type OrderView =
  | "all"
  | "processing"
  | "shipping"
  | "delivered"
  | "after_sales";

const views: { value: OrderView; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "processing", label: "待发货" },
  { value: "shipping", label: "待收货" },
  { value: "delivered", label: "已完成" },
  { value: "after_sales", label: "售后中" },
];

/** 渲染支持服务端搜索、筛选与稳定游标分页的客户订单列表。 */
export function OrdersListPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const rawView = params.get("view");
  const view = views.some((item) => item.value === rawView)
    ? (rawView as OrderView)
    : "all";
  const [searchText, setSearchText] = useState(q);
  const orders = useInfiniteQuery({
    queryKey: ["support", "orders", q, view],
    queryFn: ({ pageParam }) =>
      listSupportOrders({ cursor: pageParam, q, view }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  const items = orders.data?.pages.flatMap((page) => page.orders) ?? [];

  /** 提交订单号或商品名称查询，并重置服务端游标。 */
  function handleSearch(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const next = new URLSearchParams(params);
    const normalized = searchText.trim();
    if (normalized) next.set("q", normalized);
    else next.delete("q");
    setParams(next);
  }

  /** 切换客户状态视图，并保留当前搜索条件。 */
  function selectView(nextView: OrderView): void {
    const next = new URLSearchParams(params);
    if (nextView === "all") next.delete("view");
    else next.set("view", nextView);
    setParams(next);
  }

  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}>
        <div>
          <span className={styles.eyebrow}>订单与履约</span>
          <h1>我的订单</h1>
        </div>
        <p>搜索商品或订单号，查看配送进度、交易信息和售后状态。</p>
      </header>

      <section className={styles.orderToolbar} aria-label="订单搜索和筛选">
        <form className={styles.orderSearch} onSubmit={handleSearch}>
          <label className="sr-only" htmlFor="support-order-search">
            搜索订单号或商品名称
          </label>
          <input
            id="support-order-search"
            value={searchText}
            placeholder="搜索订单号或商品名称"
            onChange={(event) => setSearchText(event.target.value)}
          />
          <button type="submit">搜索</button>
        </form>
        <div className={styles.segmented} aria-label="订单状态">
          {views.map((item) => (
            <button
              type="button"
              className={view === item.value ? styles.selected : undefined}
              aria-pressed={view === item.value}
              key={item.value}
              onClick={() => selectView(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>

      {orders.isPending && <SupportLoading label="正在读取订单…" />}
      {orders.isError && <SupportError />}
      {!orders.isPending && !orders.isError && items.length === 0 && (
        <SupportEmpty
          title="没有找到符合条件的订单"
          message="可以清除搜索条件或切换订单状态后再试。"
        />
      )}
      <section className={styles.commercialOrderGrid} aria-label="客户订单列表">
        {items.map((order) => (
          <OrderSummaryCard order={order} key={order.order_id} />
        ))}
      </section>
      {orders.hasNextPage && (
        <button
          className={styles.loadMore}
          type="button"
          disabled={orders.isFetchingNextPage}
          onClick={() => void orders.fetchNextPage()}
        >
          {orders.isFetchingNextPage ? "正在加载…" : "加载更多订单"}
        </button>
      )}
    </main>
  );
}
