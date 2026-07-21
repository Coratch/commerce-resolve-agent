# 代码目录与文件职责

本文档导航 CommerceResolve 已存在的代码与数据边界。版本目标见
[`PROJECT_GOAL_MAP.md`](../PROJECT_GOAL_MAP.md)。

## 目录结构

```text
ecommerce-agent/
├── data/
│   ├── policies/                    # 受版本控制的政策事实来源
│   └── eval/                        # Provider 合成数据与显式接受的脱敏 Baseline
├── frontend/                        # React + TypeScript SPA
│   ├── e2e/                         # Playwright 真实浏览器场景
│   └── src/
│       ├── api/                     # HTTP 客户端、OpenAPI 生成类型
│       ├── app/                     # 应用外壳、路由与 Session 状态
│       └── features/                # auth、chat、orders、memories 垂直功能
├── migrations/                      # Alembic 业务数据库迁移
├── src/commerce_resolve/
│   ├── adapters/                    # Fake、OpenAI、政策、业务、会话、退款与 L2 适配器
│   ├── web/                         # FastAPI 模块化单体
│   │   └── routes/                  # auth、chat、conversations、orders、L2/Memory API
│   ├── access.py                    # Principal、业务 Scope 与 LLM Policy
│   ├── auth.py                      # 凭证校验、Token 与摘要
│   ├── business_models.py           # Web 业务领域模型
│   ├── checkpointing.py             # LangGraph SQLite Checkpointer
│   ├── cli.py                       # ask、serve、db、invite、index、eval、openapi、l2-context
│   ├── evaluation.py                # v0.1 固定 Eval
│   ├── policy_evaluation.py         # v0.2 固定 Eval
│   ├── web_evaluation.py            # v0.3 固定 Web/权限 Eval
│   ├── refund_evaluation.py         # v0.4 固定退款/审批 Eval
│   ├── l2_evaluation.py             # v0.5 固定 L2 Harness Eval
│   ├── conversation_evaluation.py   # v0.6 固定会话生命周期 Eval
│   ├── context_evaluation.py        # v0.7 固定 Context/Trace/Freshness Eval
│   ├── eval_models.py               # v0.8 通用 Eval、Artifact、Baseline 与比较 Schema
│   ├── eval_catalog.py              # 显式 Suite Registry 与 v0.1-v0.8 Adapter
│   ├── eval_runtime.py              # Run 指纹、Artifact、Baseline 接受与 Candidate 比较
│   ├── eval_system_evaluation.py    # v0.8 的 40 条 Harness 自检场景
│   ├── provider_evaluation.py       # 20 条真实 Provider 双次资格评测
│   ├── eval_release.py              # 固定 ReleaseCheck、受控子进程和发布报告
│   ├── release_checks.py            # 迁移、OpenAPI 类型与敏感产物内部检查
│   ├── conversation_models.py       # 公开会话、消息、Run 与事件领域模型
│   ├── conversation_projection.py   # 内部 Graph 结果到公开消息的白名单投影
│   ├── conversation_runtime.py      # 后台 Run、Graph streaming 与公开事件协调
│   ├── gateways.py                  # Interpreter、业务、政策契约
│   ├── l2_gateways.py               # L2 Model、Case Repository 与依赖契约
│   ├── l2_context.py                # 确定性 Context Pack、Manifest 与来源指纹
│   ├── l2_models.py                 # L2 决策、预算、Observation、Case 与偏好模型
│   ├── l2_observability.py          # 失败归因、公开错误和脱敏诊断投影
│   ├── l2_policy.py                 # 升级、工具、预算与无进展确定性规则
│   ├── l2_tools.py                  # 固定 R0 Tool Registry 与受控 handler
│   ├── l2_memory.py                 # 独立 SqliteStore namespace 与偏好 CRUD
│   ├── l2_workflow.py               # L2 节点、路由、退款桥接与主图注册
│   ├── models.py                    # Agent 与政策稳定模型
│   ├── policy_rules.py              # 确定性政策规则与 Renderer
│   ├── policy_workflow.py           # 政策节点和路由注册
│   ├── refund_rules.py              # 退款资格、指纹和预览纯规则
│   ├── refund_workflow.py           # 退款审批、执行和验证节点
│   ├── state.py                     # AgentState 与可信 RunContext
│   └── workflow.py                  # 唯一 LangGraph 主图
└── tests/                            # 单元、图、Repository、Web 与恢复测试
```

`var/`、`.env`、`frontend/node_modules/`、`frontend/dist/`、测试缓存和浏览器报告是本地
运行产物，不提交仓库。

## 后端分层

```text
CLI / FastAPI routes
        ↓
Session + CSRF + Access Policy
        ↓
可信 AccessPrincipal / BusinessScope
        ↓
请求级 Dependencies + 唯一 build_workflow()
        ↓
Gateway 契约
  ├─ Fake demo adapters
  ├─ Fake Refund Gateway（测试失败注入）
  ├─ OpenAI-compatible Interpreter
  ├─ SQLite Business Repository
  ├─ SQLite Refund Gateway
  └─ SQLite FTS5 Policy Repository
```

- `workflow.py`、`policy_workflow.py`、`refund_workflow.py` 和 `l2_workflow.py` 只编排
  Agent State，不处理 Cookie、密码或 ORM。
- `web/routes/` 只负责 HTTP 契约、授权顺序和依赖装配，不复制订单或政策回答逻辑。
- `access.py` 中的 Policy 根据服务端 Principal 决定模式与 LLM 权限，浏览器不能自报。
- `adapters/sqlite_business.py` 负责账号、邀请码、Session、conversation、私有订单物流及配额。
- `adapters/sqlite_refunds.py` 负责支付、退款动作、退款、审计、原子幂等执行和独立回读。
- `adapters/sqlite_l2.py` 负责 L2 Case、Context Manifest、模型调用关联、Case 内事件序号、
  聚合指标与归属查询；Prompt、候选正文和隐藏推理不进入业务库。
- `adapters/sqlite_conversations.py` 负责公开会话生命周期、消息、Agent Run、可重放事件、
  幂等请求和失败/中断状态；Context Reader 只有在完整注册身份 SQL 约束内才读取最多 100 条
  公开消息，不读取内部 Checkpoint 作为聊天历史。
- `adapters/openai_l2_agent.py` 只把 OpenAI-compatible Chat JSON 解析为严格 L2 决策，
  不执行工具；`fake_l2_agent.py` 提供离线脚本决策。
- `adapters/l2_freshness.py` 复用既有订单、物流、退款和政策 Gateway 只读重取当前事实，
  通过规范化来源指纹判断 Observation 是否仍有效，不扩大模型工具白名单。
- `refund_rules.py` 是无数据库和模型副作用的确定性资格、余额、指纹与预览层。
- `l2_policy.py` 与 `l2_tools.py` 把模型建议转换为经身份、白名单、参数和预算校验的 R0
  Observation；模型不持有 Repository 或可执行函数。
- `l2_context.py` 从允许来源生成内存 Context Pack 和无正文 Manifest，执行锚点、相关性、
  去重、新鲜度、冲突、Essential 校验及稳定预算裁剪。
- `l2_observability.py` 将 Context、模型、工具、Policy、预算和验证失败映射为稳定归因，
  并只向 Web 或本地 CLI 暴露白名单字段。
- `checkpointing.py` 只保存 LangGraph State；业务事实和政策正文不进入 Checkpoint。
- `eval_catalog.py` 通过显式 Adapter 保留旧 Suite 输出，同时为 217 条场景生成稳定全局 ID；
  不扫描插件或动态导入用户模块。
- `eval_runtime.py` 把源码、Fixture、Catalog 和门槛摘要写入忽略目录中的 Run Artifact；只有
  明确通过且安全违规为零的 Run 才能通过独立命令成为版本控制 Baseline。
- `provider_evaluation.py` 复用既有真实 Interpreter/L2 Adapter，但只使用 20 条合成数据；
  资格报告不保存完整 Prompt、自然语言回复、Key 或 Base URL，并区分 Provider 不可用与
  结构化输出无效。
- `eval_release.py` 只执行代码中固定的测试、迁移、OpenAPI、前端、E2E 和敏感产物检查，
  使用 `shell=False`、环境白名单、超时进程组终止和脱敏本地日志。

## Web 关键文件

| 文件 | 职责 |
|---|---|
| `web/settings.py` | 一次性读取四类数据库、Web、Cookie、Origin、模型额度和静态资源配置。 |
| `web/dependencies.py` | 构造 `WebServices`，解析 Session/CSRF/Principal，执行限流、thread 锁和请求级依赖装配。 |
| `web/routes/auth.py` | 游客 Session、邀请注册、登录、退出及 Session 轮换。 |
| `web/routes/orders.py` | 注册用户私有订单/物流 CRUD、Mock 支付维护和退款只读查询。 |
| `web/routes/chat.py` | 保留兼容同步消息入口，并把退款、升级和记忆决定提交到统一异步 Run。 |
| `web/routes/conversations.py` | 会话列表/历史/生命周期、异步消息、Run 查询、重试和可重放 SSE。 |
| `web/routes/l2.py` | 按 conversation 查询本人 L2 Case，读取指标并用 keyset 分页公开 Trace；同时管理本人确认过的受限长期偏好。 |
| `web/schemas.py` | 严格拒绝额外字段的浏览器请求与公开响应模型。 |
| `web/errors.py` | 稳定、不泄露内部细节的公开错误语义。 |
| `web/spa.py` | 生产环境静态资源和 React Router fallback。 |
| `web/app.py` | FastAPI 工厂、生命周期、异常处理和安全响应 Header。 |

## 前端关键文件

| 文件或目录 | 职责 |
|---|---|
| `src/api/generated.ts` | 从真实 FastAPI OpenAPI 自动生成的 TypeScript 类型，不手工维护。 |
| `src/api/client.ts` | 同源 Fetch、CSRF Header、Cookie 与公开错误映射。 |
| `src/app/App.tsx` | SPA 外壳、React Router 与公开访问模式导航。 |
| `src/app/session.ts` | TanStack Query 管理当前服务端 Session。 |
| `src/features/auth/` | 邀请注册和登录页面。 |
| `src/features/chat/ChatPage.tsx` | 服务端会话列表、URL 选择、历史恢复、异步发送、SSE 进度和生命周期操作。 |
| `src/features/chat/ChatPage.module.css` | 会话侧栏、公开消息、进度和审批卡的响应式样式。 |
| `src/features/chat/L2Cards.tsx` | AI 升级预览、长期偏好建议，以及可选择 Case、分页、显示刷新/裁剪和指标的公开 L2 Trace。 |
| `src/features/orders/` | 私有订单/物流 CRUD、Mock 支付维护和退款记录展示。 |
| `src/features/memories/` | 已确认长期偏好的查看、受限枚举纠正和删除页面。 |
| `e2e/web-flow.spec.ts` | 游客路径及“私有数据→退款审批→AI 二线多工具 Case”的真实 HTTP 浏览器闭环。 |
| `e2e/refund-ui.spec.ts` | 不监听端口时加载真实构建产物的 Chromium 退款 UI 合约场景。 |

前端不保存 Session Token、模型 Key、用户 ID 或工作区 ID，也不直接访问数据库、模型或
LangGraph。回复按纯文本渲染，不执行 Agent 输出中的 HTML。

## 一次 Web Chat 请求如何流动

```text
React POST /api/conversations/{thread_id}/messages
  {message, client_message_id}
  → Cookie Session + Origin + CSRF 校验
  → 服务端生成 AccessPrincipal
  → 业务库先校验 conversation 的 subject/workspace/mode 归属
  → 业务库原子接受用户消息、Agent Run 和 accepted 事件
  → 202 Accepted {run_id, reused}
  → BackgroundTask 获取 thread 互斥锁
  → guest: Fake Interpreter + 只读 demo Gateway
     registered: LLM Policy + 原子额度 + OpenAI Interpreter + 私有 SQLite Gateway
  → build_workflow() + SQLite Checkpointer + graph.stream(updates)
  → 每个已完成节点映射为有限阶段并写入公开 Run Event
  → Graph 终态/interrupt 经 Public Projector 写入助手消息和 Run 终态
  → 浏览器 GET /runs/{run_id}/events 使用 SSE 读取并按 Last-Event-ID 重放
  → 页面按服务端消息历史刷新列表与当前对话
```

授权发生在打开 Checkpoint 之前。注册用户的业务事实每轮都从 Repository 重读，不把旧
订单或物流正文作为会话事实复用；模型失败也不会切换到 Fake。兼容入口
`POST /api/chat/messages` 调用相同 Runtime 并等待终态，但 React 不再依赖该同步路径。

## 一次 Mock 退款如何流动

```text
POST /api/conversations/{thread_id}/messages {message, client_message_id}
  → Interpreter 只提取 refund_request / order_id / refund_reason
  → Refund Gateway 读取最新订单、物流、支付和已有退款
  → Refund Policy + 当前政策事实计算资格、余额、渠道和引用
  → 服务端保存 awaiting_approval action
  → LangGraph interrupt，Checkpoint 记录恢复点
  → React 恢复并展示不可修改的 R2 Preview Card
  → POST /api/conversations/{thread_id}/refund-approval {action_id, approve|reject}
  → Session / Origin / CSRF / conversation / action 绑定校验
  → 原子接受决定 Run，返回 202；后台恢复 Graph，并通过同一 SSE 管道公开结果
  → 拒绝：幂等终止，零退款
     批准：重读事实和政策并校验 fingerprint
  → SQLite 短事务按 action_id 幂等写入 Mock 退款和审计
  → Verifier 使用新查询回读退款、支付余额和关联字段
  → 只有 verified=true 才公开 refund_completed
```

审批恢复使用不允许调用 Interpreter 的依赖装配，因此不会重复消耗 LLM 配额。客户端不
接收或回传执行金额、渠道、资格和工作区；这些字段只从服务端 action 读取。

## 一次 AI 二线 Case 如何流动

```text
POST /api/conversations/{thread_id}/messages 明确请求升级
  → 一线 Interpreter 只提取 l2_support_request / order_id / issue_summary
  → 服务端 L2 Upgrade Policy 验证注册身份、LLM 能力、额度和中断冲突
  → LangGraph interrupt，页面展示“AI 二线客服，并非真人”、工具和预算
  → POST /api/conversations/{thread_id}/l2-upgrade-decision {preview_id, confirm|cancel}
  → 原子接受决定 Run，返回 202；后台恢复 Graph，并通过同一 SSE 管道公开结果
  → confirm 后幂等创建 L2 Case；cancel 保持零 Case、零模型调用
  → 每轮从授权公开消息、当前 Observation 和已确认偏好构造 Context Candidate
  → Freshness Reader 重取任务相关业务/政策来源并比较指纹
  → Context Builder 执行相关性、去重、冲突、Essential 和输入预算裁剪
  → 先幂等保存无正文 Manifest；保存失败或必要上下文无效时零 Provider 调用
  → L2 Model 只接收 Context Pack，每轮只返回一种严格结构化决策
  → Harness 校验身份、工具白名单、参数、预算和 action signature
  → R0 Tool Registry 读取最新订单、物流、退款状态、政策或已确认偏好
  → 受控 Observation 回到 Agent Loop
  → ask_user / memory / refund 使用独立 interrupt；answer/stop 结束 Case
  → Case Repository 保存公开状态、预算、模型用量、失败归因与单调序号 Trace
  → GET /api/l2-cases/{case_id}/trace 只读分页，不重新执行 Graph 或副作用
```

L2 提出的退款只携带订单和原因，必须进入既有 v0.4 退款链；它不能提供金额、渠道或批准
结果。偏好建议也不会直接写 Store，必须由用户确认后按 `(workspace, user)` namespace
幂等写入。

## 四类持久化数据

| 数据 | 默认位置 | 生命周期 |
|---|---|---|
| 账号、邀请、Session、workspace、conversation、公开消息/Run/Event、订单/物流、支付、退款、L2 Case、计量、审计、额度 | `var/business.sqlite` | 产品与业务事实，Alembic 迁移 |
| LangGraph State | `var/checkpoints.sqlite` | 会话恢复，Checkpointer 管理 |
| 政策检索索引 | `var/policy-index.sqlite` | 派生数据，可从 `data/policies/` 重建 |
| 已确认长期偏好 | `var/memory.sqlite` | LangGraph Store，独立 namespace，可查看、纠正和删除 |

四类 SQLite 不能合库。密码、明文邀请码、Cookie Token、CSRF Token 和 LLM Key 不进入
Checkpoint、浏览器响应或日志。

v0.4 在业务库新增 `mock_payments`、`refund_actions`、`mock_refunds` 和
`refund_audit_events`。金额使用整数 minor units；partial unique index 阻止同一订单存在
多个活动退款动作或完成退款。政策索引仍是可重建派生数据，不作为退款业务事实。

v0.5 migration 新增 `l2_support_cases`、`l2_case_events` 和 `l2_model_calls`；Memory Store
使用独立 `memory setup` 初始化，不由 Alembic 管理。Checkpoint 只保存 Loop 恢复所需的
Runtime、预算和引用，不保存完整 Prompt、数据库连接或大型业务正文。

v0.6 migration 扩展 `conversations`，新增 `conversation_messages`、`agent_runs` 和
`agent_run_events`。公开历史只保存经过 Pydantic 白名单投影的消息、引用和动作卡片；
`request_hash`、`checkpoint_id`、内部 event key、Prompt、隐藏推理和原始 ToolMessage 不进入
浏览器响应。迁移前会话标记为 `history_state=partial`。

v0.7 migration 新增 `l2_context_manifests`，扩展 L2 Case、事件和模型调用的 Context 策略、
Trace 状态、Case 内单调序号、Manifest 关联、Token/耗时及失败归因字段。新模型调用必须先
保存 Manifest；旧 Case 保持原事件并标记为 `trace_state=partial`，不会从 Checkpoint 补造
上下文历史。Context Pack 只在调用内存在，Manifest 不保存候选正文。

## 测试定位

- `test_workflow.py`、`test_policy_workflow.py`：v0.1/v0.2 主图行为。
- `test_business_repository.py`：迁移、认证、邀请、私有数据与事务。
- `test_web_guest.py`、`test_web_auth.py`、`test_web_business_data.py`：HTTP 权限闭环。
- `test_web_chat.py`、`test_web_recovery.py`：模型授权、conversation、恢复和失败不降级。
- `test_v03_evaluation.py`：20 个固定 v0.3 发布场景。
- `test_refund_repository.py`：金额、迁移、业务约束、幂等事务和回读验证。
- `test_refund_workflow.py`：资格、引用预览、interrupt、拒绝、批准、过期和失败路由。
- `test_web_refunds.py`：恢复、配额、重复批准、跨 thread、CSRF/Origin 和越权边界。
- `refund_evaluation.py` / `test_cli.py`：24 个固定 v0.4 发布场景和 CLI 门禁。
- `test_l2_persistence.py`、`test_l2_memory.py`：L2 Case/Trace/计量与独立 Store 生命周期。
- `test_l2_policy.py`、`test_l2_tools.py`、`test_openai_l2_agent.py`：决策 Schema、预算、
  Tool Harness 和真实适配器边界。
- `test_l2_workflow.py`、`test_web_l2.py`：升级、Loop、补充信息、偏好、退款桥接和恢复。
- `l2_evaluation.py` / `test_l2_evaluation.py` / `test_cli.py`：30 个固定 v0.5 场景和发布门禁。
- `test_conversation_lifecycle.py`：历史恢复、消息/Run 幂等、会话生命周期、Pending Action
  归档和删除门禁。
- `test_l2_context.py`：确定性选择、去重、Essential、预算和无正文 Manifest。
- `test_l2_freshness.py`：订单、物流、退款、政策来源指纹刷新与 unavailable 语义。
- `test_l2_context_persistence.py`：Manifest/模型关联、序号分页、身份读取、旧数据迁移与损坏降级。
- `test_context_evaluation.py` / `context_evaluation.py` / `test_cli.py`：36 个固定 v0.7 场景、
  长上下文压缩、安全、Replay、归因和 CLI 发布门禁。
- `test_web_l2.py`、`ChatPage.test.tsx`、`e2e/web-flow.spec.ts`：服务端 Case 恢复、指标、
  Trace 分页去重、跨账号隔离，以及刷新后的真实浏览器回放。
- `conversation_evaluation.py` / `test_conversation_evaluation.py` / `test_cli.py`：32 个固定
  v0.6 会话、SSE、恢复、身份和数据最小化场景。
- `eval_system_evaluation.py` / `test_eval_system_evaluation.py`：40 个 Catalog、Artifact、
  Baseline、比较、故障归因、安全与发布门禁元场景，以及强制失败注入。
- `test_eval_catalog.py`、`test_eval_runtime.py`、`test_eval_cli.py`：统一 217 场景 Catalog、
  可复现 Run、显式 Baseline、Candidate 比较与兼容 CLI。
- `provider_evaluation.py` / `test_provider_evaluation.py`：20 条合成 Provider Fixture 的两次
  资格运行、Fake Provider 确定性验证和脱敏 Artifact。
- `eval_release.py` / `release_checks.py` / `test_eval_release.py`：固定工程命令、失败传播、
  环境白名单、迁移、OpenAPI 类型和敏感产物检查。
- `frontend/src/**/*.test.tsx`：API/组件行为。
- `frontend/e2e/*.spec.ts`：生产 SPA + FastAPI 与无端口构建产物的浏览器闭环。

## 当前边界

v0.8 只实现本地单实例、单 Uvicorn worker、进程内 BackgroundTask、步骤级 SSE、单个有界
AI 二线 Agent 和固定 R0 工具。不实现真实人工客服、真实支付、部分退款、逆向物流、
多级审批、多 Agent、Provider Token 流、Redis/外部 Worker、多实例部署或真实电商接入。
进程退出会将未完成 Run 标记为 `interrupted`，由用户显式安全重试，不承诺自动续跑。

v0.8 Eval Artifact 默认位于 `var/eval/` 并被 Git 忽略；版本库只保存合成数据、明确接受的
脱敏 Baseline 和稳定报告。离线门禁与 Provider 资格是独立通道，Provider 波动不能降低
确定性安全门槛。

新增、删除、移动代码目录或改变文件职责时，必须同步更新本文档。
