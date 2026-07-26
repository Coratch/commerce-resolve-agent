import {
  Activity,
  BarChart3,
  Database,
  Home,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Orbit,
  Radio,
  ServerCog,
} from "lucide-react";
import { Navigate, NavLink, Outlet } from "react-router-dom";

import type { SessionResponse } from "../api/types";
import { InteractiveField } from "../components/InteractiveField";
import styles from "../features/admin/Admin.module.css";

interface AdminLayoutProps {
  session: SessionResponse;
  logoutPending: boolean;
  onLogout: () => void;
}

const navigation = [
  {
    label: "总览",
    items: [{ to: "/admin", label: "运营概览", icon: LayoutDashboard }],
  },
  {
    label: "工作区",
    items: [
      { to: "/admin/data", label: "演示工作区", icon: Database },
      { to: "/admin/invitations", label: "邀请与账号", icon: KeyRound },
    ],
  },
  {
    label: "质量与诊断",
    items: [
      { to: "/admin/runs", label: "运行监控", icon: Activity },
      { to: "/admin/eval", label: "质量评估", icon: BarChart3 },
      { to: "/admin/system", label: "系统状态", icon: ServerCog },
    ],
  },
] as const;

/** 渲染与客户售后中心分离、但共享同一 Session 的运营控制台外壳。 */
export function AdminLayout({ session, logoutPending, onLogout }: AdminLayoutProps) {
  if (!session.capabilities.can_access_admin) {
    return <Navigate replace to="/support" />;
  }
  return (
    <div className={styles.adminShell}>
      <InteractiveField variant="admin" />
      <aside className={styles.sidebar}>
        <NavLink className={styles.adminBrand} to="/admin">
          <span>
            <Orbit aria-hidden="true" size={22} strokeWidth={1.7} />
          </span>
          <div>
            <strong>运营控制台</strong>
            <small>CommerceResolve / Ops</small>
          </div>
        </NavLink>
        <nav className={styles.adminNav} aria-label="运营控制台导航">
          {navigation.map((group) => (
            <section className={styles.navGroup} key={group.label}>
              <span>{group.label}</span>
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} end={to === "/admin"}>
                  <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
                  {label}
                </NavLink>
              ))}
            </section>
          ))}
        </nav>
        <div className={styles.sidebarFooter}>
          <span className={styles.environmentLabel}>
            <Radio aria-hidden="true" size={13} />
            本地演示环境
          </span>
          <strong>{session.username}</strong>
          <NavLink to="/support">
            <Home aria-hidden="true" size={14} />
            返回客户售后中心
          </NavLink>
          <button type="button" onClick={onLogout} disabled={logoutPending}>
            <LogOut aria-hidden="true" size={14} />
            退出
          </button>
        </div>
      </aside>
      <section className={styles.adminContent}>
        <Outlet />
      </section>
    </div>
  );
}
