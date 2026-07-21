# ADR-0002：v0.1 使用 SQLite Checkpointer

状态：Accepted  
日期：2026-07-15

## 背景

v0.1 要求用户在缺少订单号时暂停对话，并在程序退出后使用相同 `thread_id` 继续。内存 Checkpointer 无法跨进程保存 State，而直接引入 PostgreSQL 会增加本地开发和测试成本。

## 决定

- v0.1 使用 SQLite 实现 LangGraph Checkpointer。
- Checkpoint 数据库只保存 Task/Conversation State，不作为订单、物流或退款事实数据库。
- CLI 必须使用稳定的 `thread_id`；不同用户和 thread 必须隔离。
- 测试使用临时 SQLite 文件，并通过关闭、重新打开连接模拟进程重启。
- SQLite 文件属于运行产物，不提交 Git。

## 备选方案

- `InMemorySaver`：适合单元测试，但不能满足跨进程恢复验收。
- PostgreSQL Checkpointer：更适合并发和生产部署，但对 v0.1 本地单机范围过重。
- 自定义持久化：不能提供超过官方 Checkpointer 的当前价值，且会扩大测试与迁移成本。

## 结果与代价

- 本地环境无需启动额外数据库服务，端到端恢复测试简单。
- SQLite 只适合当前单机开发，不承诺多实例并发或生产可用性。
- State Schema、节点名称或 Checkpoint 格式变化时仍需考虑迁移。
- 进入生产化版本前必须根据并发、可靠性和运维需求重新评估 PostgreSQL。

## 参考

- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
