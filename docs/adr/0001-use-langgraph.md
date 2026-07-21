# ADR-0001：使用 LangGraph 作为 Agent 编排运行时

状态：Accepted  
日期：2026-07-15

## 背景

CommerceResolve 后续需要表达确定性订单查询、售后政策检索、退款审批、跨进程恢复、人工介入以及有限步骤的 Agent Loop。资金相关流程还要求检查执行前后的 State，并能审计具体路径。

## 决定

- 使用 LangGraph 作为核心编排运行时。
- 显式定义 State、节点、边、停止状态和持久化边界。
- 固定业务流程优先使用确定性图；只有确实需要动态工具选择时才引入 Agent Loop。
- 业务适配器、Policy 和工具契约保持普通 Python，实现不依赖 LangGraph，便于独立测试和替换。
- v0.1 使用单图，不引入多 Agent 或子图。

## 理由

- 持久化 Checkpoint 支持跨调用和跨进程恢复。
- `interrupt` 与 `Command` 可以表达退款审批和恢复。
- 显式 State 和路由便于测试、审计和失败归因。
- Streaming 可以向后续 CLI、API 或 UI 暴露执行进度。
- 框架可以独立于具体模型供应商使用。

## 备选方案

- OpenAI Agents SDK：标准工具循环、Handoff、Guardrail 和 Tracing 更简洁，但当前项目更重视显式状态、确定性路由和可恢复审批。
- Google ADK：支持图工作流和多语言，Java 生态有吸引力，但项目当前以 Python 学习 LangGraph 为明确目标。
- Pydantic AI：类型契约和测试体验良好，但项目希望直接实践 LangGraph 的持久执行语义。
- 自建 Agent Loop：依赖最少，但需要自行实现 Checkpoint、恢复、人工审批和状态历史，不能产生额外产品价值。

## 结果与代价

- 团队需要维护 State Schema、节点和路由，代码量会高于高层 Agent SDK。
- State、节点名称和 Checkpoint 成为需要兼容管理的接口。
- LangGraph 不负责业务规则、数据访问或退款安全，这些仍由确定性组件承担。
- 如果未来更换运行时，需要迁移持久化 State 和中断恢复语义。

## 参考

- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
