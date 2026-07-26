# ADR-0010：使用单应用 Docker Compose 与独立 SQLite 交付 v1.0

状态：Accepted  
日期：2026-07-21  
接受日期：2026-07-21  
关联版本：v1.0

## 背景

v0.8 已形成 FastAPI/React 同源服务、单 Uvicorn worker、进程内 BackgroundTasks，以及业务、
LangGraph Checkpoint、长期 Memory 和政策索引四类独立 SQLite。v1.0 需要把它交付为可重复启动、
可判断健康、可备份恢复和可升级的单机作品集版本。

当前版本不要求多实例、高可用、真实交易系统或生产 SLA。此时迁移 PostgreSQL、增加 Redis/Worker
或采用 Kubernetes 会同时改变 Repository、后台执行、锁、备份和部署边界，却不是满足 v1.0
验收条件的必要手段。只保留 Conda 手工运行则无法提供干净主机、版本一致和最小权限的交付证据。

因此需要决定 v1.0 的参考运行拓扑、持久化、数据挂载、网络暴露和日志边界。

## 决策

### 使用 Docker Compose 作为单机参考交付

- 参考环境为 Ubuntu 24.04 LTS、Docker Engine 和 Docker Compose v2。
- 使用多阶段 Dockerfile 构建 React 静态资源、Python Wheel 和最小运行镜像。
- Compose 只包含一个长期运行的 `app` 服务；运维动作使用同一镜像的一次性容器。
- 容器内运行一个 Uvicorn worker，不增加独立 Worker、队列或第二 Web 实例。
- macOS Conda/npm 路径继续作为开发方式，但不作为 v1.0 参考交付门禁。

### 保留独立 SQLite

- `business.sqlite`、`checkpoints.sqlite` 和 `memory.sqlite` 继续分别保存业务、Graph 和长期偏好事实。
- `policy-index.sqlite` 继续是从版本化政策来源重建的派生数据。
- v1.0 不引入 PostgreSQL、MySQL、Redis 或跨进程锁。
- 应用与 init/backup/restore/upgrade 使用同一 POSIX Instance Lock，保证单主机只有一个写入主体。

### 使用宿主 bind mount 保存实例数据

- 仓库/Release Bundle 的 `./var` 挂载到容器 `/var/lib/commerce-resolve`。
- 该目录保存 SQLite、Instance Manifest、Backup Set 和脱敏 Operations Audit。
- 镜像内政策来源和前端资源只读；`.env` 由 Compose 注入，不进入镜像和数据目录。
- bind mount 使现有 v0.8 `var/*.sqlite` 能直接进入受控升级，也让维护者明确掌握数据位置。

### 只支持回环暴露

- Compose 固定把宿主 `127.0.0.1` 映射到容器 Web 端口。
- 容器内监听 `0.0.0.0` 只为端口映射，不代表允许公网 HTTP。
- v1.0 不捆绑 Caddy/Nginx、DNS 或证书；非回环绑定由 Preflight 拒绝。

### 使用停机一致性 Backup

- 正式 Backup 前停止 app 并取得 Instance Lock。
- 三个权威 SQLite 分别使用 Python `sqlite3.Connection.backup()` 生成快照。
- Backup Manifest 记录版本、文件大小、SHA-256、SQLite 完整性和有限领域计数。
- 政策索引、日志、Eval、临时文件、`.env` 和 Secret 不进入 Backup Set。
- Restore 默认只允许空目标；覆盖要求 Instance ID 确认和 rollback Backup。

### 使用本地结构化日志

- 应用使用 Python 标准库输出白名单 JSON 日志到 stdout/stderr。
- Compose 使用有限轮转的 Docker `local` logging driver。
- 运维审计保存在独立脱敏 JSONL，不写入业务数据库。
- v1.0 不接入外部 APM、集中日志或在线 Trace 平台。

## 备选方案

### 继续只支持 Conda + npm 手工启动

改动最少，但安装顺序、前端构建、迁移、运行时版本和权限依赖维护者记忆，无法成为可重复的
单机 Release Bundle。因此只保留为开发路径。

### 迁移到 PostgreSQL

更适合多实例和高并发，也有成熟在线备份工具；但 v1.0 不要求这些能力。迁移会同时影响业务
Repository、LangGraph Checkpointer/Store、Fixture、恢复和全部集成测试，因此延后到真实并发或
多实例需求出现后重新 Spec/ADR。

### 使用 Docker named volume

隔离性较好，但本地作品集场景的备份、恢复、v0.8 既有数据导入和人工检查都需要额外辅助容器。
明确 bind mount 边界更易理解和验证，因此选择 `./var`。

### 增加 Redis/Celery Worker

可让后台任务独立于 Web 进程，但会改变 Run 调度、幂等、停止和恢复模型，并引入新的持久化与
运维故障面。v1.0 使用显式 interrupted/retry 语义，不自动继续未知副作用，因此不选择。

### Kubernetes 或多个 Compose 服务

能演示更复杂部署，但与单机目标冲突，并要求处理 SQLite 多实例、网络、健康依赖和滚动升级。
当前一个应用容器已能覆盖所有验收条件。

### 在线复制 SQLite 文件

单库 Backup API 可以在写入期间取得一致快照，但三个独立 SQLite 没有跨库事务。为了让 Backup
Set 具有明确业务时间点，本版本选择短暂停机，而不是宣称在线跨库一致性。

### 捆绑 HTTPS 反向代理

更接近公网部署，但会引入域名、证书、信任链和额外容器。v1.0 参考服务只绑定回环地址，公网
产品化需要新的 Spec 和安全审计。

## 后果

正向结果：

- 一台干净 Linux 主机只需 Docker/Compose，即可构建和运行同一版本。
- 现有 SQLite Repository、Checkpoint、Memory 和 Agent 行为无需迁移。
- v0.8 `var` 数据可以在同一路径上验证升级，备份恢复过程对维护者可见。
- 单容器、单 worker 与当前 BackgroundTasks/thread lock 语义一致。
- 非 root、只读根文件系统、回环映射和有限日志显著收紧本地运行边界。
- 未来需要 PostgreSQL/Worker 时，可以用 Eval 和运行指标证明迁移需求，而不是提前猜测。

代价：

- Backup 和 Upgrade 存在短暂停机，不提供零停机或高可用。
- bind mount 的权限和磁盘容量由宿主维护者负责。
- SQLite 与单 worker 不适合高并发或多实例，v1.0 不能宣称生产扩展能力。
- 本地 Backup 默认与实例位于同一磁盘，不构成异地灾难恢复。
- Docker 成为参考交付前置条件；当前 macOS 开发路径仍需要单独维护。
- 不捆绑 HTTPS 意味着远程访问不属于受支持路径。

## 接受条件

本 ADR 与 v1.0 Technical Plan 一起接受，长期约束为：

- v1.0 参考部署只有一个非 root app 容器和一个 Uvicorn worker。
- 三类权威 SQLite 与一类派生政策索引继续分离。
- `./var` 是唯一持久 bind mount，Secret、日志和镜像构建缓存不进入 Backup Set。
- Backup/Restore/Upgrade 与 app 通过 Instance Lock 互斥；正式 Backup 使用停机一致性。
- 宿主只绑定回环地址；非回环与 HTTPS 交付不属于 v1.0。
- 不新增 PostgreSQL、Redis、Worker、Kubernetes 或外部可观测平台。
- 未来改变以上任一边界必须由新 Feature Spec 和 ADR 驱动。

## 关联文档

- [`../specs/v1.0-single-host-delivery.md`](../specs/v1.0-single-host-delivery.md)
- [`../specs/system-constraints.md`](../specs/system-constraints.md)
- [`../plans/v1.0-single-host-delivery-plan.md`](../plans/v1.0-single-host-delivery-plan.md)
- [`0002-use-sqlite-checkpointer.md`](0002-use-sqlite-checkpointer.md)
- [`0004-same-origin-web-architecture.md`](0004-same-origin-web-architecture.md)
- [`0009-versioned-eval-runs-and-dual-gates.md`](0009-versioned-eval-runs-and-dual-gates.md)
