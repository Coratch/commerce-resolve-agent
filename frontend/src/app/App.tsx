import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";

import { logout } from "../api/client";
import { LoginPage } from "../features/auth/LoginPage";
import { RegisterPage } from "../features/auth/RegisterPage";
import { ChatPage } from "../features/chat/ChatPage";
import { MemoriesPage } from "../features/memories/MemoriesPage";
import { OrdersPage } from "../features/orders/OrdersPage";
import { sessionQueryKey, useSessionQuery } from "./session";
import styles from "./App.module.css";

/** 渲染全局导航、当前身份和版本内页面路由。 */
function AppShell() {
  const session = useSessionQuery();
  const queryClient = useQueryClient();
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: async (value) => {
      queryClient.setQueryData(sessionQueryKey, value);
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  /** 请求退出，并由服务端返回新的游客 Session。 */
  function handleLogout(): void {
    logoutMutation.mutate();
  }

  if (session.isPending) {
    return <main className={styles.center}>正在建立安全会话…</main>;
  }
  if (session.isError || session.data === undefined) {
    return <main className={styles.center}>无法连接 CommerceResolve 服务。</main>;
  }

  const registered = session.data.mode === "registered";
  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <NavLink className={styles.brand} to="/chat">
          <span className={styles.logo}>CR</span>
          <span>
            <strong>CommerceResolve</strong>
            <small>可审计的电商售后 Agent</small>
          </span>
        </NavLink>
        <nav className={styles.nav} aria-label="主导航">
          <NavLink to="/chat">对话</NavLink>
          {registered && <NavLink to="/orders">我的订单</NavLink>}
          {registered && <NavLink to="/memories">长期偏好</NavLink>}
          {!registered && <NavLink to="/register">邀请注册</NavLink>}
          {!registered && <NavLink to="/login">登录</NavLink>}
        </nav>
        <div className={styles.identity}>
          <span>{registered ? session.data.username : "游客演示"}</span>
          {registered && (
            <button
              type="button"
              className={styles.textButton}
              onClick={handleLogout}
              disabled={logoutMutation.isPending}
            >
              退出
            </button>
          )}
        </div>
      </header>
      <Routes>
        <Route path="/" element={<ChatPage session={session.data} />} />
        <Route path="/chat" element={<ChatPage session={session.data} />} />
        <Route path="/chat/:threadId" element={<ChatPage session={session.data} />} />
        <Route path="/register" element={<RegisterPage session={session.data} />} />
        <Route path="/login" element={<LoginPage session={session.data} />} />
        <Route path="/orders" element={<OrdersPage session={session.data} />} />
        <Route path="/memories" element={<MemoriesPage session={session.data} />} />
      </Routes>
    </div>
  );
}

/** 提供浏览器路由并渲染 CommerceResolve 单页应用。 */
export function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
