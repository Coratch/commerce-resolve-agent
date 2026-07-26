import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Boxes,
  ClipboardList,
  LogOut,
  MemoryStick,
  Orbit,
  Radio,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useEffect } from "react";

import { logout } from "../api/client";
import type { SessionResponse } from "../api/types";
import { InteractiveField } from "../components/InteractiveField";
import { LoginPage } from "../features/auth/LoginPage";
import {
  AdminDataPage,
  AdminEvalPage,
  AdminInvitationsPage,
  AdminOverviewPage,
  AdminRunDetailPage,
  AdminRunsPage,
  AdminSystemPage,
} from "../features/admin/AdminPages";
import { RegisterPage } from "../features/auth/RegisterPage";
import { LandingPage } from "../features/landing/LandingPage";
import { MemoriesPage } from "../features/memories/MemoriesPage";
import { AgentDrawerProvider } from "../features/support/AgentDrawer";
import { DemoSettingsPage } from "../features/support/DemoSettingsPage";
import { OrderDetailPage } from "../features/support/OrderDetailPage";
import { OrdersListPage } from "../features/support/OrdersListPage";
import { ServiceDetailPage } from "../features/support/ServiceDetailPage";
import { ServicesPage } from "../features/support/ServicesPage";
import { SupportHomePage } from "../features/support/SupportHomePage";
import { AdminLayout } from "./AdminLayout";
import { sessionQueryKey, useSessionQuery } from "./session";
import styles from "./App.module.css";

interface CustomerLayoutProps {
  session: SessionResponse;
  logoutPending: boolean;
  onLogout: () => void;
}

const customerNavigation = [
  { to: "/support", label: "售后首页", icon: Orbit },
  { to: "/support/orders", label: "我的订单", icon: Boxes },
  { to: "/support/services", label: "服务进度", icon: ClipboardList },
] as const;

/** 在主路由切换后回到页面顶部，避免上一页滚动位置遮挡新页面标题。 */
function ScrollToTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [pathname]);

  return null;
}

/** 渲染客户售后中心导航，并与运营控制台保持独立信息架构。 */
function CustomerLayout({ session, logoutPending, onLogout }: CustomerLayoutProps) {
  return (
    <div className={styles.app}>
      <InteractiveField />
      <header className={styles.header}>
        <NavLink className={styles.brand} to="/support">
          <span className={styles.logo}>
            <Orbit aria-hidden="true" strokeWidth={1.8} />
          </span>
          <span>
            <strong>CommerceResolve</strong>
            <small>After-sales intelligence</small>
          </span>
        </NavLink>
        <nav className={styles.nav} aria-label="主导航">
          {customerNavigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to}>
              <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className={styles.identity}>
          <span className={styles.demoBadge}>
            <Radio aria-hidden="true" size={14} />
            本地演示
          </span>
          <div className={styles.accountLinks}>
            <NavLink to="/support/memories">
              <MemoryStick aria-hidden="true" size={14} />
              长期偏好
            </NavLink>
            <NavLink to="/support/settings">
              <Settings2 aria-hidden="true" size={14} />
              演示设置
            </NavLink>
            {session.capabilities.can_access_admin && (
              <NavLink to="/admin">
                <ShieldCheck aria-hidden="true" size={14} />
                运营控制台
              </NavLink>
            )}
          </div>
          <span className={styles.accountName}>
            <Bot aria-hidden="true" size={14} />
            {session.username}
          </span>
          <button
            type="button"
            className={styles.textButton}
            onClick={onLogout}
            disabled={logoutPending}
            aria-label="退出当前账号"
          >
            <LogOut aria-hidden="true" size={15} />
            <span>退出</span>
          </button>
        </div>
      </header>
      <AgentDrawerProvider session={session}>
        <Outlet />
      </AgentDrawerProvider>
    </div>
  );
}

/** 加载可信 Session，并为客户与管理员路由提供共享退出行为。 */
function AppShell() {
  const session = useSessionQuery();
  const queryClient = useQueryClient();
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: async (value) => {
      queryClient.setQueryData(sessionQueryKey, value);
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== "session",
      });
    },
  });

  /** 请求退出，并由服务端返回不含业务能力的匿名状态。 */
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
    <Routes>
      <Route path="/" element={<LandingPage session={session.data} />} />
      <Route path="/register" element={<RegisterPage session={session.data} />} />
      <Route path="/login" element={<LoginPage session={session.data} />} />
      <Route
        element={
          registered ? (
            <CustomerLayout
              session={session.data}
              logoutPending={logoutMutation.isPending}
              onLogout={handleLogout}
            />
          ) : (
            <Navigate replace to="/login" />
          )
        }
      >
        <Route path="/support" element={<SupportHomePage session={session.data} />} />
        <Route path="/support/orders" element={<OrdersListPage />} />
        <Route path="/support/orders/:orderId" element={<OrderDetailPage />} />
        <Route path="/support/services" element={<ServicesPage />} />
        <Route path="/support/services/:serviceId" element={<ServiceDetailPage />} />
        <Route path="/support/memories" element={<MemoriesPage session={session.data} />} />
        <Route path="/support/settings" element={<DemoSettingsPage />} />
        <Route path="/orders/*" element={<Navigate replace to="/support/orders" />} />
        <Route path="/services/*" element={<Navigate replace to="/support/services" />} />
        <Route path="/chat/*" element={<Navigate replace to="/support" />} />
        <Route path="/memories" element={<Navigate replace to="/support/memories" />} />
      </Route>
      <Route
        path="/admin"
        element={
          registered ? (
            <AdminLayout
              session={session.data}
              logoutPending={logoutMutation.isPending}
              onLogout={handleLogout}
            />
          ) : (
            <Navigate replace to="/login" />
          )
        }
      >
        <Route index element={<AdminOverviewPage />} />
        <Route path="data" element={<AdminDataPage />} />
        <Route path="invitations" element={<AdminInvitationsPage />} />
        <Route path="runs" element={<AdminRunsPage />} />
        <Route path="runs/:runId" element={<AdminRunDetailPage />} />
        <Route path="eval" element={<AdminEvalPage />} />
        <Route path="system" element={<AdminSystemPage />} />
      </Route>
      <Route path="*" element={<Navigate replace to="/" />} />
    </Routes>
  );
}

/** 提供浏览器路由并渲染 CommerceResolve 单页应用。 */
export function App() {
  return (
    <BrowserRouter>
      <ScrollToTop />
      <AppShell />
    </BrowserRouter>
  );
}
