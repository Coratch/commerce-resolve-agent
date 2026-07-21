# CommerceResolve 项目目标地图

本文件是长期产品方向、版本顺序和当前焦点的唯一入口。全局业务与安全边界见 [`docs/specs/system-constraints.md`](./docs/specs/system-constraints.md)。

## 北极星

### 产品目标

构建一个可审计的电商售后客服 Agent Web 服务：游客可以使用只读演示数据和确定性 Fake 模式体验对话；受邀注册用户可以在私有演示工作区维护自定义订单与物流数据，并使用 LLM 完成售后对话。公开会话可以跨页面和登录恢复，系统支持 Mock 退款审批，以及由受控 Harness 驱动、明确披露为 AI 的二线客服 Agent，并在项目内持久化业务状态、确定性 Policy 和权限约束下验证最终结果。

项目只用于学习和实践 Agent 工程。订单、物流、支付、退款和 L2 Support Case 均使用本地 Mock/Fake 数据与适配器，不接入真实电商、客服、支付、物流或交易系统，也不产生真实资金和外部业务副作用。

Mock 描述的是数据来源和业务边界，不限制存储方式。Web 场景的 Mock 业务状态可以持久化到本地业务数据库，测试场景可以使用内存 Fake 或临时数据库；两者都不能与 LangGraph Checkpoint 混用。

产品成功不是“模型给出了听起来合理的回复”，而是售后问题得到正确、安全、可追溯的处理。

### 学习目标

以接近真实售后约束的 Mock 业务场景为载体，逐步实践：

- LangGraph State、Reducer、Node、Edge、Command、Checkpoint、Streaming 与 Human-in-the-loop。
- 模型工具调用、结构化输出和有界 Agent Loop。
- 短期会话记忆、业务状态和长期记忆的分层管理。
- 售后政策 RAG、引用、检索评估与失败分析。
- 确定性 Policy、退款审批、幂等执行和结果验证。
- Agent Eval、Tracing、成本、延迟和安全指标。

每项技术只有在解决当前版本的可验收问题时才进入项目。

## 第一性原理

1. 用户需要的是问题被解决，而不是进行一次聊天。
2. 项目内 Mock 订单、物流和退款 Repository 是业务事实来源，模型不能编造业务状态。
3. LLM 是候选动作生成器，不是模拟资金操作的授权主体。
4. 查询、决策、审批、执行和验证必须是可观察的独立步骤。
5. 写操作必须幂等；任务恢复不能造成重复退款、Case 或记忆写入。
6. 无法由一线流程安全处理时，可以升级到受控 AI 二线客服，但不能冒充真人。
7. Agent 质量必须通过固定场景和业务结果衡量。
8. 身份、业务数据归属和 LLM 使用权是独立权限，必须由服务端确定，不能由客户端或模型自报。

由此得到长期链路：

```text
用户请求
→ 识别诉求与订单
→ 查询订单/物流或检索政策
→ 确定性 Policy
→ 必要时人工审批
→ 幂等执行
→ 回读 Mock 业务状态验证
→ 回复或升级至 AI 二线客服
→ Checkpoint、Audit 与 Eval
```

## 版本路线

| 版本 | 用户结果 | 核心实践 | 状态 |
|---|---|---|---|
| v0.1 | 查询本人订单和物流，并可跨进程继续会话 | State、只读工具、SQLite Checkpoint、基础 Eval | **Completed（2026-07-17）** |
| v0.2 | 根据售后政策回答并提供可定位来源 | 文档建模、RAG、引用、检索 Eval | **Completed（2026-07-17）** |
| v0.3 | 通过 Web 对话；游客使用只读 Fake 演示，受邀用户维护私有订单与物流并使用 LLM | Web、邀请注册、身份隔离、私有业务数据、模型授权 | **Completed（2026-07-17）** |
| v0.4 | 生成 Mock 退款预览，经审批后安全执行模拟退款 | Policy、interrupt、幂等键、执行后验证 | **Completed（2026-07-19）** |
| v0.5 | 复杂售后问题升级至受控 AI 二线客服并可恢复处理 | L2 Agent Harness、有界 Loop、Tool Registry、工作/长期记忆 | **Completed（2026-07-20）** |
| v0.6 | 刷新、切页和重新登录后恢复公开历史，并管理多个会话和进行中交互 | 会话生命周期、公开消息、幂等、渐进输出、断线恢复 | **Completed（2026-07-21）** |
| v0.7 | 提升 L2 Agent 的上下文选择、轨迹回放与可观测性 | Context Engineering、Trace/Replay、失败归因、Harness 指标 | **Completed（2026-07-21）** |
| v0.8 | 用固定数据集衡量正确性、安全性和成本并完成产品加固 | Agent Eval、Tracing、回归门禁 | 后续 |
| v1.0 | 提供可部署、可维护的售后 Agent 服务 | API、持久化升级、观测、部署和运维 | 后续 |

顺序依据：先建立可靠的只读事实查询和知识检索，再建立 Web 身份、数据归属和模型权限边界；这些基础完成后才引入资金写操作和 Agent Loop。Agent Loop 完成后先补齐用户可见会话连续性，再优化上下文和轨迹。Eval 从 v0.1 开始积累，而不是到 v0.8 才补测试。

## 当前状态：v0.7 已完成，v0.8 尚未开始

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

v0.8 只保留路线占位，Feature Spec 尚未创建。讨论并接受 v0.8 Spec 前，不实现新的 Eval
平台、外部 Tracing、部署或产品加固能力。

## 长期成功指标

- 售后任务端到端完成率。
- 工具选择与参数正确率。
- 订单和政策事实准确率。
- 未授权数据访问及模拟资金操作数量，目标为 `0`。
- 游客触发 LLM 调用及跨用户数据读取数量，目标为 `0`。
- 重试或恢复造成的重复副作用数量，目标为 `0`。
- 刷新、切页或重新登录造成的公开消息丢失数量，目标为 `0`。
- 重复提交或断线恢复产生的重复公开消息和 Agent Run 数量，目标为 `0`。
- 复杂售后 Case 的 L2 升级正确率和解决率。
- P95 延迟、平均模型成本和平均步骤数。

## 文档入口

- 开发流程：[`AGENTS.md`](./AGENTS.md)
- 系统约束：[`docs/specs/system-constraints.md`](./docs/specs/system-constraints.md)
- Feature Specs：[`docs/specs/`](./docs/specs/)
- 技术 Plans：[`docs/plans/`](./docs/plans/)
- 架构决策：[`docs/adr/`](./docs/adr/)

只有长期方向、版本顺序、当前版本或状态变化时才更新本地图。用户行为进入 Spec，技术实现进入 Plan，长期选择进入 ADR。
