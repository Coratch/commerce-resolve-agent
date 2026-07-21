# ADR-0004：采用同源 FastAPI 与 React Web 架构

状态：Accepted  
日期：2026-07-17

## 背景

v0.3 要让游客通过 Web 使用只读 Fake 演示，并让受邀用户在私有工作区维护订单与物流、
使用服务端授权的 LLM。后续版本还会出现退款审批、任务暂停与恢复、人工转接、Agent
Loop 状态和 Eval 展示。

浏览器是不可信边界。身份、工作区、模型权限和业务规则必须由服务端决定；同时，项目
需要在不过早引入微服务和生产基础设施的前提下，为后续交互保留可维护的前端结构。

## 决定

- 后端采用 FastAPI 模块化单体和 Uvicorn 单 worker，提供同源内部 JSON API。
- 前端采用 React + TypeScript + Vite；React Router 管理页面，TanStack Query 只管理
  服务端状态，CSS Modules 负责样式。
- 开发环境由 Vite 将 `/api` 代理到 FastAPI；交付时 FastAPI 同源托管 Vite 构建的
  `frontend/dist`，不开放 credentialed CORS。
- 浏览器认证使用服务端不透明 Session Cookie；Session 可撤销、过期和轮换，不使用 JWT
  或 localStorage Token。
- 所有状态变更请求使用服务端同步 CSRF Token 与 Origin 校验；身份、工作区和模型模式
  不接受客户端直接指定。
- 业务关系数据使用独立 `business.sqlite`、SQLAlchemy 2.0 同步 ORM 和 Alembic 显式迁移。
  业务数据库不与 LangGraph Checkpoint 或政策 FTS5 索引合并。
- v0.3 使用普通 HTTP 请求/响应，不提前引入 SSE、WebSocket、Redux、Next.js、异步 ORM、
  PostgreSQL、Redis 或多实例部署。

## 理由

- FastAPI 可以复用现有 Pydantic 契约、同步 LangGraph 运行路径和 Python 测试装配。
- React + TypeScript 能承载后续审批、恢复和任务状态交互，并在浏览器侧检查 API 数据类型。
- 同源交付减少 Cookie、CSRF、CORS 和部署边界，不需要第二个应用服务端。
- 服务端 Session 符合当前单体浏览器产品的撤销、退出和账号停用需求；JWT 不产生当前价值。
- SQLAlchemy 提供关系映射和事务边界，Alembic 使持续演进的业务 Schema 可审查、可测试。
- SQLite 满足本地单实例学习范围，同时保持与 Checkpoint、政策索引不同的生命周期。

## 备选方案

- Jinja2 + 原生 JavaScript：v0.3 初始成本更低，但后续审批、Handoff 和 Agent 状态会增加
  分散脚本并产生前端整体迁移成本。
- Next.js：提供 SSR 和全栈 React，但当前不需要 SEO 或第二个服务端，会增加认证转发与部署边界。
- JWT：适合无状态跨服务或第三方 API，但当前需要立即撤销、退出和账号停用。
- 原始 `sqlite3` + 启动时 `create_all`：依赖更少，但关系数据和长期迁移缺少明确契约。
- AsyncSession 或 PostgreSQL：并发能力更强，但 v0.3 单实例没有数据证明需要承担这些成本。

## 结果与代价

- 项目增加 Node/npm 构建链、前端测试和浏览器端到端测试，需要同时维护 Python 与
  TypeScript 依赖锁。
- FastAPI OpenAPI Schema 将用于生成 TypeScript 类型，但服务端 Pydantic 校验和授权仍是
  运行时事实来源。
- 单 worker 和 SQLite 不承诺生产多实例能力；需要扩展时必须根据性能和部署证据新增 ADR。
- 前端不能绕过 Session、CSRF、Access Policy、Repository Scope 或 LangGraph 主图。
- v0.3 只实现已接受 Plan 的最小 SPA，不借机提前开发流式输出或复杂全局状态。

## 关联文档

- [`../specs/v0.3-web-accounts.md`](../specs/v0.3-web-accounts.md)
- [`../plans/v0.3-web-accounts-plan.md`](../plans/v0.3-web-accounts-plan.md)
- [`../specs/system-constraints.md`](../specs/system-constraints.md)
- [`../../PROJECT_GOAL_MAP.md`](../../PROJECT_GOAL_MAP.md)
