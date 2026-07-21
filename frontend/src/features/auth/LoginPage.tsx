import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { ApiError, login } from "../../api/client";
import type { SessionResponse } from "../../api/types";
import { sessionQueryKey } from "../../app/session";
import styles from "./Auth.module.css";

interface LoginPageProps {
  session: SessionResponse;
}

/** 提供服务端 Session 登录表单，成功后替换可信身份缓存。 */
export function LoginPage({ session }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: login,
    onSuccess: (value) => {
      queryClient.setQueryData(sessionQueryKey, value);
      navigate("/orders");
    },
  });

  /** 提交最小账号凭据，不向服务端发送身份或模型字段。 */
  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    mutation.mutate({ username, password });
  }

  if (session.mode === "registered") {
    return <Navigate to="/orders" replace />;
  }
  return (
    <main className={styles.page}>
      <section className={styles.card}>
        <h1>登录私有工作区</h1>
        <p>登录后可以管理自己的演示订单，并使用服务端授权的 LLM。</p>
        <form className={styles.form} onSubmit={handleSubmit}>
          <label>
            用户名
            <input
              name="username"
              value={username}
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label>
            密码
            <input
              name="password"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {mutation.error instanceof ApiError && (
            <div className={styles.error} role="alert">
              {mutation.error.message}
            </div>
          )}
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "正在登录…" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
