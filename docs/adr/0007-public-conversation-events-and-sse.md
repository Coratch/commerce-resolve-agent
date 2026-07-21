# ADR-0007：分离公开会话事件并使用可重放 SSE

状态：Accepted  
接受日期：2026-07-21  
日期：2026-07-21

## 背景

v0.3–v0.5 的 React 页面只在组件内存中保存公开消息；LangGraph Checkpoint 能恢复内部 State
和中断，但不是适合列表、分页、公开投影和删除的产品消息模型。当前同步 HTTP 还把 Graph
执行与浏览器请求绑定，无法在刷新或短暂断线后恢复正在产生的公开进度。

v0.6 需要同时满足：

- 公开历史与内部 Checkpoint 分离。
- 消息提交、Run 和客户端重试幂等。
- 浏览器能在最终结果前看到服务端确认的进度。
- 断线后只补发缺失内容。
- 仍保持本地单实例和 SQLite，不提前引入生产队列或多实例广播。

一线 Interpreter 与 L2 Agent 主要产生结构化 JSON 决策。原始 Provider Token 在完整 Schema、
Policy 和业务验证前不具备公开资格，因此不能直接作为用户答案传输。

## 决定

- 在业务数据库中新增独立的公开 Conversation Message、Agent Run 和 Run Event 模型。
- LangGraph Checkpointer 继续只保存内部 Agent State、中断和恢复点，不能直接作为历史 API。
- 每个用户提交先在业务库原子写入消息与 Run，再返回 `202 Accepted`。
- 本地单实例通过 FastAPI/Starlette 进程内 BackgroundTask 执行 Graph；执行不依赖 SSE 连接
  存活。
- 使用 SSE 传输已经持久化、经过白名单投影的步骤进度、待处理动作和最终消息。
- Run Event 使用单调事件 ID；浏览器通过 `Last-Event-ID` 重放缺失事件。
- SSE 不直接传输结构化模型的 Provider 原始 Token、Prompt、隐藏推理、完整 State 或原始
  ToolMessage。
- 进程重启时，遗留的 active Run 标记为 interrupted；不自动重放可能已发生的模型调用或
  业务动作。Pending Action 继续由 Checkpoint 恢复。
- 当前实现继续只承诺单实例、单 Uvicorn worker。多实例与生产队列需要新的 ADR。

## 备选方案

### 直接从 Checkpoint 查询历史

实现最少，但会耦合内部 Schema，难以安全分页和删除，也可能暴露 ToolMessage、Prompt 或
内部路由字段，因此拒绝。

### 同步 HTTP + 前端本地打字机

只能在完整结果返回后切割字符串，无法证明服务端仍在处理，也不能在断线后重放，因此不
满足 v0.6。

### WebSocket

可以双向通信，但当前用户输入和审批都适合独立幂等 POST；WebSocket 会增加协议、鉴权、
心跳和重连状态，没有额外必要价值，因此暂不采用。

### Redis/消息队列 + Worker

更适合多实例与进程崩溃恢复，但会显著扩大部署、任务租约、重复消费和测试范围。当前本地
单实例尚未证明需要，推迟到生产化版本。

### Provider Token 直传

可以提供细粒度视觉效果，但当前 Token 是尚未验证的 JSON 决策或候选答案，可能绕过
Schema、Policy 和事实验证，因此拒绝。

## 结果与代价

- 刷新、切页、重新登录和短暂断线不再依赖 React 内存。
- 历史 API 只读取公开投影，Checkpoint 演进不会直接成为前端协议。
- SSE 断线可以确定性重放，Graph 也不会因浏览器连接关闭而立即取消。
- 公开事件和最终消息可以通过固定数据集进行 Eval。
- 需要维护 Conversation/Run/Event Schema、投影器和跨 SQLite 的一致性检测。
- 进程内任务不能在服务崩溃后继续正在进行的模型调用；只能标记中断并安全重试。
- Provider Token 级视觉连续性被有意推迟，v0.6 以真实节点进度和验证后的最终回复为准。

## 关联文档

- [`../specs/v0.6-conversation-lifecycle.md`](../specs/v0.6-conversation-lifecycle.md)
- [`../plans/v0.6-conversation-lifecycle-plan.md`](../plans/v0.6-conversation-lifecycle-plan.md)
- [`../specs/system-constraints.md`](../specs/system-constraints.md)
