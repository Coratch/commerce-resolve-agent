# ADR-0008：使用元数据型 Context Manifest 与双层 Trace

状态：Accepted  
日期：2026-07-21  
接受日期：2026-07-21  
关联版本：v0.7

## 背景

v0.5 的 L2 Harness 已保存 Case、模型计量和脱敏公开事件，v0.6 又增加了公开消息、Run/Event
和可重放 SSE。但每次 L2 模型调用仍按固定窗口拼装上下文，没有稳定记录候选来源、选择结果、
时效、冲突、裁剪和预算。出现错误时，无法区分“模型判断错误”和“必要上下文没有进入请求”。

直接持久化完整 Prompt 或原始 ToolMessage 虽然便于调试，却会复制用户消息、订单、政策、
长期偏好和工具正文，扩大敏感数据面，并可能暴露隐藏推理。只保留现有公开事件又不足以支持
上下文 Eval 和失败归因。

因此需要决定 Context Pack、诊断数据、公开 Trace 和 Replay 的长期边界。

## 决策

### Context Pack 只在单次调用内存在

- 每次 L2 模型调用前，由确定性代码构建严格 Schema 的 `L2ContextPack`。
- Context Pack 只保留在当前 `l2_decide` 调用内，不写入 LangGraph State、Checkpoint、业务库、
  日志或浏览器。
- Model Adapter 只接收 Context Pack 的最小 Provider 投影；身份原值、内部选择分数和排除候选
  不发送给云模型。
- 恢复任务时重新读取当前授权来源并构建新的 Context Pack，不复用旧 Prompt。

### 持久化元数据型 Context Manifest

- 每次实际模型调用必须先生成并幂等保存一个 `L2ContextManifest`。
- Manifest 保存稳定来源引用、来源版本或内容摘要、时效状态、选择结果、排除原因、计数、
  确定性大小、预算、冲突、截断、策略版本和 Pack 哈希。
- Manifest 不保存完整 Prompt、消息正文、Observation 正文、政策正文、密钥、Cookie、支付凭据
  或隐藏 Chain-of-thought。
- Manifest 写入业务数据库中的独立 `l2_context_manifests` 表，不进入 Checkpoint，也不与可重建
  的政策索引或长期记忆 Store 混用。
- Manifest 按 `case_id + step_id` 幂等；保存或校验失败时不得调用模型。

### Public Trace 与 Diagnostic Trace 分层

- `l2_case_events` 继续作为用户可见 Public Trace 的事实源，并增加稳定 Case 内序号、事件协议
  版本和有限上下文摘要。
- Public Trace 只显示来源类别、公开证据、数量、截断、阶段、结果、耗时和停止原因。
- Diagnostic Trace 从 Context Manifest、模型计量和公开事件聚合，只面向隔离测试、固定 Eval
  和显式本地诊断；它可以显示排除原因和脱敏来源引用，但仍不显示完整 Prompt 或隐藏推理。
- Web API 不提供任意 Diagnostic Trace 管理入口，避免把“调试模式”变成越权读取真实数据的
  后门。

### Replay 永远只读

- Replay 只按稳定序号读取已持久化 Public Trace 和公开指标。
- 刷新、分页、重新登录或 SSE 重连不得调用 LangGraph、模型、工具、长期记忆、退款或任何
  写 Repository。
- v0.7 之前的 Case 没有 Context Manifest 时标记 `partial`；不从 Checkpoint 或模型补造历史。
- v0.7 不引入外部 Trace 平台、向量数据库、消息队列或第二套事件存储。

## 备选方案

### 保存完整 Prompt 和模型响应

调试直接，但会复制敏感业务数据、扩大删除和访问控制范围，并违反“不保存隐藏推理和完整
Prompt”的产品边界，因此拒绝。

### 只扩展现有公开事件参数

实现最少，但现有参数摘要无法表达逐候选选择、时效、冲突、预算和排除原因，也无法稳定
支持 36 条 Context Eval，因此拒绝。

### 直接接入 LangSmith、Langfuse 或 OpenTelemetry

成熟工具可以提供 Trace UI 和聚合，但会引入外部数据传输、额外部署与产品耦合；当前版本
首先需要证明数据契约和本地 Eval，而非搭建生产观测平台，因此延后。

### 把 Context Manifest 写入 LangGraph State

恢复方便，但会增大每个 Checkpoint、复制可重建诊断数据，并把 State Schema 与观测存储强
耦合，因此拒绝。State 只保存最后的稳定 Manifest 引用和策略版本（如实现确有需要），不保存
完整 Manifest。

## 后果

正向结果：

- 可以确定性回答“模型这一步使用了哪些来源，以及为什么排除其他候选”。
- Public Trace、Diagnostic Trace 和 Eval 共享同一持久事实，但不会向用户暴露内部细节。
- Context 策略可以按版本对比，旧 Case 可以诚实降级为 `partial`。
- Replay 可通过 Repository 只读测试证明零副作用。
- 未来如接入外部观测平台，可以从经过脱敏的结构化事件投影，不必导出完整 Prompt。

代价：

- 需要一次 Alembic 增量迁移、严格 Manifest Schema、幂等 Repository 和旧 Case 回填语义。
- 每次模型调用前多一次小型本地事务；Manifest 写入失败会主动阻断模型调用。
- 完整问题复现仍需固定 Fake 数据和版本化 Eval；生产用户数据不能直接导出为调试样本。
- Diagnostic Trace 不能回答模型隐藏思考过程，只能定位输入、动作、结果和失败层级。这是有意
  的安全边界。

## 接受条件

本 ADR 与 v0.7 Technical Plan 一起审核。只有以下约束被接受后才改为 `Accepted`：

- Context Pack 不持久化。
- Context Manifest 仅保存元数据与稳定引用。
- Public/Diagnostic Trace 分层，均不保存完整 Prompt 或隐藏推理。
- Replay 是经过零副作用测试的 Repository 只读投影。
- 旧 Case 明确显示 `partial`，不补造历史。

## 关联文档

- [`../specs/v0.7-context-observability.md`](../specs/v0.7-context-observability.md)
- [`../specs/system-constraints.md`](../specs/system-constraints.md)
- [`../plans/v0.7-context-observability-plan.md`](../plans/v0.7-context-observability-plan.md)
- [`0006-llm-l2-support-harness.md`](0006-llm-l2-support-harness.md)
- [`0007-public-conversation-events-and-sse.md`](0007-public-conversation-events-and-sse.md)
