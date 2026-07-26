# CommerceResolve 单机部署

参考环境是 Ubuntu 24.04、Docker Engine 与 Docker Compose v2。应用只绑定宿主
`127.0.0.1`，以 UID/GID `10001` 的非 root 用户、只读 rootfs 和单 Uvicorn worker 运行。

## 首次启动

```bash
cp .env.deploy.example .env.deploy
mkdir -p var
# Linux 上确保容器用户可写数据目录：
sudo chown -R 10001:10001 var

./deploy/commerce-resolve build
./deploy/commerce-resolve up
./deploy/commerce-resolve status
```

默认 `LLM_FEATURE_ENABLED=false`，游客 Fake、订单管理、Mock 退款和政策 RAG 不需要
Provider。需要真实 LLM 时，只在未提交的 `.env.deploy` 中同时设置 `LLM_API_KEY`、
`LLM_MODEL` 和 `LLM_BASE_URL`。

## 生命周期

```bash
./deploy/commerce-resolve restart
./deploy/commerce-resolve logs
./deploy/commerce-resolve down
```

`down` 不删除 `var`。健康端点为 `/api/health/live` 与 `/api/health/ready`；只有后者
代表实例可承接业务请求。

## 备份与恢复

```bash
./deploy/commerce-resolve backup
./deploy/commerce-resolve backup-verify <backup_id>

# 空实例恢复
./deploy/commerce-resolve restore <backup_id>

# 覆盖恢复还必须显式确认当前 instance_id
./deploy/commerce-resolve restore <backup_id> \
  --replace --confirm-instance-id <instance_id>
```

备份会在服务原本运行时停止并于成功后重启；原本停止时则保持停止。Backup 包含
business、checkpoints、memory 三个权威 SQLite 与实例清单；
不包含 `.env`、日志、政策索引、Eval 或浏览器产物。损坏备份在写目标前被拒绝。

## 从 v0.8 升级

```bash
./deploy/commerce-resolve upgrade
```

升级先生成并验证 `pre-upgrade` Backup，再执行兼容检查、迁移和政策索引重建。失败时
服务保持停止，按命令输出的 Backup ID 恢复，不执行自动 Down Migration。
