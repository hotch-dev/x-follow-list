# SQLite 备份与恢复

## 备份

备份必须通过 `x_follow_list.operations.backup.backup_database` 使用 SQLite Online Backup API 生成；不要在服务运行时直接复制数据库文件。结果包含文件大小、创建时间和 SHA-256。将数据库备份、artifact 目录及校验清单一起保存到加密介质。

每日备份，至少保留 30 天。备份完成后核对 SHA-256，并限制只有运维账号可读。

## 恢复

`restore_database` 在同目录临时文件中校验 SHA-256 和 `PRAGMA integrity_check`，验证成功后原子替换目标；失败不会覆盖现有数据库。恢复 artifact 时同样核对数据库记录中的哈希与文件大小。

每月至少执行一次隔离环境恢复演练：恢复数据库和 artifact、运行迁移、启动 API、确认 `/health/ready`、抽查登录/扫描历史/关系分页/XLSX 下载，并记录耗时与校验值。演练不得访问 X 生产环境。
