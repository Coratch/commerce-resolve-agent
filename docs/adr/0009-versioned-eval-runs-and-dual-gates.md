# ADR-0009：使用版本化 Eval Run、受控 Baseline 与双通道门禁

状态：Accepted
日期：2026-07-21
接受日期：2026-07-21
关联版本：v0.8

## 背景

v0.1–v0.7 已积累七组固定 Eval、177 个确定性场景和多层自动化测试。当前 CLI 可以运行单个
Suite 或简单聚合全部 Report，但不同版本拥有不同 Schema，没有统一 Run Manifest、结果契约、
Baseline 或 Candidate 比较。模型、Prompt、工具、Policy 和 Context 变化后，维护者只能人工
对照输出，无法可靠区分真实回归、数据集变化和运行环境差异。

直接把所有评测交给真实 Provider 会引入网络、费用和非确定性，无法成为默认发布门禁；只使用
Fake Model 又不能证明真实模型的结构化输出和工具选择质量。把运行产物全部提交 Git 会复制临时
数据和噪声；完全不保留经过审核的 Baseline 则无法长期比较。

因此需要决定 Eval Run、Baseline、离线门禁、真实 Provider 资格评测和 Artifact 的长期边界。

## 决策

### 使用统一、版本化的 Eval Run 契约

- 现有七组 Eval 继续作为各自业务场景的事实源，不重写或复制 177 个场景。
- 新增适配层，把不同 Report 投影为统一的 Suite、Scenario、Metric 和 Safety 结果。
- 每次运行生成严格 Pydantic Schema 的 Run Manifest 和规范化结果。
- Manifest 记录代码、数据集、模型、Prompt、工具、Policy、Context、迁移、依赖和阈值版本。
- 结构化结果使用稳定规范化 JSON 和 SHA-256 内容摘要，时间、Run ID 和耗时等允许变化字段不进入
  确定性结果指纹。
- 缺少必要版本、指纹或 Scenario 契约的 Run 不能成为 Baseline。

### Offline Release Gate 与 Provider Qualification 分离

- Offline Release Gate 使用 Fake Model、Fake Gateway、临时数据库和固定数据，默认禁用网络并
  不读取真实 API Key。
- 离线门禁负责确定性业务结果、工具轨迹、Policy、审批、恢复、安全、迁移、后端、前端和浏览器
  回归，是每个候选版本的强制发布证据。
- Provider Qualification 只在显式请求时连接 OpenAI-compatible Chat Provider，只使用版本化
  合成 Fixture 和隔离 Mock 系统。
- 模型、Prompt、结构化输出 Schema 或 Context 策略变化时必须提供 Provider 资格报告；普通离线
  测试不依赖网络。
- 两条通道分别给出状态，不能用一条通道的通过覆盖另一条通道的失败、未完成或不可比较。

### Baseline 必须显式接受

- Candidate 只有完整通过对应通道门槛后才具备 Baseline 资格。
- 接受 Baseline 是独立显式动作，普通运行、失败运行和报告生成不能更新 Baseline。
- Baseline 保存规范化结果、Manifest 摘要、内容指纹、接受原因和被取代的 Baseline ID。
- Scenario、Fixture 或指标契约不兼容时比较结果为 `incomparable`，不使用平均分或缺失默认值伪造
  无回归。
- 安全失败和确定性结果失败不能由其他指标提升抵消。

### 运行 Artifact 与受控 Baseline 分层

- 普通 Run 的 Manifest、结果、Markdown、日志和临时数据库写入已被 Git 忽略的 `var/eval/`。
- 只有维护者明确接受、通过脱敏检查的 Baseline 才写入版本控制下的 `data/eval/baselines/`。
- Provider 资格 Fixture 放在版本控制下的 `data/eval/`，只包含合成数据和结构化期望。
- 正式版本摘要继续保存到 `docs/eval/`，但不复制完整临时 Artifact、Provider 原始响应或真实用户
  数据。
- Baseline 和报告不进入 LangGraph Checkpoint、业务数据库、政策索引或长期 Memory Store。

### 不新增外部 Eval 平台依赖

- v0.8 使用现有 Python、Pydantic、pytest、SQLite、React/Vitest 和 Playwright 技术栈。
- Release Gate 的外部命令使用明确参数数组、固定工作目录、超时和脱敏输出，不通过 Shell 字符串
  拼接。
- 不接入 LangSmith、Langfuse、OpenTelemetry、云端 Trace、在线 A/B 或集中日志平台。
- 未来接入外部平台时只能消费经过脱敏的统一契约，并需要新的 Spec/ADR。

## 备选方案

### 重写七组 Eval 为一个新数据集

统一程度高，但会一次性改写 177 个已验证场景，增加行为漂移和回归风险，也会把各版本特有指标
压平。因此选择兼容适配层，后续只在真实需求出现时逐步迁移单个 Suite。

### 把所有 Run 写入业务 SQLite

查询方便，但会把发布工程数据与账号、订单、退款和 L2 Case 混在同一生命周期，并使 Baseline
审阅和 Git 版本化困难。因此运行 Artifact 使用文件系统，业务库保持不变。

### 只保存 Markdown 报告

便于阅读，但不能稳定做机器比较、指纹校验和 CI 门禁。因此同时保存规范化 JSON 和 Markdown
投影，JSON 是比较事实源。

### 自动采用最近一次通过运行作为 Baseline

操作简单，但可能在阈值或数据集变化时静默移动基准，使回归被吸收。因此拒绝自动更新，要求
显式接受和原因。

### 只运行真实 Provider Eval

更接近实际模型，但网络和非确定性会破坏默认回归；错误也难以区分模型和 Harness。因此保留离线
强制门禁，并单独进行真实 Provider 资格评测。

### 直接接入外部 Eval/Tracing 平台

具备现成 UI 和聚合能力，但引入外部数据传输、账号、费用和供应商耦合。当前首先验证本地契约、
门禁和数据最小化，因此延后。

## 后果

正向结果：

- 维护者可以用同一契约运行、比较和解释 v0.1–v0.8 Eval。
- Fake 回归与真实模型质量不再混为一个分数。
- 安全和确定性结果使用强门禁，不会被平均指标掩盖。
- 普通运行不污染 Git，经过审核的 Baseline 又能随代码审查和版本历史维护。
- 未来 CI 或外部平台可以消费稳定 JSON，而不需要读取七种内部 Report。

代价：

- 需要维护 Suite Adapter、Catalog 版本、规范化规则和 Baseline 兼容策略。
- Release Gate 会执行后端、前端和浏览器检查，本地运行时间明显增加。
- Provider Qualification 仍受网络、费用和模型漂移影响，不能完全确定性复现。
- 文件型 Artifact 只适合本地单实例，不提供多用户并发锁或集中查询。
- 明确接受 Baseline 增加一次审核操作，这是防止静默移动基准的有意成本。

## 接受条件

本 ADR 与 v0.8 Technical Plan 一起接受，长期约束为：

- 现有 177 个场景通过适配层保留，不整体重写。
- Offline Release Gate 和 Provider Qualification 分离且不能互相覆盖结论。
- Baseline 只能显式接受，安全失败或不兼容不能成为 Baseline。
- 普通 Artifact 写入 `var/eval/`，只有脱敏受控 Baseline 进入 `data/eval/baselines/`。
- 统一契约不保存密钥、完整 Prompt、隐藏推理、真实用户正文或原始 Provider 响应。
- v0.8 不新增外部 Eval/Tracing 平台和运行时依赖。

## 关联文档

- [`../specs/v0.8-agent-eval-hardening.md`](../specs/v0.8-agent-eval-hardening.md)
- [`../specs/system-constraints.md`](../specs/system-constraints.md)
- [`../plans/v0.8-agent-eval-hardening-plan.md`](../plans/v0.8-agent-eval-hardening-plan.md)
- [`0006-llm-l2-support-harness.md`](0006-llm-l2-support-harness.md)
- [`0008-context-manifest-and-dual-trace.md`](0008-context-manifest-and-dual-trace.md)
