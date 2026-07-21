# CommerceResolve

基于 LangGraph 的可审计电商售后客服 Agent。游客可在 Web 中使用只读演示数据和
确定性 Fake 模式；受邀用户可维护私有演示订单、物流和 Mock 支付，使用服务端授权的
LLM 查询私有数据，并对确定性生成的 Mock 整单退款预览进行批准或拒绝。复杂问题可以在
用户确认后升级到明确标注为 AI 的二线客服，由受控 Agent Harness 调用只读工具、暂停
追问、建议长期偏好或复用既有 Mock 退款审批链。公开消息、Agent Run 和有限步骤事件由
服务端持久化，支持刷新恢复、多会话管理、审批恢复和可重放 SSE。L2 每次模型调用前由
确定性代码选择、刷新和裁剪上下文，并先保存不含正文的 Context Manifest；本人可以在
对话页只读回放公开 Trace，开发者可以查看脱敏诊断与聚合指标。

本项目只用于学习和实践 Agent 工程。订单、物流、支付、退款和 L2 Support Case 全部使用本地
Mock/Fake 数据与适配器，不接入真实电商平台、物流服务、支付渠道或交易系统，不产生
真实资金和外部业务副作用。

Mock 数据可以持久化到本地 `var/business.sqlite`；“Mock”表示它与真实外部系统隔离，
不表示只能使用内存数据。LangGraph 工作流状态仍单独保存在 Checkpoint 数据库中。

## 当前状态

- `v0.1 订单与物流查询`：Completed，固定 Eval `15/15`。
- `v0.2 售后政策 RAG`：Completed，固定 Eval `20/20`。
- `v0.3 Web、邀请注册与私有业务数据`：Completed，固定 Eval `20/20`。
- `v0.4 Mock 退款预览、审批与幂等执行`：Completed，固定 Eval `24/24`。
- `v0.5 LLM 二线客服 Agent Harness`：Completed，固定 Eval `30/30`。
- `v0.6 会话生命周期、历史恢复与可恢复交互`：Completed，固定 Eval `32/32`。
- `v0.7 L2 上下文工程、轨迹回放与可观测性`：Completed，固定 Eval `36/36`。

v0.7 的核心边界：Context Pack 只在调用内存在；数据库中的 Manifest 仅保存来源引用、版本、
选择原因、计数和哈希，不保存完整 Prompt、候选正文或隐藏推理。Replay 只读取业务库，不会
重新运行 Graph、模型或工具。订单、物流、退款与政策在模型调用前按真实来源重新验证；必要
事实缺失、过期、冲突或超预算时安全停止且不调用 Provider。

## 安装

后端使用 Python 3.12 和 Conda：

```bash
conda activate ecom-agent
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

前端使用锁定的 npm 依赖：

```bash
cd frontend
npm ci
cd ..
```

复制本地配置；只有受邀用户使用真实 LLM 时才必须填写三个 `LLM_*` 变量：

```bash
cp .env.example .env
```

## 初始化运行数据

业务 Schema 不会在应用启动时自动创建，首次运行必须显式迁移。政策索引也是独立、
可重建的派生数据：

```bash
python -m commerce_resolve db upgrade
python -m commerce_resolve policy-index build
python -m commerce_resolve memory setup
```

创建一个默认七天有效、只能使用一次的邀请码：

```bash
python -m commerce_resolve invite create
```

命令只在本次输出明文邀请码；业务数据库仅保存摘要。也可以撤销尚未使用的邀请码：

```bash
python -m commerce_resolve invite revoke --invite-id <invite_id>
```

## 启动 Web

开发模式使用两个终端。后端：

```bash
conda activate ecom-agent
python -m commerce_resolve serve
```

前端：

```bash
cd frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 只在开发时把 `/api` 代理到 FastAPI。

本地交付模式先构建 SPA，再由 FastAPI 同源托管：

```bash
cd frontend
npm run build
cd ..
python -m commerce_resolve serve
```

浏览器打开 `http://127.0.0.1:8000`。当前只承诺本地单实例和 Uvicorn 单 worker；HTTPS
部署时必须设置 `COOKIE_SECURE=true`。

## Web 使用路径

1. 游客打开 `/chat`，可查询共享 `ORD-001`、物流和售后政策，真实 LLM 调用为 `0`。
2. 操作员通过 CLI 创建邀请码。
3. 用户在 `/register` 注册，再到 `/login` 登录。
4. 注册用户在 `/orders` 创建或维护私有演示订单、物流和已结算 Mock 支付。
5. 用户回到 `/chat`，输入例如“请退款 ORD-DEMO-001，商品有质量问题”。
6. 系统读取最新业务事实和当前政策，生成带引用的 R2 退款预览并暂停。
7. 用户明确批准或拒绝；批准后只在本地业务库创建一笔 Mock 退款并回读验证。
8. 返回 `/orders` 可以查看退款标识、金额和状态。
9. 在 `/chat` 明确请求“升级二线客服处理复杂售后问题”，先查看 AI 身份、工具和预算预览。
10. 确认后，Agent 在固定预算内读取订单、物流、退款状态、政策或已确认偏好；需要更多信息
    时会暂停等待补充。
11. Agent 建议长期偏好时，只有用户确认后才写入独立 Memory Store；可在 `/memories`
    查看、纠正或删除。
12. Case 的公开状态、上下文计数、Token/耗时和脱敏轨迹会显示在对话页；刷新、切页或重新
    登录后仍从服务端按 Case 序号只读回放，且支持多个历史 Case。
13. 注册用户可在会话侧栏新建、切换、归档、恢复或删除会话；当前会话写入 URL。
14. 刷新、切页或重新登录后，页面从服务端重新加载公开历史和待处理动作。

页面关闭或服务重启后，重新进入同一个 conversation 会恢复待审批预览。待审批期间普通
消息输入被禁用；批准请求只能提交服务端 `action_id` 和决定，不能覆盖金额或渠道。

普通消息与退款、L2 升级、长期偏好等决定均进入同一 Run/Event 管道。运行中会话通过
服务端步骤级 SSE 展示进度；短暂断线可用 `Last-Event-ID` 补发缺失事件。当前不直接展示
Provider 原始 Token，因为结构化模型输出必须先通过 Schema、Policy 和业务验证。

账号、邀请、Session、工作区、业务数据和 L2 Case 事实保存在 `var/business.sqlite`；
LangGraph State 保存在 `var/checkpoints.sqlite`；政策索引保存在
`var/policy-index.sqlite`；已确认长期偏好保存在 `var/memory.sqlite`。四者不能合库。

## CLI 学习与调试入口

CLI 仍可直接验证 v0.1/v0.2 主图：

```bash
python -m commerce_resolve ask \
  --thread-id order-001 \
  --user-id user-001 \
  "查询订单 ORD-001 的物流"

python -m commerce_resolve ask \
  --thread-id policy-001 \
  --user-id user-001 \
  "签收后多少天可以退货？"
```

真实 OpenAI-compatible Chat 只在显式选择时用于可信本地 CLI：

```bash
python -m commerce_resolve ask \
  --interpreter openai \
  --thread-id deepseek-001 \
  --user-id user-001 \
  "换货期限和条件是什么？"
```

Web 不接受 `--interpreter` 等客户端授权字段。

查看指定 L2 Case 的本地脱敏 Context Manifest、公开 Trace 和聚合指标：

```bash
python -m commerce_resolve l2-context inspect \
  --case-id <case_id> \
  --database var/business.sqlite
```

诊断输出不包含上下文正文、完整 Prompt、隐藏推理、原始身份或密钥，也不会重新运行 Agent。

## 测试、类型与 Eval

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check

python -m commerce_resolve eval --suite v0.1
python -m commerce_resolve eval --suite v0.2
python -m commerce_resolve eval --suite v0.3
python -m commerce_resolve eval --suite v0.4
python -m commerce_resolve eval --suite v0.5
python -m commerce_resolve eval --suite v0.6
python -m commerce_resolve eval --suite v0.7
python -m commerce_resolve eval --suite all

cd frontend
npm run typecheck
npm test
npm run build
npm run test:e2e
npm run test:e2e:offline
```

前端 API 类型来自 FastAPI 的真实 OpenAPI 文档：

```bash
python -m commerce_resolve openapi export
cd frontend
npm run api:generate
```

离线测试和 Eval 使用 Fake Model、临时数据库和本地 ASGI，不访问网络或真实 API Key。

## 文档入口

- [项目目标地图](./PROJECT_GOAL_MAP.md)
- [代码目录与文件职责](./docs/codebase-guide.md)
- [开发流程](./AGENTS.md)
- [全局系统约束](./docs/specs/system-constraints.md)
- [v0.4 Feature Spec](./docs/specs/v0.4-refund-approval.md)
- [v0.4 Technical Plan](./docs/plans/v0.4-refund-approval-plan.md)
- [v0.4 Eval](./docs/eval/v0.4-report.md)
- [v0.5 Feature Spec](./docs/specs/v0.5-l2-support-harness.md)
- [v0.5 Technical Plan](./docs/plans/v0.5-l2-support-harness-plan.md)
- [v0.5 Eval](./docs/eval/v0.5-report.md)
- [v0.6 Feature Spec](./docs/specs/v0.6-conversation-lifecycle.md)
- [v0.6 Technical Plan](./docs/plans/v0.6-conversation-lifecycle-plan.md)
- [v0.6 Eval](./docs/eval/v0.6-report.md)
- [v0.7 Feature Spec](./docs/specs/v0.7-context-observability.md)
- [v0.7 Technical Plan](./docs/plans/v0.7-context-observability-plan.md)
- [v0.7 Eval](./docs/eval/v0.7-report.md)
- [ADR-0008 元数据型 Context Manifest 与双层 Trace](./docs/adr/0008-context-manifest-and-dual-trace.md)
- [ADR-0007 公开会话事件与可重放 SSE](./docs/adr/0007-public-conversation-events-and-sse.md)
- [ADR-0004 同源 Web 架构](./docs/adr/0004-same-origin-web-architecture.md)
- [ADR-0005 Mock 交易边界](./docs/adr/0005-mock-transaction-boundary.md)
- [ADR-0006 LLM 二线客服 Harness](./docs/adr/0006-llm-l2-support-harness.md)

## 已知限制

- 所有订单、支付与退款均为本地 Mock 数据，不连接真实支付或电商系统。
- 只支持单笔原始支付的整单退款，不支持部分退款、优惠分摊或逆向物流。
- 当前受邀用户同时充当申请人与演示审批人，不代表生产级职责分离。
- SQLite 和单实例 Web 只用于本地学习；没有生产并发、对账和灾难恢复保证。
- 二线客服仍是 AI，不连接真实人工客服系统；当前只有一个有界 Agent，不包含多 Agent。
- L2 只开放固定 R0 只读工具；没有任意代码执行、外部网络工具或自动批准退款。
- SSE 是已经持久化的步骤级公开事件，不提供 Provider Token 级流式传输。
- `BackgroundTasks` 只适用于本地单进程；进程退出会把未完成 Run 标记为 `interrupted`，
  由用户显式安全重试，不提供生产级自动续跑。
- v0.6 迁移前已有会话标记为 `history_state=partial`，不会从内部 Checkpoint 推测或公开旧消息。
- v0.7 迁移前已有 L2 Case 标记为 `trace_state=partial`，不会补造 Context Manifest；损坏的新
  Trace 会降级为 `unavailable`。
- 当前 Context 选择使用确定性字符串、订单锚点与中文 bigram；未引入 Embedding、Rerank、
  自动摘要或外部 Trace 平台。Token 在 Provider 不返回 Usage 时为显式标记的保守估算。
- 会话暂不支持全文搜索、编辑单条消息、重新生成、分支、分享、导出或附件。
