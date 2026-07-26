import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronRight, MessageCircleMore, X } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import {
  ApiError,
  createConversation,
  listSupportOrders,
} from "../../api/client";
import type { SessionResponse } from "../../api/types";
import { ConversationPanel } from "../chat/ConversationPanel";
import { ProductThumbnail } from "./ProductThumbnail";
import styles from "./AgentDrawer.module.css";

interface AgentDrawerContextValue {
  open: () => void;
  openForOrder: (orderId: string, label?: string) => Promise<void>;
}

interface AgentDrawerProviderProps {
  session: SessionResponse;
  children: ReactNode;
}

const AgentDrawerContext = createContext<AgentDrawerContextValue | null>(null);

/** 生成订单绑定的助手标题，避免在多个入口重复拼接文案。 */
function orderContextLabel(orderId: string, label?: string): string {
  return label?.trim() || `订单 ${orderId}`;
}

/** 提供跨客户路由持续存在的订单 Agent 抽屉与受控打开能力。 */
export function AgentDrawerProvider({
  session,
  children,
}: AgentDrawerProviderProps) {
  const [visible, setVisible] = useState(false);
  const [activeOrderId, setActiveOrderId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [contextLabel, setContextLabel] = useState("选择订单后开始售后任务");
  const [creating, setCreating] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const orders = useQuery({
    queryKey: ["support", "agent-order-picker"],
    queryFn: () => listSupportOrders({ view: "all" }),
    enabled: visible && activeOrderId === null,
  });

  /** 打开全局入口，但不在用户选定订单前创建空会话。 */
  const open = useCallback((): void => {
    setProblem(null);
    setVisible(true);
  }, []);

  /** 创建或恢复指定订单的唯一活动任务，并保持抽屉跨路由挂载。 */
  const openForOrder = useCallback(
    async (orderId: string, label?: string): Promise<void> => {
      setVisible(true);
      setProblem(null);
      setActiveOrderId(orderId);
      setContextLabel(orderContextLabel(orderId, label));
      setCreating(true);
      try {
        const conversation = await createConversation(orderId);
        setActiveThreadId(conversation.thread_id);
      } catch (error) {
        setActiveThreadId(null);
        setProblem(
          error instanceof ApiError
            ? error.message
            : "当前订单的智能售后暂时无法打开，请稍后重试。",
        );
      } finally {
        setCreating(false);
      }
    },
    [],
  );

  /** 返回订单选择界面，不结束服务端任务或清理历史。 */
  function chooseAnotherOrder(): void {
    setActiveOrderId(null);
    setActiveThreadId(null);
    setContextLabel("选择订单后开始售后任务");
    setProblem(null);
  }

  /** 关闭视觉抽屉，保留当前订单和 Thread 以便继续。 */
  function close(): void {
    setVisible(false);
  }

  const contextValue = useMemo(
    () => ({ open, openForOrder }),
    [open, openForOrder],
  );

  return (
    <AgentDrawerContext.Provider value={contextValue}>
      {children}
      <button
        type="button"
        className={styles.floatingTrigger}
        aria-label="打开智能售后助手"
        aria-expanded={visible}
        onClick={open}
      >
        <MessageCircleMore aria-hidden="true" size={20} />
        <span>智能售后</span>
      </button>
      {visible && (
        <div className={styles.layer}>
          <button
            type="button"
            className={styles.backdrop}
            aria-label="关闭智能售后助手"
            onClick={close}
          />
          <aside className={styles.drawer} aria-label="智能售后助手">
            <header className={styles.drawerHeader}>
              <div>
                <span className={styles.mark}>
                  <Bot aria-hidden="true" size={17} />
                </span>
                <div>
                  <strong>CommerceResolve Agent</strong>
                  <small>{contextLabel}</small>
                </div>
              </div>
              <div className={styles.headerActions}>
                {activeOrderId !== null && (
                  <button type="button" onClick={chooseAnotherOrder}>
                    切换订单
                  </button>
                )}
                <button type="button" aria-label="关闭助手" onClick={close}>
                  <X aria-hidden="true" size={18} />
                </button>
              </div>
            </header>
            {creating && (
              <div className={styles.centerState}>正在恢复订单任务…</div>
            )}
            {!creating && problem !== null && (
              <div className={styles.centerState}>
                <p>{problem}</p>
                {activeOrderId !== null && (
                  <button
                    type="button"
                    onClick={() => void openForOrder(activeOrderId)}
                  >
                    重试
                  </button>
                )}
              </div>
            )}
            {!creating && problem === null && activeThreadId !== null && (
              <ConversationPanel
                threadId={activeThreadId}
                session={session}
                contextLabel={contextLabel}
              />
            )}
            {!creating && problem === null && activeOrderId === null && (
              <section className={styles.orderPicker}>
                <span>建立订单任务</span>
                <h2>这次需要处理哪笔订单？</h2>
                <p>选择后才会创建或恢复对应 Thread，不会产生空会话。</p>
                {orders.isPending && <div>正在读取订单…</div>}
                {orders.isError && (
                  <div>订单暂时无法读取，请稍后重新打开。</div>
                )}
                <div className={styles.orderList}>
                  {orders.data?.orders.map((order) => (
                    <button
                      type="button"
                      key={order.order_id}
                      onClick={() =>
                        void openForOrder(
                          order.order_id,
                          `${order.item_title_preview ?? "订单"} · ${order.order_id}`,
                        )
                      }
                    >
                      <ProductThumbnail
                        src={order.preview_items?.[0]?.image_url}
                        alt={
                          order.preview_items?.[0]?.image_alt ??
                          order.item_title_preview ??
                          "订单商品"
                        }
                        size="small"
                      />
                      <span>
                        <strong>{order.item_title_preview ?? "订单商品"}</strong>
                        <small>{order.order_id}</small>
                      </span>
                      <ChevronRight aria-hidden="true" size={17} />
                    </button>
                  ))}
                </div>
              </section>
            )}
          </aside>
        </div>
      )}
    </AgentDrawerContext.Provider>
  );
}

/** 读取全局 Agent 抽屉控制器，只允许在客户布局内部使用。 */
export function useAgentDrawer(): AgentDrawerContextValue {
  const value = useContext(AgentDrawerContext);
  if (value === null) {
    throw new Error("useAgentDrawer 必须在 AgentDrawerProvider 内使用");
  }
  return value;
}
