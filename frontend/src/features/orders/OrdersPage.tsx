import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";

import {
  ApiError,
  createOrder,
  deleteOrder,
  listOrders,
  updateOrder,
  upsertMockPayment,
} from "../../api/client";
import type {
  OrderStatus,
  PaymentChannel,
  PaymentStatus,
  PublicOrder,
  SessionResponse,
  ShipmentStatus,
} from "../../api/types";
import styles from "./OrdersPage.module.css";

interface OrdersPageProps {
  session: SessionResponse;
}

interface OrderEditorProps {
  order: PublicOrder;
}

const orderStatusLabels: Record<OrderStatus, string> = {
  processing: "处理中",
  shipped: "已发货",
  delivered: "已送达",
  cancelled: "已取消",
};

const shipmentStatusLabels: Record<ShipmentStatus, string> = {
  preparing: "待揽收",
  in_transit: "运输中",
  delivered: "已签收",
};

const paymentStatusLabels: Record<PaymentStatus, string> = {
  pending: "待结算",
  settled: "已结算",
  failed: "支付失败",
};

const paymentChannelLabels: Record<PaymentChannel, string> = {
  mock_card: "Mock 银行卡",
  mock_wallet: "Mock 钱包",
};

/** 渲染单条订单的确定性编辑和删除控件。 */
function OrderEditor({ order }: OrderEditorProps) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<OrderStatus>(order.status);
  const [shipmentStatus, setShipmentStatus] = useState<ShipmentStatus>(
    order.shipment?.status ?? "preparing",
  );
  const [lastEvent, setLastEvent] = useState(order.shipment?.last_event ?? "等待揽收");
  const [paymentAmount, setPaymentAmount] = useState(order.payment?.amount ?? "129.90");
  const [paymentChannel, setPaymentChannel] = useState<PaymentChannel>(
    order.payment?.channel ?? "mock_card",
  );
  const [paymentStatus, setPaymentStatus] = useState<PaymentStatus>(
    order.payment?.status === "refunded" ? "settled" : order.payment?.status ?? "settled",
  );
  const updateMutation = useMutation({
    mutationFn: () =>
      updateOrder(order.order_id, {
        status,
        shipment: { status: shipmentStatus, last_event: lastEvent },
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteOrder(order.order_id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });
  const paymentMutation = useMutation({
    mutationFn: () =>
      upsertMockPayment(order.order_id, {
        amount: paymentAmount,
        currency: "CNY",
        channel: paymentChannel,
        status: paymentStatus,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  /** 二次确认后删除当前订单，不提供批量或 Agent 写入。 */
  function handleDelete(): void {
    if (window.confirm(`确认删除订单 ${order.order_id} 及其物流吗？`)) {
      deleteMutation.mutate();
    }
  }

  return (
    <article className={styles.orderCard}>
      <div className={styles.orderTitle}>
        <div>
          <span>订单</span>
          <strong>{order.order_id}</strong>
        </div>
        <span className={styles.badge}>{orderStatusLabels[order.status]}</span>
      </div>
      <div className={styles.editorGrid}>
        <label>
          订单状态
          <select value={status} onChange={(event) => setStatus(event.target.value as OrderStatus)}>
            {Object.entries(orderStatusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          物流状态
          <select
            value={shipmentStatus}
            onChange={(event) => setShipmentStatus(event.target.value as ShipmentStatus)}
          >
            {Object.entries(shipmentStatusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.wide}>
          最近物流事件
          <input
            value={lastEvent}
            maxLength={300}
            onChange={(event) => setLastEvent(event.target.value)}
          />
        </label>
      </div>
      <section className={styles.paymentSection}>
        <h3>Mock 支付</h3>
        <p>
          {order.payment
            ? `当前：¥${order.payment.amount} · ${order.payment.channel} · ${order.payment.status}`
            : "尚未配置退款所需的 Mock 支付事实。"}
        </p>
        <div className={styles.editorGrid}>
          <label>
            支付金额
            <input
              value={paymentAmount}
              inputMode="decimal"
              pattern="(0|[1-9][0-9]{0,9})\.[0-9]{2}"
              onChange={(event) => setPaymentAmount(event.target.value)}
            />
          </label>
          <label>
            原支付渠道
            <select
              value={paymentChannel}
              onChange={(event) => setPaymentChannel(event.target.value as PaymentChannel)}
            >
              {Object.entries(paymentChannelLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            支付状态
            <select
              value={paymentStatus}
              onChange={(event) => setPaymentStatus(event.target.value as PaymentStatus)}
            >
              {Object.entries(paymentStatusLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => paymentMutation.mutate()}
            disabled={paymentMutation.isPending}
          >
            保存 Mock 支付
          </button>
        </div>
        {order.refunds.length > 0 && (
          <ul className={styles.refundList}>
            {order.refunds.map((refund) => (
              <li key={refund.refund_id}>
                {refund.refund_id} · ¥{refund.amount} · {refund.status}
              </li>
            ))}
          </ul>
        )}
      </section>
      {(updateMutation.error instanceof ApiError ||
        deleteMutation.error instanceof ApiError ||
        paymentMutation.error instanceof ApiError) && (
        <p className={styles.error} role="alert">
          {(updateMutation.error ?? deleteMutation.error ?? paymentMutation.error)?.message}
        </p>
      )}
      <div className={styles.actions}>
        <button
          type="button"
          onClick={() => updateMutation.mutate()}
          disabled={updateMutation.isPending || lastEvent.trim() === ""}
        >
          保存修改
        </button>
        <button type="button" className={styles.danger} onClick={handleDelete}>
          删除
        </button>
      </div>
    </article>
  );
}

/** 提供注册用户私有订单与物流的列表和创建表单。 */
export function OrdersPage({ session }: OrdersPageProps) {
  const queryClient = useQueryClient();
  const orders = useQuery({
    queryKey: ["orders"],
    queryFn: listOrders,
    enabled: session.mode === "registered",
  });
  const [orderId, setOrderId] = useState("");
  const [lastEvent, setLastEvent] = useState("等待揽收");
  const createMutation = useMutation({
    mutationFn: createOrder,
    onSuccess: async () => {
      setOrderId("");
      setLastEvent("等待揽收");
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  /** 创建一条处理中订单和待揽收物流作为可查询演示事实。 */
  function handleCreate(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    createMutation.mutate({
      order_id: orderId.trim().toUpperCase(),
      status: "processing",
      shipment: { status: "preparing", last_event: lastEvent.trim() },
    });
  }

  if (session.mode !== "registered") {
    return <Navigate to="/login" replace />;
  }
  return (
    <main className={styles.page}>
      <section className={styles.heading}>
        <span>PRIVATE DEMO WORKSPACE</span>
        <h1>订单与物流数据</h1>
        <p>这里维护的是当前账号隔离的演示事实；Agent 只能读取，不能通过自然语言修改。</p>
      </section>
      <section className={styles.createCard}>
        <h2>新建演示订单</h2>
        <form onSubmit={handleCreate}>
          <label>
            订单号
            <input
              value={orderId}
              placeholder="ORD-DEMO-001"
              pattern="ORD-[A-Za-z0-9-]{3,32}"
              onChange={(event) => setOrderId(event.target.value)}
              required
            />
          </label>
          <label>
            初始物流事件
            <input
              value={lastEvent}
              maxLength={300}
              onChange={(event) => setLastEvent(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={createMutation.isPending}>
            {createMutation.isPending ? "正在创建…" : "创建订单"}
          </button>
        </form>
        {createMutation.error instanceof ApiError && (
          <p className={styles.error} role="alert">
            {createMutation.error.message}
          </p>
        )}
      </section>
      <section className={styles.list} aria-label="私有订单列表">
        {orders.isPending && <p>正在读取私有订单…</p>}
        {orders.isError && <p className={styles.error}>无法读取订单，请重新登录后重试。</p>}
        {orders.data?.length === 0 && <p className={styles.empty}>还没有演示订单。</p>}
        {orders.data?.map((order) => <OrderEditor key={order.order_id} order={order} />)}
      </section>
    </main>
  );
}
