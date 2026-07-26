# 代码目录与文件职责

本文档导航 CommerceResolve 已存在的代码与数据边界。版本目标见
[`PROJECT_GOAL_MAP.md`](../PROJECT_GOAL_MAP.md)。

## 目录结构

```text
ecommerce-agent/
├── deploy/                          # 固定单机生命周期入口与速查
├── Dockerfile                       # Node/Python 多阶段非 root 运行镜像
├── compose.yaml                     # 单服务、回环端口和只读容器拓扑
├── data/
│   ├── demo/v1.3/                  # 版本化商品、画像和商品资源目录
│   ├── demo/portfolio-demo-v1.json # V2.0 三场景演示工作区契约
│   ├── policies/                    # 受版本控制的政策事实来源
│   ├── operations/                  # v0.8 → v1.0 合成升级夹具
│   └── eval/                        # 固定 Eval、Provider 合成数据与显式接受的脱敏 Baseline
├── docs/
│   ├── eval/catalog/                # 可审查的版本化 Eval Suite 清单
│   ├── eval/v1.3.1-report.md        # 商业可信度工程验收、量表结果与独立门禁
│   ├── eval/v1.3.2-report.md        # 沉浸式界面的图标、动效、响应式和边界验收
│   ├── eval/v2.0-report.md          # V2.0 产品、四层 Eval 与 Provider 验收证据
│   ├── eval/v2.0.1-report.md        # 未知意图澄清、恢复、上限和安全验收证据
│   └── product/                     # 固定截图清单与锚定产品评审量表
├── frontend/                        # React + TypeScript SPA
│   ├── e2e/                         # Playwright 真实浏览器场景
│   ├── public/catalog/v1.3/         # 本地商品图片、fallback 与资源来源/摘要清单
│   └── src/
│       ├── api/                     # HTTP 客户端、OpenAPI 生成类型
│       ├── app/                     # 应用外壳、路由与 Session 状态
│       ├── components/              # 跨路由的生成式 Canvas 等表现层组件
│       └── features/                # 客户 auth/chat/support/memories 与 admin 运营功能
├── migrations/                      # Alembic 业务数据库迁移
├── src/commerce_resolve/
│   ├── adapters/                    # Fake、OpenAI、政策、业务、会话、退款、L2 与运营适配器
│   ├── operations/                  # Manifest、锁、Preflight、生命周期、备份恢复、升级和诊断
│   ├── web/                         # FastAPI 模块化单体
│   │   └── routes/                  # auth、support、workspace、chat、conversation、admin API
│   ├── access.py                    # Principal、业务 Scope 与 LLM Policy
│   ├── auth.py                      # 凭证校验、Token 与摘要
│   ├── business_models.py           # Web 业务领域模型
│   ├── checkpointing.py             # LangGraph SQLite Checkpointer
│   ├── cli.py                       # ask、serve、admin、ops、db、invite、index、eval、openapi、l2-context
│   ├── evaluation.py                # v0.1 固定 Eval
│   ├── policy_evaluation.py         # v0.2 固定 Eval
│   ├── web_evaluation.py            # v0.3 固定 Web/权限 Eval
│   ├── refund_evaluation.py         # v0.4 固定退款/审批 Eval
│   ├── l2_evaluation.py             # v0.5 固定 L2 Harness Eval
│   ├── conversation_evaluation.py   # v0.6 固定会话生命周期 Eval
│   ├── context_evaluation.py        # v0.7 固定 Context/Trace/Freshness Eval
│   ├── eval_models.py               # v0.8 通用 Eval、Artifact、Baseline 与比较 Schema
│   ├── eval_catalog.py              # 当前与历史归档 Suite 的显式 Registry
│   ├── eval_runtime.py              # Run 指纹、Artifact、Baseline 接受与 Candidate 比较
│   ├── eval_system_evaluation.py    # v0.8 的 40 条 Harness 自检场景
│   ├── operations_evaluation.py     # v1.0 的 32 条单机交付与恢复场景
│   ├── service_center_evaluation.py # v1.1 的 36 条服务中心、安全与升级场景
│   ├── admin_evaluation.py          # v1.2 的 40 条角色、运营、脱敏与双表面场景
│   ├── commercial_experience_evaluation.py # v1.3 的 48 条目录、投影、组合咨询与体验场景
│   ├── commercial_credibility_evaluation.py # v1.3.1 的 32 条信息架构、语言、响应式与证据场景
│   ├── immersive_interface_evaluation.py # v1.3.2 的 24 条图标、动效、艺术方向与边界场景
│   ├── v20_product_evaluation.py    # V2.0 Workflow、RAG、Loop 与 Safety 四层 Eval
│   ├── admin_models.py              # 运营客户、邀请、审计、Run、Eval 和系统公开模型
│   ├── admin_services.py            # 固定 Eval 根与有限系统状态的只读服务
│   ├── demo_catalog.py              # v1.3 目录校验与面向显式客户的幂等场景初始化
│   ├── demo_catalog_models.py       # 商品、SKU、资源、画像、场景和包裹严格 Schema
│   ├── portfolio_demo.py            # V2.0 Manifest、公开订单号与注册工作区初始化
│   ├── workspace_models.py          # 演示工作区状态、重置请求和审计领域模型
│   ├── workspace_reset.py           # 锁定、幂等的完整工作区重置协调服务
│   ├── provider_evaluation.py       # 12–100 条真实 Provider 双次资格评测
│   ├── eval_release.py              # 固定 ReleaseCheck、受控子进程和发布报告
│   ├── release_checks.py            # 部署、迁移、OpenAPI 类型与敏感产物内部检查
│   ├── structured_logging.py        # request/run/action 关联与脱敏 JSON 日志
│   ├── conversation_models.py       # 公开会话、消息、Run 与事件领域模型
│   ├── conversation_projection.py   # 内部 Graph 结果到公开消息的白名单投影
│   ├── conversation_runtime.py      # 后台 Run、Graph streaming 与公开事件协调
│   ├── order_context.py             # 对话前显式订单号提取与规范化
│   ├── service_center.py            # 注册客户售后首页、订单与服务状态公开读模型
│   ├── service_center_models.py     # 售后首页、订单、物流和服务投影模型
│   ├── service_guidance.py           # 组合咨询的确定性事实查询、政策召回和方案装配
│   ├── service_resolution.py         # 可持久化公开服务方案与允许动作 Schema
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
  Agent State，不处理 Cookie、密码或 ORM；一级图中的 `clarify_intent` 与
  `respond_intent_fallback` 负责有限跨轮次意图澄清。
- `adapters/openai_interpreter.py` 只生成严格 `Interpretation`；“退库”等已知高影响歧义
  先确定性收敛为 `unknown`，不能由模型猜测成退款或退货。
- `web/routes/` 只负责 HTTP 契约、授权顺序和依赖装配，不复制订单或政策回答逻辑。
- `access.py` 中的 Policy 根据服务端 Principal 决定模式与 LLM 权限，浏览器不能自报。
- `adapters/sqlite_business.py` 负责账号、邀请码、Session、conversation、私有订单物流及配额。
- `adapters/sqlite_refunds.py` 负责支付、退款动作、退款、审计、原子幂等执行和独立回读。
- `adapters/sqlite_l2.py` 负责 L2 Case、Context Manifest、模型调用关联、Case 内事件序号、
  聚合指标与归属查询；Prompt、候选正文和隐藏推理不进入业务库。
- `adapters/sqlite_conversations.py` 负责公开会话生命周期、消息、Agent Run、可重放事件、
  幂等请求和失败/中断状态；Context Reader 只有在完整注册身份 SQL 约束内才读取最多 100 条
  公开消息，不读取内部 Checkpoint 作为聊天历史。
- `adapters/sqlite_service_center.py` 只从订单、物流、退款动作、Mock 退款、Conversation 和 L2 Case
  投影客户售后读模型；v1.3 增量读取商品快照、金额和多包裹履约，但不保存第二套服务状态，
  也不执行 Graph 或模型。
- `demo_catalog.py` 只从版本控制目录读取模板，校验本地资源摘要，并在显式 CLI 或管理员请求中
  幂等写入目标客户；应用启动、客户页面和只读查询不会自动 Seed。
- `portfolio_demo.py` 在邀请注册事务中装配三个 V2.0 场景，并生成不编码用户、状态或风险的
  `CR-XXXX-XXXX` 公开订单号；隐藏 `scenario_key` 不进入公开 Schema 或模型上下文。
- `workspace_reset.py` 在同一工作区锁下恢复基准事实并清理退款、服务、Conversation、
  Checkpoint 和 Memory；重试通过稳定 generation 和 request ID 保持幂等。
- `adapters/sqlite_workspaces.py` 只负责演示工作区版本、状态、重置审计和请求幂等事实。
- `service_guidance.py` 在一次结构化解释后至多各查询一次订单、物流和政策，把最新事实装配成
  `ServiceResolution`；它只提出 `request_refund` 候选动作，不绕过既有退款审批。
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
- `eval_catalog.py` 当前发布门禁只包含与 V2.0 产品契约兼容的 9 个 Suite、共 265 条场景；
  其余历史 Suite 归档但仍可显式回放。完整历史 Catalog 共 465 条，不扫描插件或动态导入
  用户模块。
- `eval_runtime.py` 把源码、Fixture、Catalog 和门槛摘要写入忽略目录中的 Run Artifact；只有
  明确通过且安全违规为零的 Run 才能通过独立命令成为版本控制 Baseline。
- `provider_evaluation.py` 复用既有真实 Interpreter/L2 Adapter，支持 12–100 条合成数据；
  资格报告不保存完整 Prompt、自然语言回复、Key 或 Base URL，并区分 Provider 不可用与
  结构化输出无效。
- `eval_release.py` 只执行代码中固定的测试、迁移、OpenAPI、前端、E2E 和敏感产物检查，
  使用 `shell=False`、环境白名单、超时进程组终止和脱敏本地日志。
- `operations/` 只负责单机发布和数据生命周期，不绕过 Agent Policy 调用退款或 L2 工具；
  `backup.py` 只备份业务、Checkpoint、Memory 三类权威 SQLite，政策索引始终由来源重建。
- `operations_evaluation.py` 在临时目录实际执行初始化、锁、Run 收敛、Health、备份恢复和
  v0.8 升级；容器真实生命周期由 Release Gate 单独验证。
- `adapters/sqlite_admin.py` 负责管理员角色、有限客户/邀请目录、后台审计、脱敏 Run/Event 与运营
  聚合读写；它不创建客户 Session，也不修改退款、Checkpoint、Memory 或 Eval Artifact。
- `admin_services.py` 只在固定服务端根目录读取 Eval/Baseline 与有限系统状态；Web 请求不会启动
  Eval、Provider Qualification、Backup、Restore 或 Upgrade。

## v1.0 运维关键文件

| 文件 | 职责 |
|---|---|
| `operations/models.py` | Release、Instance、Preflight、Backup、Capability 和退出码契约。 |
| `operations/manifest.py` | 计算发布摘要并原子读写 Release/Instance Manifest。 |
| `operations/locking.py` | 用 POSIX `flock` 保证服务和高影响运维互斥。 |
| `operations/preflight.py` | 按 init/serve/backup/restore/upgrade/status 执行只读预检。 |
| `operations/lifecycle.py` | 幂等初始化、Run 收敛、本机状态和派生政策索引重建。 |
| `operations/backup.py` | 停止态 SQLite Snapshot、校验、空目标/覆盖 Restore 和回滚 Backup。 |
| `operations/upgrade.py` | 构建 v0.8 合成实例并编排升级前备份与 v1.0 升级。 |
| `operations/audit.py` | 写入脱敏、有限轮转且与业务库分离的运维审计。 |
| `operations/diagnostics.py` | 聚合版本、能力、表计数和最近有限运维结果。 |
| `operations/cli.py` | 注册固定 `ops` 子命令，不接受任意 Shell。 |
| `Dockerfile` / `compose.yaml` | 构建同源 SPA + FastAPI 镜像并声明单服务安全拓扑。 |
| `deploy/commerce-resolve` | 构建、初始化、启停、备份、恢复和升级的唯一宿主入口。 |

## Web 关键文件

| 文件 | 职责 |
|---|---|
| `web/settings.py` | 一次性读取四类数据库、Web、Cookie、Origin、模型额度和静态资源配置。 |
| `web/dependencies.py` | 构造 `WebServices`，解析 Session/CSRF/Principal，执行限流、thread 锁和请求级依赖装配。 |
| `web/routes/auth.py` | 匿名能力投影、邀请注册、登录、退出及 Session 轮换；匿名请求不创建 Cookie 或工作区。 |
| `web/routes/orders.py` | 已注册客户的本人订单、物流、支付和退款只读兼容 API；公开 CRUD 已移除。 |
| `web/routes/workspace.py` | 本人演示工作区状态和受确认、幂等的完整重置 API。 |
| `web/routes/admin.py` | 管理员邀请、工作区重置、审计、Run、Eval、系统和概览 API；不提供单笔订单 CRUD。 |
| `web/routes/support.py` | 当前身份的售后首页、可搜索筛选订单、快照/包裹详情和只读服务投影 API；读取不会创建会话或调用模型。 |
| `web/routes/chat.py` | 保留兼容同步消息入口，并把退款、升级和记忆决定提交到统一异步 Run。 |
| `web/routes/conversations.py` | 会话列表/历史/生命周期、异步消息、Run 查询、重试和可重放 SSE。 |
| `web/routes/l2.py` | 按 conversation 查询本人 L2 Case，读取指标并用 keyset 分页公开 Trace；同时管理本人确认过的受限长期偏好。 |
| `web/schemas.py` | 严格拒绝额外字段的浏览器请求与公开响应模型。 |
| `web/errors.py` | 稳定、不泄露内部细节的公开错误语义。 |
| `web/spa.py` | 生产环境挂载 Vite `/assets`、本地商品 `/catalog`，并提供 React Router fallback。 |
| `web/app.py` | FastAPI 工厂、生命周期、异常处理和安全响应 Header。 |
| `web/health.py` | 独立 Liveness、只读 Readiness 与可选能力降级状态。 |
| `web/request_context.py` | 生成/校验 request ID，并只记录路由模板和有限请求事实。 |

## 前端关键文件

| 文件或目录 | 职责 |
|---|---|
| `src/api/generated.ts` | 从真实 FastAPI OpenAPI 自动生成的 TypeScript 类型，不手工维护。 |
| `src/api/client.ts` | 同源 Fetch、CSRF Header、Cookie 与公开错误映射。 |
| `src/app/App.tsx` / `App.module.css` | CustomerLayout、React Router、路由滚动复位、Lucide 客户导航和沉浸式品牌外壳。 |
| `src/app/AdminLayout.tsx` | 服务端 Capability 驱动的 Lucide 运营控制台外壳、控制室导航和客户表面切换。 |
| `src/components/InteractiveField.tsx` | 单 RAF Canvas 流场；限制 DPR，并在页面隐藏或 Reduced Motion 时暂停连续动画。 |
| `src/components/InteractiveField.module.css` | 让生成式画布固定铺满视口但不接收指针事件。 |
| `src/app/session.ts` | TanStack Query 管理当前服务端 Session。 |
| `src/styles/global.css` | 全局设计 Token、背景、键盘焦点和 Reduced Motion 兜底。 |
| `src/features/auth/` | 邀请注册和登录页面。 |
| `src/features/chat/useConversationSession.ts` | 封装可复用的消息历史、SSE Run、错误回复和三类待审批动作恢复。 |
| `src/features/chat/ConversationPanel.tsx` | 在订单或服务上下文中复用公开对话、引用和审批卡片。 |
| `src/features/chat/ConversationContextPanel.tsx` | 在对话旁集中展示处理状态、会话事实、回答依据和重要操作审批边界。 |
| `src/features/chat/ConversationContextPanel.module.css` | 上下文面板的状态层级、引用列表、审批说明与响应式表现。 |
| `src/features/chat/ServiceResolutionCard.tsx` | 展示 Payload v2 的目标、事实、依据、建议、允许动作、停止原因和下一步；未知版本不猜测字段。 |
| `src/features/chat/L2Cards.tsx` | AI 升级预览、长期偏好建议和客户可理解的服务记录；不公开工具名、模型调用、Token 或内部错误码。 |
| `src/features/support/AgentDrawer.tsx` | 持有全局订单绑定 Agent 状态；桌面为右侧抽屉，移动端为全屏层，路由切换不卸载。 |
| `src/features/support/DemoSettingsPage.tsx` | 展示演示数据版本和完整重置影响范围，只提交确认及稳定 request ID。 |
| `src/features/support/` | 售后首页、订单/商品/物流详情、服务进度和订单绑定 Agent；不提供客户订单编辑器。 |
| `src/features/admin/` | 分组运营概览、目标客户演示数据、邀请、脱敏 Run 监控、只读 Eval 与系统审计页面。 |
| `src/features/memories/` | 已确认长期偏好的查看、受限枚举纠正、删除和状态说明页面。 |
| `e2e/web-flow.spec.ts` | 游客路径及“私有数据→退款审批→AI 二线多工具 Case”的真实 HTTP 浏览器闭环。 |
| `e2e/refund-ui.spec.ts` | 不监听端口时加载真实构建产物，验证退款闭环、桌面游客/注册布局、移动溢出和 Reduced Motion。 |
| `e2e/service-center.spec.ts` | 真实 HTTP 验证游客订单优先入口、零自动会话创建和移动原生对话框。 |
| `e2e/admin-console.spec.ts` | 验证客户后台拒绝、管理员双产品切换和邀请码明文只显示一次。 |
| `e2e/v1.3-commercial-experience.spec.ts` | 验证管理员场景 Seed、游客商业化旅程及 1440/1024/390 三视口结构与截图。 |
| `e2e/v1.3.1-commercial-credibility.spec.ts` | 通过公开旅程准备十个订单和待确认服务，生成八页面、三视口的二十四份固定产品评审截图。 |
| `e2e/v1.3.2-immersive-interface.spec.ts` | 验证 Canvas、Lucide、路由滚动、商品图真实解码、无横向溢出，并生成客户首页、对话和运营控制室九份三视口截图。 |
| `e2e/v2-product.spec.ts` | 当前在线产品门禁：匿名零业务调用、邀请码注册、三场景、单订单 Agent、退款、刷新恢复、重置和管理员边界。 |

前端不保存 Session Token、模型 Key、用户 ID 或工作区 ID，也不直接访问数据库、模型或
LangGraph。回复按纯文本渲染，不执行 Agent 输出中的 HTML。

`playwright.config.ts` 只运行当前 `v2-product.spec.ts`；旧 V0/V1 E2E 作为历史资产保留，
不混入 V2.0 发布判定。`playwright.offline.config.ts` 继续验证无端口构建产物的退款闭环。

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

## 一次未知意图如何流动

```text
用户发送模糊诉求
  → bind_and_interpret 返回 unknown
  → route_after_interpret 读取 Checkpoint 中的澄清次数
  → clarify_intent 输出普通助手问题，次数 +1
  → END 等待下一条用户消息
  → 下一轮从 START 再次解释
     ├─ 识别成功：次数清零，进入既有安全路径
     ├─ 未知且次数未满：再次澄清
     └─ 已发出两轮澄清：respond_intent_fallback，次数清零
```

该循环跨用户轮次存在，不在单次 Graph 调用中空转。它复用 SQLite Checkpointer，不使用
退款/L2 的 `interrupt()`，也不调用订单、物流、政策、退款或 L2 工具。Provider 失败、配额
耗尽和 Schema 无效仍走原错误语义，不计入澄清次数。

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
  → LangGraph interrupt，页面展示“AI 深度处理助手，并非真人”、工具和预算
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

v1.1 migration `20260722_0006` 新增 `order_items`，并为 `conversations` 增加可空
`related_order_id`。旧订单保持 `items=[]`，旧会话保持无绑定；新绑定只在创建时写入且不级联删除。

v1.2 migration `20260722_0007` 为 `users` 增加默认 `customer` 的稳定角色，并新增
`admin_action_audit`。既有账号、Session 和业务归属不迁移或重写；角色每次请求从账号表重新解析，
撤销后既有 Session 立即失去运营权限。

v1.3 migration `20260722_0008` 为订单行增加可空商品快照、目录/场景标识，并新增商品级包裹履约
明细。历史订单不补造目录事实；新订单从 v1.3 目录复制下单时快照，退款金额仍以 Mock 支付事实为准。

## 测试定位

- `test_workflow.py`、`test_policy_workflow.py`：一级意图澄清和 v0.1/v0.2 主图行为。
- `test_persistence.py`：订单、政策和意图澄清 State 的 SQLite 跨实例恢复。
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
- `test_eval_catalog.py`、`test_eval_runtime.py`、`test_eval_cli.py`：统一 405 场景 Catalog、
  可复现 Run、显式 Baseline、Candidate 比较与兼容 CLI。
- `provider_evaluation.py` / `test_provider_evaluation.py`：20 条合成 Provider Fixture 的两次
  资格运行、Fake Provider 确定性验证和脱敏 Artifact。
- `eval_release.py` / `release_checks.py` / `test_eval_release.py`：固定工程命令、失败传播、
  环境白名单、迁移、OpenAPI 类型和敏感产物检查。
- `test_operations_*.py`、`test_structured_logging.py`、`test_deployment_bundle.py`：v1.0 的
  Preflight、实例锁、生命周期、健康、Backup/Restore、升级、日志和 Bundle 契约。
- `test_service_center_repository.py`、`test_service_center_projection.py`、`test_web_service_center.py`：
  v1.1 商品行、售后读模型、游标、权限和零副作用 API。
- `test_context_bound_conversation.py`：订单绑定、省略订单号、显式冲突和模型前停止。
- `test_v11_upgrade.py`：代表性 v1.0 数据原地升级至迁移 `20260722_0006` 后保持可读。
- `service_center_evaluation.py` / `test_service_center_evaluation.py`：36 条固定 v1.1 场景与安全门禁。
- `test_web_admin.py`、`test_admin_services.py`：管理员权限矩阵、目标客户 Mock 数据、邀请明文、
  Monitoring 白名单、Eval 四态和系统状态只读边界。
- `test_v12_upgrade.py`：v1.1 数据升级至 `20260722_0007` 后角色默认值、Session 和订单保持可读。
- `admin_evaluation.py` / `test_admin_evaluation.py`：40 条固定 v1.2 场景及零安全违规门禁。
- `test_demo_catalog.py`、`test_v13_upgrade.py`：v1.3 目录/资源、幂等 Seed、快照不变性与
  `20260722_0008` 迁移兼容。
- `test_service_guidance.py`：组合意图、一次事实查询、确定性方案、退款候选动作和安全降级。
- `commercial_experience_evaluation.py` / `test_commercial_experience_evaluation.py`：
  48 条固定 v1.3 目录、投影、组合咨询、恢复和安全场景。
- `frontend/src/features/admin/AdminPages.test.tsx`、`frontend/e2e/admin-console.spec.ts`：双表面路由、
  只读运营投影、客户拒绝和一次性邀请码展示。
- `frontend/src/**/*.test.tsx`：API/组件行为。
- `frontend/e2e/*.spec.ts`：生产 SPA + FastAPI 与无端口构建产物的浏览器闭环。
- `commercial_credibility_evaluation.py` / `test_commercial_credibility_evaluation.py`：v1.3.1
  的 32 条信息架构、客户语言、业务事实、运营只读、响应式和证据门禁。
- `test_v20_product_contract.py`：`portfolio-demo-v1`、注册原子性、重置、公开订单号和
  单订单活动 Thread 契约。
- `test_v20_product_evaluation.py` / `v20_product_evaluation.py`：V2.0 Workflow、RAG、
  Agent Loop 和 Safety 四层 36 条固定场景。
- `migrations/versions/20260724_0009_v20_portfolio_workspace.py`：工作区数据集状态、重置审计
  和活动单订单 Thread 唯一索引。
- `frontend/e2e/v2-product.spec.ts`：当前真实浏览器产品闭环；匿名、注册、退款、恢复、重置
  和管理员权限均由真实 HTTP 验证。

## 当前边界

V2.0 只实现本地单实例、单 Uvicorn worker、进程内 BackgroundTask、步骤级 SSE、单个有界
AI 深度处理 Agent 和固定 R0 工具。不实现真实人工客服、真实支付、部分退款、逆向物流、
多级审批、多 Agent、Provider Token 流、Redis/外部 Worker、多实例部署或真实电商接入。
进程退出会将未完成 Run 标记为 `interrupted`，由用户显式安全重试，不承诺自动续跑。

未登录访问者没有游客工作区、游客会话或 Fake 对话。注册必须使用邀请码，成功后原子创建
`portfolio-demo-v1` 三场景工作区。客户和管理员都不能逐笔修改基准订单、物流或支付事实；
完整重置保留公开订单号，并清理退款、服务、Conversation、Checkpoint 和 Memory。

一个活动售后 Thread 只绑定一个注册用户、工作区和订单；相同订单恢复活动 Thread，不同订单
相互隔离。服务记录仍是退款和 L2 事实的只读投影，不提供第二套服务写模型。

客户与运营控制台是同一 SPA 和 FastAPI 应用中的两个权限表面，不是两个服务。管理员只能由
本机 CLI 授予；运营页不能模拟客户、审批退款、执行 Eval/Provider 或运行高影响运维。

当前政策知识仍为版本化 Markdown/JSON 与 SQLite FTS5。文件上传、OCR、向量数据库、
Embedding、Rerank 和多模态证据属于 V2.1，尚未实现。

Eval Artifact 默认位于 `var/eval/` 并被 Git 忽略；版本库只保存合成数据、明确接受的
脱敏 Baseline 和稳定报告。V2.0 当前门禁为 9 个兼容 Suite、265 条场景；历史归档 Suite
继续可回放但不决定当前发布状态。离线门禁与 Provider 资格是独立通道，Provider 波动不能
降低确定性安全门槛。

新增、删除、移动代码目录或改变文件职责时，必须同步更新本文档。
