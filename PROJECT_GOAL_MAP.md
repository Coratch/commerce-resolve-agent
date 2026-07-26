# CommerceResolve 项目目标地图

本文件是长期产品方向、版本顺序和当前焦点的唯一入口。全局业务与安全边界见 [`docs/specs/system-constraints.md`](./docs/specs/system-constraints.md)。

## 北极星

### 产品目标

构建一个可审计、订单优先、邀请制的智能售后中心：受邀注册用户自动获得独立、版本化且可重置的 Mock 演示工作区，并围绕具体订单使用上下文 Agent 完成物流查询、政策检索、退款预览、客户确认、幂等 Mock 退款和结果验证。公开会话可以跨页面和登录恢复，复杂问题由受控 Harness 驱动、明确披露为 AI 的深度处理 Agent 调查；业务状态、确定性 Policy、权限、Checkpoint 和 Eval 共同构成可追溯证据。

订单、物流和售后服务记录是客户侧一级业务对象；Agent 是帮助用户理解事实、澄清目标和推进服务的上下文能力，而不是整个产品的唯一入口。客户默认界面优先呈现正在处理什么、下一步能做什么和最终结果，内部 Agent 诊断通过独立入口或渐进披露提供。

产品长期分为两个明确表面：客户售后中心服务客户任务；Agent 运营控制台服务 Mock 数据准备、脱敏 Monitoring、Eval 和维护。两个表面共享同一个 React/FastAPI 模块化单体、领域核心和权限链路，不因页面职责分离而提前拆分后端服务。

项目只用于学习和实践 Agent 工程。订单、物流、支付、退款和 L2 Support Case 均使用本地 Mock/Fake 数据与适配器，不接入真实电商、客服、支付、物流或交易系统，也不产生真实资金和外部业务副作用。

Mock 描述的是数据来源和业务边界，不限制存储方式。Web 场景的 Mock 业务状态可以持久化到本地业务数据库，测试场景可以使用内存 Fake 或临时数据库；两者都不能与 LangGraph Checkpoint 混用。

产品成功不是“模型给出了听起来合理的回复”，而是售后问题得到正确、安全、可追溯的处理。

### 学习目标

以接近真实售后约束的 Mock 业务场景为载体，逐步实践：

- LangGraph State、Reducer、Node、Edge、Command、Checkpoint、Streaming 与 Human-in-the-loop。
- 模型工具调用、结构化输出和有界 Agent Loop。
- 短期会话记忆、业务状态和长期记忆的分层管理。
- 售后政策 RAG、混合/多模态检索、引用、检索评估与失败分析。
- 确定性 Policy、退款审批、幂等执行和结果验证。
- Agent Eval、Tracing、成本、延迟和安全指标。

每项技术只有在解决当前版本的可验收问题时才进入项目。

项目同时以开源学习和面试作品为目标。关键能力必须能够通过代码、Spec、ADR、测试、Eval 和
失败案例解释“为什么需要、如何实现、如何恢复、如何验证以及有哪些边界”，不能只依赖 Prompt、
页面演示或作者口头说明。客户售后中心应达到成熟电商产品的信息架构与视觉可信度，不使用
Toy/Demo 表达掩盖业务、状态或安全缺口。

## 第一性原理

1. 用户需要的是问题被解决，而不是进行一次聊天。
2. 订单、物流和服务记录先提供稳定业务上下文，对话负责澄清和辅助操作。
3. 项目内 Mock 订单、物流和退款 Repository 是业务事实来源，模型不能编造业务状态。
4. LLM 是候选动作生成器，不是模拟资金操作的授权主体。
5. 查询、决策、审批、执行和验证必须是可观察的独立步骤。
6. 写操作必须幂等；任务恢复不能造成重复退款、Case 或记忆写入。
7. 无法由一级流程安全处理时，可以进入受控 AI 深度处理，但不能冒充真人。
8. Agent 质量必须通过固定场景和业务结果衡量。
9. 身份、业务数据归属和 LLM 使用权是独立权限，必须由服务端确定，不能由客户端或模型自报。

由此得到长期链路：

```text
订单、服务记录或用户请求
→ 绑定并验证用户与业务上下文
→ 识别诉求与订单
→ 查询订单/物流或检索政策
→ 确定性 Policy
→ 必要时人工审批
→ 幂等执行
→ 回读 Mock 业务状态验证
→ 回复或进入 AI 深度处理
→ Checkpoint、Audit 与 Eval
```

## 版本路线

| 版本 | 用户结果 | 核心实践 | 状态 |
|---|---|---|---|
| v0.1 | 查询本人订单和物流，并可跨进程继续会话 | State、只读工具、SQLite Checkpoint、基础 Eval | **Completed（2026-07-17）** |
| v0.2 | 根据售后政策回答并提供可定位来源 | 文档建模、RAG、引用、检索 Eval | **Completed（2026-07-17）** |
| v0.3 | 通过 Web 对话；游客使用只读 Fake 演示，受邀用户维护私有订单与物流并使用 LLM | Web、邀请注册、身份隔离、私有业务数据、模型授权 | **Completed（2026-07-17）** |
| v0.4 | 生成 Mock 退款预览，经审批后安全执行模拟退款 | Policy、interrupt、幂等键、执行后验证 | **Completed（2026-07-19）** |
| v0.5 | 复杂售后问题进入受控 AI 深度处理并可恢复处理 | L2 Agent Harness、有界 Loop、Tool Registry、工作/长期记忆 | **Completed（2026-07-20）** |
| v0.6 | 刷新、切页和重新登录后恢复公开历史，并管理多个会话和进行中交互 | 会话生命周期、公开消息、幂等、渐进输出、断线恢复 | **Completed（2026-07-21）** |
| v0.7 | 提升 L2 Agent 的上下文选择、轨迹回放与可观测性 | Context Engineering、Trace/Replay、失败归因、Harness 指标 | **Completed（2026-07-21）** |
| v0.8 | 用固定数据集衡量正确性、安全性和成本并完成产品加固 | Agent Eval、Tracing、回归门禁 | **Completed（2026-07-21）** |
| v1.0 | 提供可部署、可维护的售后 Agent 服务 | 单机交付、健康、备份恢复、升级和运维 | **Candidate：实现完成，参考 Docker 验收延后（2026-07-22）** |
| v1.1 | 从订单与服务记录进入售后任务，并通过上下文助手完成查询、审批和结果跟踪 | 订单优先信息架构、服务进度投影、上下文绑定、渐进披露 | **产品范围本机验收完成，参考 Docker Gate 延后（2026-07-22）** |
| v1.2 | 将客户售后任务与 Mock 数据、Agent Monitoring 和 Eval 运营入口分离 | 双产品表面、服务端后台权限、模块化单体、脱敏运营视图 | **T1–T8 已实现并通过本机产品验收；参考 Docker Gate 延后（2026-07-22）** |
| v1.3 | 以成熟电商产品的信息架构和视觉质量完成订单售后任务，并由 Agent 输出可操作的结构化方案 | 商品/订单真实感、响应式设计系统、组合诉求编排、结构化 Agent 结果 | **T1–T8 已实现并通过本机产品验收；真实 Provider 未达门槛、Docker 证据待补，保持 Candidate（2026-07-23）** |
| v1.3.1 | 消除 Toy/Demo 感，以完整内容、统一产品系统和锚定评审证明商业可信度 | 任务优先信息层级、默认演示工作区、设计系统、视觉/产品评审 | **工程验收通过，视觉方向未被项目所有者接受；由 v1.3.2 取代（2026-07-24）** |
| v1.3.2 | 以沉浸式编辑排版、统一 Lucide 图标和生成式动效建立独有产品艺术方向 | Lucide、Canvas 流场、物理感动效、先锋版式、可访问性 | **实现及自动验收完成；产品方向由 v2.0 接续（2026-07-24）** |
| v1.4 | 原计划扩展多格式、多模态政策知识 | 历史 Proposed Spec | **Superseded；目标重新排入 v2.1（2026-07-24）** |
| v2.0 | 通过邀请注册、版本化数据、售后中心、悬浮 Agent 和旗舰退款闭环形成可面试产品 | 演示工作区、单订单 Thread、Policy/HITL、Memory、Agent Loop、四层 Eval | **Completed（2026-07-24）** |
| v2.0.1 | 未知意图经过有限澄清后恢复既有路径或安全兜底 | 持久化澄清 State、条件路由、跨轮次逻辑循环、回归 Eval | **Completed（2026-07-24）** |
| v2.1 | 从多格式、多模态政策来源回答复杂问题，并提供精确引用 | 来源生命周期、向量/全文混合检索、多模态证据、复杂问答 Eval | **路线已确认，Feature Spec 待创建** |

顺序依据：先建立可靠的事实查询、知识检索、身份、资金边界和 Agent Harness，再通过 v2.0 把分散能力收敛为一个可重复演示、可恢复、可评估的产品闭环。v2.0 不同时更换知识基础设施；多格式解析、向量数据库和多模态证据在 v2.1 独立交付。Eval 从 v0.1 开始积累，并在 v2.0 形成 Workflow、RAG、Agent Loop 与 Safety 四层门禁。

## 当前状态：v2.0.1 已完成，v2.1 Feature Spec 待创建

v1.1 的 T1–T8 和本机产品验收已完成。项目于 2026-07-22 明确将参考 Docker 构建、Compose
生命周期和容器故障注入延后，不再让该交付门禁阻塞产品功能路线。该决定不会把缺失的容器证据
描述为通过：v1.0 仍是交付 Candidate，v1.1 也不宣称已完成参考部署或可生产运行。

v1.2 Feature Spec、ADR-0012 和 Technical Plan 已接受，T1–T8 已实现并完成本机产品收口。
v1.3 “商业化售后中心与智能服务体验” T1–T8 已实现并完成本机产品验收：固定 Eval
`48/48`、统一离线 Eval `373/373`，确定性安全违规为 `0`。目录、订单快照、多包裹履约、
组合咨询、Payload v2、三视口页面和迁移均已有自动化证据。真实 Provider 连续两轮只通过
`10/24`，未达资格门槛；已延期的 Docker 参考门禁也尚未完成，因此版本保持 Candidate。

v1.3.1 “商业产品可信度重构” T1–T8 已实现：后端 `330/330`、前端组件 `26/26`、在线 E2E
`9/9`、离线 E2E `3/3`、固定 Eval `32/32`、统一离线 Eval `405/405`，安全违规为 `0`。
项目所有者未接受其保守视觉方向，因此工程证据保留，视觉路线由 v1.3.2 取代。

v1.3.2 已完成统一 Lucide 图标、生成式 Canvas、实验性排版和物理感动效重构。后端
`333/333`、前端组件 `26/26`、在线 E2E `10/10`、离线 E2E `3/3`、固定 Eval `24/24`、
统一离线 Eval `429/429`，确定性安全违规为 `0`。订单、退款、Agent、Memory、RAG、权限和
持久化契约未扩大；这些实现与证据保留为历史基线，新的产品入口和交互契约由 v2.0 接续。

v2.0 “可演示、可评估的智能售后 Agent 产品” T1–T8 已完成。版本化演示数据、注册初始化、
完整工作区重置、单订单 Thread、双产品表面、旗舰退款链路和四层 Eval 已形成同一产品闭环。
V2.0 固定 Eval `36/36`，当前兼容历史 Suite + V2.0 为 `265/265`，安全违规均为 `0`；
真实 Provider 双轮资格 `23/24`，结构化输出有效率 `100%`，安全违规 `0`。完整证据见
[`docs/eval/v2.0-report.md`](./docs/eval/v2.0-report.md)。

v2.0.1 将一级 Agent 的 `unknown` 从系统异常改为跨轮次可恢复状态：最多发出两轮澄清问题，
第三次仍未知时使用确定性兜底并清零；明确意图会恢复既有业务路径。“退库”等退款/退货歧义
表达不会触发 Mock 退款。定向测试 `36/36`、全量后端测试 `349/349`，澄清路径业务工具和
未经审批退款调用均为 `0`。证据见
[`docs/eval/v2.0.1-report.md`](./docs/eval/v2.0.1-report.md)。

原 v1.4 多模态政策 Spec 已标记为 Superseded，其目标重新排入 v2.1。v2.0 不引入文件上传、
解析器、向量数据库、Embedding、Rerank 或多模态政策页面。

v0.1 已实现并验收：

- 从用户输入获得订单标识。
- 查询授权用户自己的订单和物流状态。
- 信息不足时继续同一会话补充订单号。
- 使用持久化 Checkpoint 隔离和恢复会话。
- 使用 Fake Model、Fake 业务适配器和固定场景验证结果。

v0.1 不包含退款、RAG、人工客服工单、动态 Agent Loop、Web UI、真实身份认证或 Java 微服务。

Feature Spec：[`docs/specs/v0.1-order-inquiry.md`](./docs/specs/v0.1-order-inquiry.md)。技术 Plan：[`docs/plans/v0.1-order-inquiry-plan.md`](./docs/plans/v0.1-order-inquiry-plan.md)。T1-T6 已完成；DeepSeek 真实意图识别冒烟通过，固定 Eval 15/15 场景通过。完整结果见 [`docs/eval/v0.1-report.md`](./docs/eval/v0.1-report.md)。

v0.2 已实现并验收：

- 受控 Markdown/JSON 政策语料和可重建 SQLite FTS5 索引。
- 中文 bigram、BM25、有效日期与区域过滤的确定性检索。
- 单来源、多证据、澄清、无证据拒答与冲突展示。
- 由规范化事实生成的逐项结论及服务端可定位引用。
- SQLite 跨进程政策上下文恢复和显式领域类型白名单。
- 用户与政策原文 Prompt Injection 防护、具体订单资格边界。
- DeepSeek 结构化 PolicyQuery 三条真实模型冒烟。
- 固定 RAG Eval `20/20`，v0.1 回归 `15/15`。

完整证据见 [`docs/eval/v0.2-report.md`](./docs/eval/v0.2-report.md)。

v0.3 已实现并验收：

- FastAPI 同源内部 API 与 React + TypeScript SPA。
- 游客 Session、只读 Fake 对话及零 LLM 调用。
- 邀请码注册、Argon2 凭证、可撤销 Session 和 CSRF/Origin 防护。
- 注册用户私有工作区、订单与物流 CRUD 和跨用户隔离。
- 服务端 LLM 授权、每日额度、模型失败不降级和最新业务数据查询。
- conversation 先授权后读取 Checkpoint，以及跨实例恢复和身份绑定。
- v0.3 固定 Eval `20/20`、真实浏览器 `2/2`、DeepSeek Web 冒烟 `3/3`。

Feature Spec：[`docs/specs/v0.3-web-accounts.md`](./docs/specs/v0.3-web-accounts.md)。
Technical Plan：[`docs/plans/v0.3-web-accounts-plan.md`](./docs/plans/v0.3-web-accounts-plan.md)。
完整证据：[`docs/eval/v0.3-report.md`](./docs/eval/v0.3-report.md)。

v0.4 已实现并验收：

- 本地业务库中的 Mock 支付、退款动作、退款和审计事实，以及整数分金额约束。
- 退款意图和原因提取；资格、金额、渠道、风险与审批要求由确定性代码产生。
- 带当前政策引用的 R2 退款预览和 LangGraph `interrupt` 暂停。
- 页面刷新及跨进程恢复、明确拒绝、批准前重新校验和过期预览阻断。
- 稳定 action 标识、SQLite 唯一约束、幂等执行和独立业务回读验证。
- 游客、跨用户、CSRF/Origin、参数篡改、失败、未知结果和并发安全边界。
- v0.4 固定 Eval `24/24`，未授权退款、重复退款和安全违规均为 `0`。

Feature Spec：[`docs/specs/v0.4-refund-approval.md`](./docs/specs/v0.4-refund-approval.md)。
Technical Plan：[`docs/plans/v0.4-refund-approval-plan.md`](./docs/plans/v0.4-refund-approval-plan.md)。
完整证据：[`docs/eval/v0.4-report.md`](./docs/eval/v0.4-report.md)。

v0.5 已实现并验收：

- 升级预览明确披露 AI 身份、工具和固定预算；取消和未确认均零 Case、零模型调用。
- 用户确认后幂等创建可恢复 L2 Case，由结构化单步决策驱动有界 Agent Loop。
- 固定 R0 Tool Registry 支持订单、物流、退款状态、政策和已确认偏好查询。
- 信息不足时使用 `interrupt` 暂停；页面或服务重启后可在同一 Case 补充并恢复。
- 长期偏好必须二次确认，并支持本人查看、受限纠正和删除。
- L2 退款候选完整复用 v0.4 的确定性预览、审批、幂等执行和回读验证。
- 公开 Case Trace 不包含 Prompt 或隐藏推理；固定 Eval `30/30`，安全违规为 `0`。

Feature Spec：[`docs/specs/v0.5-l2-support-harness.md`](./docs/specs/v0.5-l2-support-harness.md)。
Technical Plan：[`docs/plans/v0.5-l2-support-harness-plan.md`](./docs/plans/v0.5-l2-support-harness-plan.md)。
完整证据：[`docs/eval/v0.5-report.md`](./docs/eval/v0.5-report.md)。

v0.6 已实现并验收：

- 页面刷新、站内路由切换和重新登录后恢复本人公开聊天历史。
- 注册用户可以列出、新建、选择、归档和删除本人会话。
- Pending Refund、L2 Upgrade、L2 补充信息和 Memory Proposal 随原会话恢复。
- 用户消息、Agent Run、客户端重试和渐进输出具有稳定标识与去重语义。
- 公开消息、LangGraph Checkpoint、业务事实和长期记忆继续保持独立生命周期。
- 消息和四类 Pending Action 统一返回 `202 Accepted`，由进程内后台任务执行 Graph。
- SSE 只发送持久化、白名单投影的步骤事件，支持 `Last-Event-ID` 断线重放。
- Run 失败、进程中断、显式重试、旧会话 partial 标记和安全删除均有确定性语义。
- 固定 Eval `32/32`；重复消息、Run、事件、身份泄露、公开数据泄露和消息丢失均为 `0`。

Feature Spec：[`docs/specs/v0.6-conversation-lifecycle.md`](./docs/specs/v0.6-conversation-lifecycle.md)。
Technical Plan：[`docs/plans/v0.6-conversation-lifecycle-plan.md`](./docs/plans/v0.6-conversation-lifecycle-plan.md)。
Eval 证据：[`docs/eval/v0.6-report.md`](./docs/eval/v0.6-report.md)。
关联 ADR：[`docs/adr/0007-public-conversation-events-and-sse.md`](./docs/adr/0007-public-conversation-events-and-sse.md)。

v0.7 已实现并验收：

- 每次 L2 模型调用前从最多 100 条授权消息、当前 Observation 和已确认偏好中确定性构建
  Context Pack，保留当前目标、相关订单和早期关键信息，并受输入预算硬限制。
- Context Manifest 在 Provider 调用前幂等保存，只包含来源引用、版本、Disposition、计数与
  哈希；写入失败、Essential 缺失、过期、冲突或超预算时模型调用数为 0。
- 订单、物流、退款和政策事实通过现有 Gateway 只读重取；旧事实不会作为当前结论的唯一依据。
- 本人可以按 conversation 选择历史 Case，刷新后按 Case 内单调序号分页回放 Public Trace；
  Replay 不执行 Graph、模型、工具、记忆或 Mock 退款写入。
- Web 展示有限来源、刷新/裁剪提示和聚合指标；本地 CLI 提供不含正文的 Diagnostic Trace。
- 11 类失败归因由确定性代码产生；模型不可用与模型结构化输出无效具有不同公开语义。
- Alembic `0005` 原地迁移，旧 Case 显示 `partial`，损坏的新 Trace 安全降级为 `unavailable`。
- 固定 Eval `36/36`，必要上下文召回和任务正确率均为 `100%`，长上下文输入减少 `77.8%`；
  越权、过期事实结论、注入、Replay 副作用和公开 Trace 泄露均为 `0`。

Feature Spec：[`docs/specs/v0.7-context-observability.md`](./docs/specs/v0.7-context-observability.md)。
Completed Technical Plan：[`docs/plans/v0.7-context-observability-plan.md`](./docs/plans/v0.7-context-observability-plan.md)。
Eval 证据：[`docs/eval/v0.7-report.md`](./docs/eval/v0.7-report.md)。
Accepted ADR：[`docs/adr/0008-context-manifest-and-dual-trace.md`](./docs/adr/0008-context-manifest-and-dual-trace.md)。

v0.8 已实现并验收：

- 通过显式 Catalog 无损统一 v0.1–v0.8 的 217 条固定场景。
- Run Manifest、规范化 Artifact、源码/Fixture/依赖/门槛指纹和敏感字段扫描。
- 显式 Baseline 接受、取代关系，以及结果、轨迹、指标和安全 Candidate 比较。
- 40 条 Eval Harness 元场景与故障注入；安全违规不能被平均分抵消。
- 20 条合成真实 Provider 资格集连续运行两次，结果 `39/40`、结构化有效率 `97.5%`、
  工具准确率和证据召回均为 `100%`、安全违规为 `0`。
- 固定 Offline Release Gate `17/17`，统一离线 Eval `217/217`，安全违规为 `0`。

Accepted Feature Spec：
[`docs/specs/v0.8-agent-eval-hardening.md`](./docs/specs/v0.8-agent-eval-hardening.md)。
Completed Technical Plan：
[`docs/plans/v0.8-agent-eval-hardening-plan.md`](./docs/plans/v0.8-agent-eval-hardening-plan.md)。
Accepted ADR：
[`docs/adr/0009-versioned-eval-runs-and-dual-gates.md`](./docs/adr/0009-versioned-eval-runs-and-dual-gates.md)。
Eval 证据：[`docs/eval/v0.8-report.md`](./docs/eval/v0.8-report.md)。

v1.0 Accepted Feature Spec 已确认，目标是把现有本地开发项目收敛为可重复交付、可观察、可
备份恢复和可从 v0.8 安全升级的单机作品集版本。它不增加新售后业务，不承诺多实例、高可用、
生产 SLA 或真实外部系统。

Accepted Feature Spec：
[`docs/specs/v1.0-single-host-delivery.md`](./docs/specs/v1.0-single-host-delivery.md)。

Accepted Technical Plan：
[`docs/plans/v1.0-single-host-delivery-plan.md`](./docs/plans/v1.0-single-host-delivery-plan.md)。

Accepted ADR：
[`docs/adr/0010-single-host-compose-and-sqlite.md`](./docs/adr/0010-single-host-compose-and-sqlite.md)。

v1.0 T1–T8 已实现：

- 统一 `1.0.0` 版本、Release/Instance/Backup Manifest、严格部署配置和 Preflight。
- 单服务 Docker Compose、非 root/只读容器、回环端口与固定宿主运维入口。
- Live/Ready/Capability、本机诊断、单实例锁、优雅停机和启动 Reconciliation。
- 三类权威 SQLite 的停机 Backup、完整性校验、空目标/覆盖恢复和政策索引重建。
- 固定 v0.8 合成实例、升级前备份、失败保持非 Ready 和显式恢复证据。
- 脱敏 JSON 日志、request ID、有限运维审计和聚合计数。
- v1.0 新增 `32/32` 运维场景；统一离线 Eval `249/249`，安全违规为 `0`。
- 对话优先的商业化工作台、会话导航、处理上下文与回答依据、移动布局和 Reduced Motion 已完成，
  正常用户界面不再暴露 LLM/Fake/Provider 等内部模式术语。

当前 macOS 验收机未安装 Docker，真实镜像构建、Compose 启停和容器故障注入尚未取得参考环境
证据，因此版本保持 Candidate，不标记 Completed。详见
[`docs/eval/v1.0-report.md`](./docs/eval/v1.0-report.md)。

v1.1 已接受“订单优先的智能售后中心”方向和 Feature Spec：售后首页、订单详情和服务进度成为客户侧主要入口，
对话作为绑定订单或服务目标的上下文助手；正常客户界面不再以内部 Agent Trace、Provider 或 Eval
作为产品主体。该版本不扩展真实交易、外部系统或 Agent 权限。

v1.1 T1–T8 已实现并完成本机验收：

- 默认入口改为售后首页，提供订单列表、订单详情、服务列表和服务详情。
- Mock 订单支持可选最小商品行；旧订单以空商品行安全兼容。
- Conversation 可由服务端绑定已授权订单，省略订单号的提问使用可信绑定，冲突在模型前停止。
- Refund 与 L2 Case 通过只读投影形成客户服务时间线，不创建第二套业务事实。
- 桌面页内助手与移动原生对话框复用公开消息、SSE、审批和恢复链路。
- v1.1 固定 Eval `36/36`，统一离线 Eval `285/285`，安全违规为 `0`。
- 后端 `298 passed`，前端 `16 passed`，真实 HTTP E2E `4 passed`，离线 UI E2E `3 passed`。
- v1.0 数据升级至迁移 `20260722_0006` 后，旧订单和会话保持可读。

完整证据见 [`docs/eval/v1.1-report.md`](./docs/eval/v1.1-report.md)。参考 Docker Gate 尚未执行，
因此本实现不宣称达到 Completed 或可生产部署状态。

Accepted Feature Spec：
[`docs/specs/v1.1-post-purchase-service-center.md`](./docs/specs/v1.1-post-purchase-service-center.md)。

Accepted Technical Plan：
[`docs/plans/v1.1-post-purchase-service-center-plan.md`](./docs/plans/v1.1-post-purchase-service-center-plan.md)。

Accepted ADR：
[`docs/adr/0011-order-first-post-purchase-service-center.md`](./docs/adr/0011-order-first-post-purchase-service-center.md)。

## v1.2：客户售后中心与 Agent 运营控制台

v1.2 将客户售后中心与 Agent 运营控制台分离：客户侧继续围绕订单、服务进度、政策依据、审批和
公开对话；运营侧集中 Mock 业务管理、脱敏 Agent Monitoring、Eval 结果和有限系统状态。两个
表面继续复用同一个 FastAPI 模块化单体、React/Vite 工程、领域规则和权威存储。

v1.2 T1–T8 已实现并完成本机产品验收：

- `customer | admin` 服务端角色、Alembic `20260722_0007` 原地迁移与本机 CLI 授权。
- 客户侧 Mock 数据写接口关闭；管理员按显式目标客户维护订单、商品、物流和退款前支付。
- 邀请码一次性明文、有限客户目录和脱敏持久化后台审计。
- 真实 Agent Run/Event 的只读白名单 Monitoring，不读取完整消息或触发 Agent 副作用。
- Eval Artifact 四态、有限系统状态和运营概览只读投影；Web 不执行 Eval 或高影响运维。
- 同一个 React/FastAPI 模块化单体提供 CustomerLayout 与 AdminLayout 两个产品表面。
- v1.2 固定 Eval `40/40`，统一离线 Eval `325/325`，安全违规 `0`。
- 后端 `308 passed`，前端 `22 passed`，真实 HTTP E2E `6 passed`，离线 UI E2E `3 passed`。
- v1.1 数据升级到迁移 `20260722_0007` 后，既有账号、Session、订单和会话保持可读。

本机 Release Gate 为 `19/20`；唯一未完成项是当前机器没有 Docker，
`docker-compose-config` 按设计返回 `incomplete: docker_unavailable`。这与已接受的 Docker 延后
决定一致，不冒充参考部署已完成。

Accepted Feature Spec：
[`docs/specs/v1.2-customer-admin-surfaces.md`](./docs/specs/v1.2-customer-admin-surfaces.md)。

Completed Technical Plan：
[`docs/plans/v1.2-customer-admin-surfaces-plan.md`](./docs/plans/v1.2-customer-admin-surfaces-plan.md)。

Eval 与本机验收证据：
[`docs/eval/v1.2-report.md`](./docs/eval/v1.2-report.md)。

Accepted ADR：
[`docs/adr/0012-separate-customer-and-agent-operations-surfaces.md`](./docs/adr/0012-separate-customer-and-agent-operations-surfaces.md)。

## v1.3：商业化售后中心与智能服务体验（Candidate）

v1.3 以淘宝、Shopify、京东等成熟电商售后产品的共同信息结构和体验质量作为参考，提升商品、
订单、物流、服务和上下文 Agent 的统一表达。对标的是任务可发现性、订单真实感、状态完整性、
响应式/可访问性和售后闭环，不复制竞品品牌、视觉资产或全部业务功能。

接受范围包含版本化商品与图片资源、真实感订单场景、商业化客户页面、物流/政策/退款组合诉求、
结构化 Agent 方案和固定视觉/Agent Eval；不增加真实外部系统、退换货新业务类型、多 Agent、
运营故障处置或已延期的 Docker Gate。

v1.3 T1–T8 已实现并完成本机产品验收：

- 版本化目录包含 12 个 SPU、19 个 SKU、3 类画像、10 个场景和本地可追溯商品资源。
- 订单保存商品快照、金额与包裹履约明细；目录变化不会重写既有订单。
- 客户可以搜索、筛选并在售后首页、订单和服务页面获得一致的商品、状态、金额与下一步表达。
- 单 Agent 主图支持物流、政策和退款资格组合咨询，输出可恢复的结构化 `ServiceResolution`。
- Payload v1 保持可读，Payload v2 在刷新、切页和恢复后展示一致；未知版本安全降级为正文。
- v1.3 固定 Eval `48/48`，统一离线 Eval `373/373`，安全违规、未经审批退款和重复副作用均为 `0`。
- 真实 Provider Qualification 为 `10/24`、未达门槛，Docker 参考环境证据尚缺，因此不标记 Completed。

Accepted Feature Spec：
[`docs/specs/v1.3-commercial-service-experience.md`](./docs/specs/v1.3-commercial-service-experience.md)。

Completed Technical Plan：
[`docs/plans/v1.3-commercial-service-experience-plan.md`](./docs/plans/v1.3-commercial-service-experience-plan.md)。

Eval 与本机验收证据：
[`docs/eval/v1.3-report.md`](./docs/eval/v1.3-report.md)。

## v1.3.1：商业产品可信度重构（工程验收完成）

v1.3.1 不增加售后业务或 Agent 权限，而是修正 v1.3 “功能与截图通过但商业可信度仍不足”的
验收缺口。客户售后中心和 Agent 运营控制台将使用任务优先的信息层级、内容完整的显式演示工作区、
统一品牌/组件/状态语言以及固定产品评审量表，证明页面不再具有 Toy/Demo 感。

本版本同时补齐开源学习与面试证据：说明订单优先信息架构、双产品表面、业务事实与前端状态、
Agent 公开结果和多层质量门禁的设计取舍。自动化截图只用于回归，不再被当作视觉质量已经成熟的
充分证据。

Accepted Feature Spec：
[`docs/specs/v1.3.1-commercial-product-credibility.md`](./docs/specs/v1.3.1-commercial-product-credibility.md)。
Technical Plan：
[`docs/plans/v1.3.1-commercial-product-credibility-plan.md`](./docs/plans/v1.3.1-commercial-product-credibility-plan.md)。

Eval 与产品评审证据：
[`docs/eval/v1.3.1-report.md`](./docs/eval/v1.3.1-report.md)。

## v1.4：多模态政策知识与复杂问答（Superseded）

v1.4 拟把当前受控 Markdown/FTS5 政策问答扩展为可管理的多格式、多模态政策知识能力：管理员
导入并显式发布文本 PDF、扫描 PDF、政策图片和表格来源；客户及 AI 深度处理通过精确与向量
混合检索回答口语化、跨文档和多条件问题，并获得可定位到版本、页码、表格或图片区域的引用。

向量索引、OCR、解析结果和模型输出均为可重建派生数据，不能替代原始已发布政策、确定性
适用范围过滤或退款 Policy。该 Proposed Spec 已于 2026-07-24 被版本拆分取代，不创建 Plan
或实现代码；目标将在 v2.1 新 Feature Spec 中重新评审。

历史 Feature Spec：
[`docs/specs/v1.4-multimodal-policy-knowledge.md`](./docs/specs/v1.4-multimodal-policy-knowledge.md)。

## v2.0：可演示、可评估的智能售后 Agent 产品（Completed）

v2.0 删除游客业务体验，通过邀请码注册自动创建独立的 `portfolio-demo-v1` 工作区；客户从
成熟电商售后中心查看商品、订单和物流，并从右侧悬浮 Agent 进入单订单 Thread。旗舰旅程覆盖
物流查询、政策 RAG、确定性退款 Policy、客户确认、幂等 Mock 退款和结果验证。

本版本同时收紧长期 Memory、二线 Agent Loop、运营控制台和四层 Eval 边界。管理员不再手工
编辑单笔订单；产品不把 LLM 包装成人工客服；Fake Model 仅存在于测试与 Eval。向量数据库、
多格式解析和多模态政策证据不进入 v2.0。

Accepted / Implemented Feature Spec：
[`docs/specs/v2.0-interview-ready-agent-product.md`](./docs/specs/v2.0-interview-ready-agent-product.md)。

Completed Technical Plan：
[`docs/plans/v2.0-interview-ready-agent-product-plan.md`](./docs/plans/v2.0-interview-ready-agent-product-plan.md)。

Eval 与本机验收证据：
[`docs/eval/v2.0-report.md`](./docs/eval/v2.0-report.md)。

## v2.0.1：意图澄清循环（Completed）

一级 Agent 无法可靠分类时，在同一订单 Thread 中最多发出两轮澄清问题；每条补充重新经过
结构化解释，成功后进入既有订单、物流、政策、退款或 AI 深度处理路径。第三次仍无法识别时
返回兜底并清空次数。澄清不调用业务工具，也不使用 `interrupt()` 或新增数据库表。

Feature Spec：
[`docs/specs/v2.0.1-intent-clarification.md`](./docs/specs/v2.0.1-intent-clarification.md)。

Completed Technical Plan：
[`docs/plans/v2.0.1-intent-clarification-plan.md`](./docs/plans/v2.0.1-intent-clarification-plan.md)。

验收证据：
[`docs/eval/v2.0.1-report.md`](./docs/eval/v2.0.1-report.md)。

## v2.1：多格式、多模态政策知识与混合检索（路线）

v2.1 将在 v2.0 产品闭环稳定后重新定义向量数据库、精确/语义混合检索、多格式解析、OCR、
Rerank、多模态证据与复杂问答 Eval。当前只保留路线，不创建 Feature Spec、Plan 或代码。

## 长期成功指标

- 售后任务端到端完成率。
- 工具选择与参数正确率。
- 订单和政策事实准确率。
- 多模态/复杂政策问题的必要证据召回率与引用可解析率。
- 未授权数据访问及模拟资金操作数量，目标为 `0`。
- 未登录访问者触发业务工具、会话创建或 LLM 调用数量，目标为 `0`。
- 重试或恢复造成的重复副作用数量，目标为 `0`。
- 刷新、切页或重新登录造成的公开消息丢失数量，目标为 `0`。
- 重复提交或断线恢复产生的重复公开消息和 Agent Run 数量，目标为 `0`。
- 复杂售后 Case 的 L2 升级正确率和解决率。
- P95 延迟、平均模型成本和平均步骤数。
- 固定产品评审量表中的商业可信度得分。
- 核心页面中的开发占位内容、内部术语和无许可资源数量，目标为 `0`。
- 开源评审者从 README 完成初始化和四条核心旅程的成功率。

## 文档入口

- 开发流程：[`AGENTS.md`](./AGENTS.md)
- 系统约束：[`docs/specs/system-constraints.md`](./docs/specs/system-constraints.md)
- Feature Specs：[`docs/specs/`](./docs/specs/)
- 技术 Plans：[`docs/plans/`](./docs/plans/)
- 架构决策：[`docs/adr/`](./docs/adr/)

只有长期方向、版本顺序、当前版本或状态变化时才更新本地图。用户行为进入 Spec，技术实现进入 Plan，长期选择进入 ADR。
