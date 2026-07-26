# ADR-0012：分离客户售后中心与 Agent 运营控制台并保持模块化单体

状态：Accepted  
日期：2026-07-22  
接受日期：2026-07-22  
关联版本：v1.2

## 背景

v1.1 已将客户产品调整为订单优先的智能售后中心，客户从订单、物流和服务记录进入售后任务，
Agent 作为绑定可信上下文的辅助能力。项目同时已经具备 Mock 订单维护、邀请管理、公开 Trace、
Agent Run、Eval Artifact、Baseline、健康诊断和本机运维能力。

如果继续把客户任务、演示数据管理和 Agent 工程诊断放在同一套页面结构中，客户会看到与解决售后
问题无关的内部概念，维护者也缺少围绕运行质量、Eval 回归和 Mock 数据准备组织工作的稳定入口。
但当前仍是 SQLite、单 Worker、单机部署和单人维护的作品集项目，没有独立团队、扩缩容或安全域
证据支持拆分微服务。

因此需要同时决定产品表面的职责边界和后端部署边界。

## 决策

### 分离两个产品表面

- 客户售后中心负责订单、物流、服务进度、政策依据、上下文助手、审批、恢复和本人反馈。
- Agent 运营控制台负责项目内 Mock 业务数据、邀请与演示账号、政策与索引状态、脱敏 Agent Run、
  Monitoring、Eval 结果和有限系统状态。
- 客户页面不展示 Provider、Token、完整 Prompt、内部节点、Context Manifest、Eval 指标或其他
  用户数据。
- 运营控制台不是客服坐席系统，也不冒充真实商家后台；具体可读和可写能力必须由 v1.2 Feature
  Spec 逐项接受。

### 保持一个模块化单体后端

- 后端继续使用一个 FastAPI 应用、一个部署单元和既有领域/Repository/Policy/LangGraph 核心。
- 客户 API 与运营 API 使用独立路由命名空间和依赖装配，但不复制业务规则或建立第二套业务事实。
- 两个表面继续采用同源 Session、CSRF、Origin 和服务端授权；前端隐藏菜单不能代替后台权限。
- 当前 React + TypeScript + Vite 工程使用独立 Layout、路由组和按需加载组织两个表面，不增加第二个
  前端服务端或 credentialed CORS。
- Checkpoint、业务数据库、Memory、政策索引、Audit/Eval 继续保持既有生命周期分离，不因页面
  拆分合并或复制。

### 区分 Monitoring、Eval 与运维

- Agent Monitoring 展示真实 Run 的脱敏状态、步骤、停止原因、工具类别、延迟、用量和失败归因。
- Agent Eval 展示固定数据集、Candidate、Baseline、回归差异和安全门禁，不把真实客户会话直接
  当作可发布 Eval 样本。
- Backup、Restore、升级、完整 Eval 执行和其他高影响动作继续使用固定 CLI；运营控制台初期只读
  展示结果，不提供任意 Shell 或无界后台任务。

## 理由

- 客户和维护者完成的是两类不同任务，应拥有不同导航、术语和信息密度。
- 共享领域核心可以保证订单、退款、Policy、审批和 Eval 使用同一事实与规则。
- 单体部署符合当前 SQLite、单 Worker 和单机交付边界，避免为页面拆分引入分布式一致性、服务认证、
  队列和多套部署生命周期。
- 独立路由与服务端权限为未来拆分保留清晰接缝，但不提前支付微服务成本。

## 备选方案

### 继续把所有能力放在客户页面

实现成本最低，但会重新暴露内部 Agent 概念，让订单管理、Eval 和监控入口与客户售后任务争夺主
导航，违背 ADR-0011 的客户结果优先原则。

### 立即拆成两个后端服务

可以形成更强的运行隔离，但当前没有独立团队、扩缩容、合规边界或性能证据；反而需要处理跨服务
身份、事务、API 版本、部署和 Trace，无法改善当前版本的用户结果。

### 创建完全独立的第二套前端仓库

可以独立发布，但会复制设计系统、OpenAPI 类型、认证和构建链。当前同一 React 工程内的独立
Layout 与路由组已经能够提供产品分离；出现独立团队或发布节奏后再重新评估。

## 后果

正向结果：

- 客户售后中心可以继续围绕订单、服务和下一步动作保持低认知负担。
- 维护者可以集中查看 Mock 数据、Agent 运行质量和 Eval 证据，不通过客户页面排障。
- 前后台权限可以形成明确、可测试的服务端矩阵。
- 后端业务规则、数据和部署方式保持单一，当前运维与测试资产可以继续复用。

代价与限制：

- 同一前端工程需要维护两套 Layout、导航和路由权限测试。
- 后台能力必须逐项设计脱敏和权限，不能把现有 CLI 或内部对象直接暴露为 Web API。
- 同一进程仍共享 CPU、内存和故障域；完整 Eval 不适合由 Web 请求同步执行。
- 本 ADR 只确定长期产品与部署边界；v1.2 的具体角色、用户故事、范围和写操作以已接受的
  Feature Spec 为准，不能从本 ADR 推导额外后台权限。

## 重新评估条件

只有出现以下证据时才评估拆分服务或独立前端交付：

- 在线请求与 Eval/分析任务产生可复现的资源竞争。
- 客户与运营能力由独立团队维护并需要不同发布节奏。
- PostgreSQL、后台 Worker 或任务队列已经由独立 Spec/ADR 接受。
- 法规、安全域或租户隔离要求必须使用进程或网络边界。
- 某一模块需要独立扩缩容、故障隔离或外部 API 生命周期。

## 关联文档

- [`0011-order-first-post-purchase-service-center.md`](0011-order-first-post-purchase-service-center.md)
- [`0004-same-origin-web-architecture.md`](0004-same-origin-web-architecture.md)
- [`../specs/v1.2-customer-admin-surfaces.md`](../specs/v1.2-customer-admin-surfaces.md)
- [`../specs/system-constraints.md`](../specs/system-constraints.md)
- [`../../PROJECT_GOAL_MAP.md`](../../PROJECT_GOAL_MAP.md)
