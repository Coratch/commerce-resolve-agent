import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Navigate, Link } from "react-router-dom";

import { ApiError, register } from "../../api/client";
import type { SessionResponse } from "../../api/types";
import styles from "./Auth.module.css";

interface RegisterPageProps {
  session: SessionResponse;
}

/** 提供邀请码注册表单，并明确注册后仍需账号登录。 */
export function RegisterPage({ session }: RegisterPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invitationCode, setInvitationCode] = useState("");
  const mutation = useMutation({ mutationFn: register });

  /** 提交邀请码和新账号凭据，不在浏览器持久化敏感字段。 */
  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    mutation.mutate({
      username,
      password,
      invitation_code: invitationCode,
    });
  }

  if (session.mode === "registered") {
    return <Navigate to="/orders" replace />;
  }
  return (
    <main className={styles.page}>
      <section className={styles.card}>
        <h1>使用邀请码注册</h1>
        <p>账号拥有独立演示工作区；邀请码只用于注册，不作为登录凭证。</p>
        {mutation.isSuccess ? (
          <div className={styles.success}>
            注册成功，请前往 <Link to="/login">登录</Link>。
          </div>
        ) : (
          <form className={styles.form} onSubmit={handleSubmit}>
            <label>
              用户名
              <input
                name="username"
                value={username}
                autoComplete="username"
                minLength={3}
                maxLength={32}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </label>
            <label>
              密码（至少 12 个字符）
              <input
                name="password"
                type="password"
                value={password}
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
            <label>
              邀请码
              <input
                name="invitation_code"
                type="password"
                value={invitationCode}
                autoComplete="off"
                onChange={(event) => setInvitationCode(event.target.value)}
                required
              />
            </label>
            {mutation.error instanceof ApiError && (
              <div className={styles.error} role="alert">
                {mutation.error.message}
              </div>
            )}
            <button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "正在创建账号…" : "创建账号"}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
