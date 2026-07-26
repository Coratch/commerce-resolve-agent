# CommerceResolve

基于 LangGraph 的可审计电商售后 Agent，也是一个面向 Agent 开发工程师学习与面试的开源项目。
V2.0 把历次版本的订单、物流、RAG、退款审批、Memory、Agent Loop 和 Eval 能力收敛到一条
可重复演示的产品链路。

```text
邀请码注册
→ 自动创建独立演示工作区
→ 从具体订单打开悬浮 Agent
→ 查询订单、物流和售后政策
→ 确定性退款 Policy
→ interrupt 客户确认
→ 幂等 Mock 退款
→ 业务状态回读验证
→ Agent Run、Memory、RAG 与 Eval 证据
```

订单、物流、支付、退款和深度处理 Case 全部是本地 Mock 业务数据，不连接真实电商、物流、
支付或客服系统，不产生真实资金和外部业务副作用。

## V2.0 产品能力

- 未登录访问者只能查看公开页、登录和邀请码注册，不创建游客 Session、业务数据或模型调用。
- 受邀用户注册后自动获得隔离的 `portfolio-demo-v1` 工作区和三笔 `CR-XXXX-XXXX` 订单：
  - 物流延迟且可退款；
  - 已签收质量问题；
  - 超过退款期限。
- 客户从售后中心或订单详情打开右侧订单绑定 Agent；刷新、切页和重新登录后可恢复历史。
- 一个活动订单任务只对应一个 Thread，不同订单和不同用户严格隔离。
- 无法可靠识别的诉求在同一 Thread 中最多澄清两轮；识别成功后恢复既有路径，达到上限后
  使用确定性兜底，不把正常澄清显示为系统异常。
- 政策问答使用 SQLite FTS5、中文 bigram、BM25、有效期/区域过滤及可定位引用。
- 退款资格、金额、渠道和过期判断由确定性代码计算；RAG 和 LLM 不能授权资金动作。
- 退款通过 LangGraph `interrupt` 暂停，客户确认后幂等执行并回读业务事实验证。
- 复杂问题可进入明确标注为 AI 的有界深度处理 Loop，支持只读工具、补充信息中断和 Memory
  提议确认。
- 用户可以查看、修正和删除本人明确保存的长期偏好。
- 客户或管理员可以重置整个演示工作区；订单公开编号保持不变，派生退款、服务、会话、
  Checkpoint 和 Memory 被清除。
- 运营控制台提供邀请、演示工作区、脱敏 Agent Run、Eval 和有限系统状态；不提供订单 CRUD、
  客户冒充、Web 授予管理员或绕过审批退款。

## 技术栈

| 领域 | 技术 |
|---|---|
| Agent 编排 | LangGraph 1.2、State、Command、interrupt、SQLite Checkpointer |
| 后端 | Python 3.12、FastAPI、Pydantic、SQLAlchemy、Alembic |
| 前端 | React 19、TypeScript、Vite、TanStack Query、Lucide、Playwright |
| 业务存储 | SQLite；与 Checkpoint、Memory、RAG 索引和 Eval Artifact 分离 |
| RAG | 版本化 Markdown/JSON、SQLite FTS5、中文 bigram、BM25、引用与冲突检测 |
| LLM | OpenAI-compatible Chat API；测试和离线 Eval 使用 Fake Adapter |
| 质量 | pytest、Vitest、Playwright、Ruff、版本化离线 Eval、真实 Provider Qualification |

核心边界：

```text
用户输入
→ LLM 结构化理解
→ LangGraph 路由
→ 受控 Gateway / RAG
→ 确定性 Policy
→ 必要时客户确认
→ 幂等 Executor
→ Verifier 回读真实 Mock 业务状态
```

LLM 负责理解和解释，不负责权限、金额、退款资格或最终执行授权。

## 本地安装

推荐使用 Conda：

```bash
conda create -n ecom-agent python=3.12 pip -y
conda activate ecom-agent
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m pip install -e ".[openai,dev]" --no-deps

cd frontend
npm ci
cd ..
```

复制配置并填写 OpenAI-compatible Provider：

```bash
cp .env.example .env
```

```dotenv
LLM_API_KEY=
LLM_MODEL=replace-me
LLM_BASE_URL=https://provider.example/v1
```

密钥只保存在本地 `.env`，不得提交仓库。

## 初始化

首次运行显式创建/升级业务 Schema、政策索引和 Memory Schema：

```bash
conda activate ecom-agent
python -m commerce_resolve db upgrade
python -m commerce_resolve policy-index build
python -m commerce_resolve memory setup
```

创建一张邀请码：

```bash
python -m commerce_resolve invite create
```

邀请码明文只在本次命令输出中出现。使用该邀请码在 `/register` 注册后，系统会原子创建账号、
工作区和三笔演示订单。

项目不内置管理员账号或密码。先注册普通账号，再由本机维护者显式授予管理员角色：

```bash
python -m commerce_resolve admin grant <username>
python -m commerce_resolve admin list
```

Web 页面不能授予管理员角色。

## 启动

开发模式使用两个终端。

后端：

```bash
conda activate ecom-agent
python -m commerce_resolve serve
```

前端：

```bash
cd frontend
npm run dev
```

打开 `http://127.0.0.1:5173`。

也可以由 FastAPI 同源托管构建后的 SPA：

```bash
cd frontend
npm run build
cd ..
python -m commerce_resolve serve
```

打开 `http://127.0.0.1:8000`。当前承诺本地单实例和 Uvicorn 单 worker；参考 Docker 交付门禁
按项目决策延后，不把未验证的容器部署描述为已完成能力。

## 推荐验收路径

1. 创建邀请码并注册新账号。
2. 登录后确认售后中心自动出现 Pulse、Craft、FlowSip 三个场景订单。
3. 从物流延迟订单点击“咨询此订单”，输入“查下物流，符合条件的话帮我退款”。
4. 观察订单、物流和政策证据，确认退款预览由确定性 Policy 生成。
5. 批准退款，观察 `interrupt` 恢复、幂等执行和已验证回执。
6. 刷新页面或切换路由，再次打开同一订单，确认历史仍在且复用活动 Thread。
7. 在演示设置重置工作区，确认三笔订单号不变、退款和会话等派生数据已清除。
8. 将账号授予管理员后，从 `/admin` 查看邀请、工作区、Agent Run、Eval 和系统状态。

客户入口：

```text
/support
/support/orders
/support/orders/:orderId
/support/services
/support/memories
/support/settings
```

运营入口：

```text
/admin
/admin/data
/admin/invitations
/admin/runs
/admin/eval
/admin/system
```

## 测试与 Eval

```bash
conda run -n ecom-agent python -m ruff check .
conda run -n ecom-agent python -m ruff format --check .
conda run -n ecom-agent python -m pytest -q

conda run -n ecom-agent python -m commerce_resolve eval run --suite v2.0
conda run -n ecom-agent python -m commerce_resolve eval run --suite all

cd frontend
npm run typecheck
npm test
npm run build
npm run test:e2e:offline
npm run test:e2e
```

真实 Provider Qualification 与离线门禁分开运行：

```bash
conda run -n ecom-agent python -m commerce_resolve eval qualify \
  --dataset data/eval/v2.0/provider-qualification.json \
  --repetitions 2
```

V2.0 本机验收证据：

- V2.0 四层 Eval：`36/36`，安全违规 `0`。
- 当前兼容历史 Suite + V2.0：`265/265`，安全违规 `0`。
- 真实 Provider 双轮资格：`23/24`，结构化输出有效率 `100%`，安全违规 `0`。
- Baseline：`baseline-0b0d5665b7df5560`。

历史不兼容 Suite 继续保留并可单独回放，但不混入 V2.0 当前发布门禁。完整证据见
[`docs/eval/v2.0-report.md`](docs/eval/v2.0-report.md)。

## 文档入口

- [项目目标地图](PROJECT_GOAL_MAP.md)
- [V2.0 Feature Spec](docs/specs/v2.0-interview-ready-agent-product.md)
- [V2.0 Technical Plan](docs/plans/v2.0-interview-ready-agent-product-plan.md)
- [V2.0.1 意图澄清 Spec](docs/specs/v2.0.1-intent-clarification.md)
- [V2.0.1 意图澄清 Plan](docs/plans/v2.0.1-intent-clarification-plan.md)
- [系统约束](docs/specs/system-constraints.md)
- [代码目录与文件职责](docs/codebase-guide.md)
- [V2.0 Eval 报告](docs/eval/v2.0-report.md)
- [V2.0.1 意图澄清验收报告](docs/eval/v2.0.1-report.md)
- [历史 Spec、Plan、ADR 与 Eval](docs/)

## 已知限制

- 所有交易和客服事实均为本地 Mock，不具备真实商业接入能力。
- SQLite 适合本地学习和单实例演示，不适合多实例商业部署。
- 当前政策知识仍是受控 Markdown/JSON + FTS5；多格式、多模态和向量混合检索属于 V2.1。
- 真实 LLM 输出存在非确定性；离线安全门禁不能被真实 Provider 分数替代。
- 不展示或持久化模型隐藏推理，只保留公开步骤、结构化状态和脱敏诊断。
