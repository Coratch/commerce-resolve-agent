# ADR-0006：使用受控 LLM 二线客服 Harness 代替人工客服 Handoff

状态：Accepted  
日期：2026-07-19

## 背景

项目原路线将 v0.5 定义为创建 Mock 人工客服工单，但项目不会接入真实客服、CRM、消息
或呼叫中心。仅创建一张永远无人处理的工单无法验证问题是否得到解决，也难以承载计划中
的 Agent Loop 和记忆管理学习目标。

项目需要一个明确的复杂售后处理边界：简单请求继续走现有确定性工作流，复杂请求可以
交给更完整的 LLM 运行时；同时不能将模型包装成真人，也不能放宽退款审批和数据权限。

## 决定

- v0.5 不再实现“人工客服已接入”的产品语义，改为明确标注的“AI 二线客服”。
- AI 二线客服由单个受控 LLM Agent Harness 承载，不接入真实客服系统。
- Harness 提供上下文装配、结构化决策、类型化 Tool Registry、确定性 Policy、有限循环、
  Checkpoint、工作记忆、用户确认的长期记忆、公开 Trace 和 Eval。
- 简单订单、物流、政策和退款请求继续使用现有确定性路径；只有符合升级条件的复杂售后
  Case 才进入 Agent Loop。
- L2 Agent 没有独立授权身份。R0 工具按服务端 Policy 自动调用；R1 记忆写入需要用户
  确认；R2 Mock 退款继续复用 v0.4 的用户审批与幂等执行链。
- 长期记忆只保存用户明确确认的低风险偏好，独立于 Checkpoint、业务数据库和 RAG 索引。
- 不存储或展示模型隐藏推理，只记录结构化决策、工具轨迹、公开结果和停止原因。
- v0.5 只实现一个 L2 Agent；没有 Eval 证据前不扩展为多 Agent 或 Supervisor。

## 备选方案

- 只创建 Mock 人工工单：实现简单，但没有真实处理者，产品结果停在“已登记”而非解决。
- 用 Prompt 声称模型是人工客服：展示成本低，但误导用户，也无法形成明确的权限和运行时
  边界。
- 直接构建多 Agent 客服团队：可以展示更多角色，但会同时增加路由、共享状态、成本和
  Eval 变量，当前没有需求证据。
- 让 L2 Agent 自动执行退款：看似闭环，但会把候选动作生成者变成审批主体，破坏 v0.4
  已建立的风险隔离。

## 结果与代价

- v0.5 可以在真实业务约束下实践 Agent Loop、Harness、记忆管理、恢复和轨迹 Eval。
- 产品必须始终披露 AI 身份，不能使用“真人已接单”文案。
- 需要新增 L2 Case、循环预算、工具治理和 Memory Store 的独立生命周期。
- Agent Loop 会增加模型调用、延迟和失败模式，必须通过固定预算和 Eval 控制。
- 原 v0.5 `Mock 人工客服 Handoff` Draft 被本决策取代，未进入实现或迁移。
- 原 v0.6 的 Agent Loop 能力前移到 v0.5；v0.6 改为上下文工程、轨迹回放与 Harness
  可观测性增强。

## 关联文档

- [`../specs/system-constraints.md`](../specs/system-constraints.md)
- [`../specs/v0.5-l2-support-harness.md`](../specs/v0.5-l2-support-harness.md)
- [`../../PROJECT_GOAL_MAP.md`](../../PROJECT_GOAL_MAP.md)
- [`../../README.md`](../../README.md)
