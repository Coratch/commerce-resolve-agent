import {
  ArrowRight,
  CheckCircle2,
  GitBranch,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { Link, Navigate } from "react-router-dom";

import type { SessionResponse } from "../../api/types";
import styles from "./LandingPage.module.css";

/** 渲染未登录访客可访问的产品说明与邀请制入口。 */
export function LandingPage({ session }: { session: SessionResponse }) {
  if (session.mode === "registered") {
    return <Navigate replace to="/support" />;
  }
  return (
    <main className={styles.page}>
      <header className={styles.topbar}>
        <Link className={styles.brand} to="/">
          <span>CR</span>
          <strong>CommerceResolve</strong>
        </Link>
        <nav aria-label="公开导航">
          <a href="#workflow">工作流</a>
          <a href="#evidence">工程证据</a>
          <Link to="/login">登录</Link>
          <Link className={styles.inviteLink} to="/register">
            邀请注册
          </Link>
        </nav>
      </header>
      <section className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>LANGGRAPH / AFTER-SALES AGENT</span>
          <h1>
            售后问题，
            <em>由证据驱动解决。</em>
          </h1>
          <p>
            从订单与物流事实出发，经过政策检索、确定性退款规则、客户确认和幂等
            Mock 执行，形成可恢复、可审计、可评估的 Agent 完整链路。
          </p>
          <div className={styles.actions}>
            <Link to="/register">
              使用邀请码注册
              <ArrowRight aria-hidden="true" size={18} />
            </Link>
            <a
              href="https://github.com/Coratch/commerce-resolve-agent"
              rel="noreferrer"
              target="_blank"
            >
              查看开源部署
            </a>
          </div>
        </div>
        <aside className={styles.proofCard}>
          <span>旗舰任务 / 物流延迟退款</span>
          <ol>
            <li><CheckCircle2 size={16} />订单与物流事实</li>
            <li><CheckCircle2 size={16} />政策 RAG 证据</li>
            <li><ShieldCheck size={16} />确定性资格与金额</li>
            <li><RotateCcw size={16} />interrupt 与幂等恢复</li>
          </ol>
          <strong>0 个真实资金副作用</strong>
        </aside>
      </section>
      <section className={styles.workflow} id="workflow">
        <span>01 / Product flow</span>
        <h2>不是泛聊天，而是一条订单任务。</h2>
        <div>
          {[
            ["01", "选择订单", "Thread 只绑定一个用户、工作区与订单。"],
            ["02", "提出诉求", "模型只理解意图并选择候选能力。"],
            ["03", "校验与确认", "Policy 决定资格，客户确认执行意愿。"],
            ["04", "执行与回读", "Mock Executor 幂等写入，再查询真实结果。"],
          ].map(([index, title, body]) => (
            <article key={index}>
              <span>{index}</span>
              <h3>{title}</h3>
              <p>{body}</p>
            </article>
          ))}
        </div>
      </section>
      <section className={styles.evidence} id="evidence">
        <GitBranch aria-hidden="true" size={34} />
        <div>
          <span>02 / Interview-ready engineering</span>
          <h2>LangGraph、Memory、RAG、Agent Loop 与 Eval 各有明确职责。</h2>
        </div>
        <p>
          订单、物流和金额来自确定性业务存储；RAG 只提供政策依据；Memory
          只保存用户明确批准的低风险偏好；复杂任务进入只读、有界的 AI 深度处理 Loop。
        </p>
      </section>
    </main>
  );
}
