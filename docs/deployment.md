# 阶段 A 部署与回滚

## 生产配置与密钥

复制 `deploy/.env.example` 为 `deploy/.env`，替换所有占位值。生产配置必须使用绝对数据目录；主密钥至少 32 个随机字符，bootstrap token 只用于首次建立 OWNER，随后应从环境中删除。不要把 `.env`、浏览器 profile、SQLite 或导出 artifact 提交到 Git。

## 启动、健康检查与迁移

1. 先按照 `docs/backup-restore.md` 生成并校验备份。
2. 执行 `python -m alembic upgrade head` 完成迁移。
3. 在 `deploy` 目录执行 `docker compose up -d --build`。
4. API 存活检查为 `/health/live`，就绪健康检查为 `/health/ready`；只有后者返回 200 才接入流量。
5. Direct Chrome 需要持久化 browser profile。容器模式需确认宿主机图形/浏览器能力；不具备时应在受控宿主机运行 worker。

服务只绑定到环回地址。若经反向代理公开，必须启用 TLS、限制管理端来源，并将 `X_FOLLOW_LIST_APP_ORIGIN` 设置为唯一外部 origin。

## 回滚

停止 API 和 worker，保留数据卷；回滚应用镜像到上一已验证版本。如果新版本执行了数据库迁移，优先从升级前备份恢复到新的数据目录并校验，再切换数据卷。禁止在未备份时直接运行 Alembic downgrade。恢复后依次检查 SQLite integrity、`/health/ready`、登录、最近成功快照和 artifact 下载。
