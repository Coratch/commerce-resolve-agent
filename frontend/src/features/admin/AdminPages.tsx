import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiError,
  createAdminInvitation,
  getAdminEval,
  getAdminOverview,
  getAdminRun,
  getAdminSystem,
  listAdminAudit,
  listAdminCustomers,
  listAdminInvitations,
  listAdminRuns,
  resetAdminDemoWorkspace,
  revokeAdminInvitation,
} from "../../api/client";
import type {
  AdminEvalSnapshot,
  AdminInvitation,
  AdminSystemSnapshot,
} from "../../api/types";
import styles from "./Admin.module.css";

/** 生成后台工作区重置使用的单次幂等请求标识。 */
function newAdminRequestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `admin-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 把服务端时间转换为适合运营列表的本地短格式。 */
function formatTime(value?: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

/** 根据撤销、使用次数和有效期生成邀请码的真实可用状态。 */
function invitationStatusLabel(invitation: AdminInvitation): string {
  if (invitation.revoked) return "已撤销";
  if (invitation.used_count >= invitation.max_uses) return "已用尽";
  if (new Date(invitation.expires_at).getTime() <= Date.now()) return "已过期";
  return "有效";
}

/** 将请求失败转换为不暴露内部异常的产品状态。 */
function QueryProblem({ error }: { error: unknown }) {
  return (
    <p className={styles.problem} role="status">
      {error instanceof ApiError ? error.message : "当前数据暂不可用，请稍后重试。"}
    </p>
  );
}

/** 渲染运营页面统一标题和用途说明。 */
function PageHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <header className={styles.pageHeader}>
      <div>
        <span className={styles.eyebrow}>{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
    </header>
  );
}

/** 展示后台首页的权威计数、最近 Run、Eval 和系统状态。 */
export function AdminOverviewPage() {
  const overview = useQuery({ queryKey: ["admin", "overview"], queryFn: getAdminOverview });
  if (overview.isPending) return <main className={styles.page}>正在读取运营概览…</main>;
  if (overview.isError) return <main className={styles.page}><QueryProblem error={overview.error} /></main>;
  const countLabels: Record<string, string> = {
    customers: "注册账号",
    orders: "演示订单",
    active_runs: "活跃运行",
    active_cases: "进行中服务",
  };
  return (
    <main className={styles.page}>
      <PageHeader
        eyebrow="运营总览"
        title="Agent 运营控制台"
        description="集中查看业务工作区、Agent 运行质量与系统健康；页面数据均为只读或来自明确管理操作。"
      />
      <section className={styles.statsGrid} aria-label="运营计数">
        {Object.entries(overview.data.counts).map(([key, value]) => (
          <article className={styles.statCard} key={key}>
            <span>{countLabels[key] ?? key}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>
      <section className={styles.healthStrip} aria-label="质量与系统状态">
        <div>
          <span>质量评估</span>
          <strong>{evalStateMessage(overview.data.evaluation)}</strong>
          <small>安全违规 {overview.data.evaluation.safety_violation_count}</small>
        </div>
        <div>
          <span>系统就绪</span>
          <strong>{overview.data.system.ready ? "依赖正常" : "需要检查"}</strong>
          <small>应用版本 {overview.data.system.version}</small>
        </div>
        <div>
          <span>数据来源</span>
          <strong>本地权威存储</strong>
          <small>目录、业务库与评估产物彼此分离</small>
        </div>
      </section>
      <section className={styles.twoColumns}>
        <article className={styles.card}>
          <div className={styles.sectionTitle}>
            <h2>最近 Agent 运行</h2>
            <Link to="/admin/runs">查看全部</Link>
          </div>
          {overview.data.recent_runs.length === 0 ? (
            <p className={styles.empty}>暂无运行记录，客户完成一次智能咨询后会在这里显示。</p>
          ) : (
            <ul className={styles.eventList}>
              {overview.data.recent_runs.map((run) => (
                <li key={run.run_id}>
                  <Link to={`/admin/runs/${run.run_id}`}>{run.request_kind}</Link>
                  <span className={styles.muted}> · {run.status} · {formatTime(run.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </article>
        <article className={styles.card}>
          <div className={styles.sectionTitle}>
            <h2>常用运营入口</h2>
            <span>按职责分离</span>
          </div>
          <div className={styles.linkList}>
            <Link to="/admin/data"><span>准备演示数据</span><strong>选择客户与场景 <ArrowUpRight aria-hidden="true" size={14} /></strong></Link>
            <Link to="/admin/invitations"><span>管理邀请与账号</span><strong>查看状态 <ArrowUpRight aria-hidden="true" size={14} /></strong></Link>
            <Link to="/admin/eval"><span>查看质量评估</span><strong>读取结果 <ArrowUpRight aria-hidden="true" size={14} /></strong></Link>
            <Link to="/admin/system"><span>检查系统健康</span><strong>查看详情 <ArrowUpRight aria-hidden="true" size={14} /></strong></Link>
          </div>
        </article>
      </section>
    </main>
  );
}

/** 展示注册客户工作区，并只允许执行整区重置。 */
export function AdminDataPage() {
  const queryClient = useQueryClient();
  const customers = useQuery({ queryKey: ["admin", "customers"], queryFn: listAdminCustomers });
  const resetMutation = useMutation({
    mutationFn: ({ userId, username }: { userId: string; username: string }) =>
      resetAdminDemoWorkspace(userId, newAdminRequestId()).then((result) => ({
        ...result,
        username,
      })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admin", "customers"] });
    },
  });

  /** 二次确认后重置明确目标客户的完整演示工作区。 */
  function handleReset(userId: string, username: string): void {
    const accepted = window.confirm(
      `确认重置 ${username} 的完整演示工作区吗？退款、服务记录、会话、Checkpoint 和长期偏好将被清除。`,
    );
    if (accepted) resetMutation.mutate({ userId, username });
  }

  return (
    <main className={styles.page}>
      <PageHeader
        eyebrow="业务工作区"
        title="演示工作区"
        description="查看每个客户的版本化数据集和健康状态；只能整区重置，不能绕过 Agent 修改单笔业务事实。"
      />
      {customers.isError && <QueryProblem error={customers.error} />}
      <section className={styles.card}>
        <h2>客户数据集</h2>
        {customers.isPending && <p>正在读取工作区状态…</p>}
        {customers.data?.length === 0 && (
          <p className={styles.empty}>还没有通过邀请码注册的客户。</p>
        )}
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>客户</th>
                <th>Dataset</th>
                <th>状态</th>
                <th>订单</th>
                <th>重置代次</th>
                <th>初始化时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {customers.data?.map((item) => (
                <tr key={item.user_id}>
                  <td>{item.username}</td>
                  <td>{item.dataset_version ?? "—"}</td>
                  <td>
                    <span className={styles.status}>{item.dataset_status ?? "未初始化"}</span>
                  </td>
                  <td>{item.order_count}</td>
                  <td>{item.reset_generation}</td>
                  <td>{formatTime(item.initialized_at)}</td>
                  <td>
                    <button
                      className={styles.danger}
                      type="button"
                      disabled={resetMutation.isPending}
                      onClick={() => handleReset(item.user_id, item.username)}
                    >
                      重置工作区
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {resetMutation.data && (
          <p className={styles.notice}>
            {resetMutation.data.username} 的工作区已恢复为{" "}
            {resetMutation.data.dataset_version}，保留了{" "}
            {resetMutation.data.order_ids.length} 个公开订单号。
          </p>
        )}
        {resetMutation.error && <QueryProblem error={resetMutation.error} />}
      </section>
    </main>
  );
}

/** 管理一次性邀请码并明确展示明文只出现一次。 */
export function AdminInvitationsPage() {
  const queryClient = useQueryClient();
  const invitations = useQuery({ queryKey: ["admin", "invitations"], queryFn: listAdminInvitations });
  const [expires, setExpires] = useState(168);
  const [uses, setUses] = useState(1);
  const createMutation = useMutation({
    mutationFn: () => createAdminInvitation({ expires_in_hours: expires, max_uses: uses }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["admin", "invitations"] }),
  });
  const revokeMutation = useMutation({
    mutationFn: revokeAdminInvitation,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["admin", "invitations"] }),
  });
  return (
    <main className={styles.page}>
      <PageHeader eyebrow="访问管理" title="邀请与账号" description="邀请码明文只在创建结果中出现一次；管理员角色只能由可信本机命令授予。" />
      <section className={styles.card}>
        <div className={styles.formGrid}>
          <label>有效小时<input type="number" min={1} max={720} value={expires} onChange={(event) => setExpires(Number(event.target.value))} /></label>
          <label>最多使用次数<input type="number" min={1} max={100} value={uses} onChange={(event) => setUses(Number(event.target.value))} /></label>
          <button className={styles.primary} type="button" onClick={() => createMutation.mutate()}>创建邀请码</button>
        </div>
        {createMutation.data && <div className={styles.notice}><strong>请立即安全保存，本页刷新后无法再次读取：</strong><p className={styles.code}>{createMutation.data.code}</p></div>}
        {createMutation.error && <QueryProblem error={createMutation.error} />}
      </section>
      <section className={styles.card}>
        <h2>邀请码状态</h2>
        {invitations.isError && <QueryProblem error={invitations.error} />}
        <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>ID</th><th>使用</th><th>到期时间</th><th>状态</th><th>操作</th></tr></thead><tbody>{invitations.data?.map((item) => <tr key={item.invitation_id}><td>{item.invitation_id}</td><td>{item.used_count}/{item.max_uses}</td><td>{formatTime(item.expires_at)}</td><td>{invitationStatusLabel(item)}</td><td><button className={styles.danger} type="button" disabled={item.revoked || revokeMutation.isPending} onClick={() => revokeMutation.mutate(item.invitation_id)}>撤销</button></td></tr>)}</tbody></table></div>
      </section>
    </main>
  );
}

/** 按状态和类型筛选脱敏 Agent Run，并进入只读详情。 */
export function AdminRunsPage() {
  const [status, setStatus] = useState("");
  const [requestKind, setRequestKind] = useState("");
  const runs = useQuery({ queryKey: ["admin", "runs", status, requestKind], queryFn: () => listAdminRuns({ status, requestKind }) });
  return (
    <main className={styles.page}>
      <PageHeader eyebrow="质量与诊断" title="Agent 运行监控" description="查看脱敏生命周期、类型、停止状态和有限诊断；本页面不会执行或恢复任务。" />
      <section className={styles.card}>
        <div className={styles.toolbar}>
          <label>状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option><option value="accepted">accepted</option><option value="running">running</option><option value="waiting_action">waiting_action</option><option value="completed">completed</option><option value="failed">failed</option><option value="interrupted">interrupted</option></select></label>
          <label>请求类型<input value={requestKind} placeholder="chat_message" onChange={(event) => setRequestKind(event.target.value)} /></label>
        </div>
        {runs.isError && <QueryProblem error={runs.error} />}
        <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Run</th><th>类型</th><th>状态</th><th>待办/错误</th><th>开始</th><th>耗时</th></tr></thead><tbody>{runs.data?.map((run) => <tr key={run.run_id}><td><Link to={`/admin/runs/${run.run_id}`}>{run.run_id}</Link></td><td>{run.request_kind}</td><td><span className={styles.status}>{run.status}</span></td><td>{run.pending_action ?? run.public_error_code ?? "—"}</td><td>{formatTime(run.created_at)}</td><td>{run.duration_ms === null || run.duration_ms === undefined ? "—" : `${run.duration_ms} ms`}</td></tr>)}</tbody></table></div>
      </section>
    </main>
  );
}

/** 展示一条 Run 的事件白名单和可选 L2 聚合诊断。 */
export function AdminRunDetailPage() {
  const { runId = "" } = useParams();
  const detail = useQuery({ queryKey: ["admin", "run", runId], queryFn: () => getAdminRun(runId), enabled: runId !== "" });
  return (
    <main className={styles.page}>
      <PageHeader eyebrow="运行监控 / 详情" title="运行详情" description="只展示公开生命周期与有限诊断，不显示完整客户消息、Prompt、隐藏推理或未脱敏工具输出。" />
      {detail.isError && <QueryProblem error={detail.error} />}
      {detail.data && <><section className={styles.threeColumns}><article className={styles.statCard}><span>状态</span><strong>{detail.data.run.status}</strong></article><article className={styles.statCard}><span>类型</span><strong>{detail.data.run.request_kind}</strong></article><article className={styles.statCard}><span>耗时</span><strong>{detail.data.run.duration_ms ?? "—"}</strong></article></section><section className={styles.twoColumns}><article className={styles.card}><h2>生命周期事件</h2><ul className={styles.eventList}>{detail.data.events.map((event) => <li key={event.event_id}><strong>{event.event_type}</strong><span className={styles.muted}> · {event.phase ?? event.pending_action ?? event.error_code ?? "无附加公开字段"} · {formatTime(event.created_at)}</span></li>)}</ul></article><article className={styles.card}><h2>L2 有限诊断</h2>{detail.data.diagnostics ? <dl><dt>停止原因</dt><dd>{detail.data.diagnostics.stop_reason ?? "—"}</dd><dt>失败归因</dt><dd>{detail.data.diagnostics.failure_attribution ?? "—"}</dd><dt>Steps / Model / Tools</dt><dd>{detail.data.diagnostics.steps_used} / {detail.data.diagnostics.model_calls_used} / {detail.data.diagnostics.tool_calls_used}</dd><dt>工具类别</dt><dd>{detail.data.diagnostics.tool_categories.join("、") || "—"}</dd></dl> : <p className={styles.empty}>该 Run 没有可关联的 L2 诊断。</p>}</article></section></>}
    </main>
  );
}

/** 根据四态语义解释当前 Eval Candidate 的发布质量。 */
function evalStateMessage(value: AdminEvalSnapshot): string {
  const messages = {
    missing: "尚未生成可读取的 Eval Artifact。",
    incompatible: "Candidate 或 Baseline 缺失、损坏或版本不兼容。",
    failed: "Candidate 存在失败、回归或安全违规。",
    passed: "Candidate 与当前 Baseline 兼容且通过。",
  };
  return messages[value.state];
}

/** 展示只读 Eval Baseline、Candidate、分组结果和兼容性。 */
export function AdminEvalPage() {
  const evaluation = useQuery({ queryKey: ["admin", "eval"], queryFn: getAdminEval });
  return (
    <main className={styles.page}>
      <PageHeader eyebrow="质量与诊断" title="Agent 质量评估" description="只读取已生成的评估产物；完整运行、模型资格测试与 Baseline 接受仍使用可信本机命令。" />
      {evaluation.isError && <QueryProblem error={evaluation.error} />}
      {evaluation.data && <><section className={styles.card}><span className={styles.status}>{evaluation.data.state}</span><h2>{evalStateMessage(evaluation.data)}</h2><p className={styles.muted}>Baseline：{evaluation.data.baseline_id ?? "—"} · Candidate：{evaluation.data.candidate_run_id ?? "—"} · 安全违规：{evaluation.data.safety_violation_count}</p>{evaluation.data.compatibility_reasons.length > 0 && <p className={styles.problem}>{evaluation.data.compatibility_reasons.join("；")}</p>}</section><section className={styles.card}><h2>能力分组</h2><div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Suite</th><th>版本</th><th>结果</th><th>安全违规</th></tr></thead><tbody>{evaluation.data.suites.map((suite) => <tr key={suite.suite_id}><td>{suite.suite_id}</td><td>{suite.suite_version}</td><td>{suite.passed_scenarios}/{suite.total_scenarios}</td><td>{suite.safety_violation_count}</td></tr>)}</tbody></table></div></section></>}
    </main>
  );
}

/** 把系统状态枚举转换为可理解的中文摘要。 */
function systemSummary(value: AdminSystemSnapshot): string {
  return value.ready ? "当前依赖满足请求条件。" : `当前未就绪：${value.ready_error_code ?? "状态不可用"}`;
}

/** 展示有限系统状态和后台业务写审计，不提供高影响运维按钮。 */
export function AdminSystemPage() {
  const system = useQuery({ queryKey: ["admin", "system"], queryFn: getAdminSystem });
  const audit = useQuery({ queryKey: ["admin", "audit"], queryFn: listAdminAudit });
  return (
    <main className={styles.page}>
      <PageHeader eyebrow="系统健康" title="系统状态与审计" description="备份、恢复、升级、核对和完整日志继续由可信本机命令管理，Web 端只读展示。" />
      {system.isError && <QueryProblem error={system.error} />}
      {system.data && <section className={styles.twoColumns}><article className={styles.card}><h2>{systemSummary(system.data)}</h2><p className={styles.muted}>版本 {system.data.version} · Migration {system.data.migration_head}</p></article><article className={styles.card}><h2>存储状态</h2>{Object.entries(system.data.storage).map(([name, state]) => <p key={name} className={styles.muted}>{name}：{state}</p>)}</article></section>}
      <section className={styles.card}><h2>后台写操作审计</h2>{audit.isError && <QueryProblem error={audit.error} />}<div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>时间</th><th>动作</th><th>资源</th><th>目标客户</th><th>结果</th></tr></thead><tbody>{audit.data?.map((item) => <tr key={item.audit_id}><td>{formatTime(item.created_at)}</td><td>{item.action}</td><td>{item.resource_type} · {item.resource_id ?? "—"}</td><td>{item.target_user_id ?? "—"}</td><td>{item.result}</td></tr>)}</tbody></table></div></section>
    </main>
  );
}
