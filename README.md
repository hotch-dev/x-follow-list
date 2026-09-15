# X Follow List

可自托管的 X 关注关系监控系统。阶段 A 已完成：本地 OWNER 管理后台、Direct Chrome 绑定与扫描、原子关系快照和事件、安全 XLSX 下载、恢复/性能门禁与部署材料均已有自动化证据。

## 环境要求

- Python 3.12+
- Node.js 22.12、24.x 或 26+
- npm 11+
- Google Chrome（Direct Chrome Provider 任务开始后需要）

Node.js 奇数版本不在当前 Vitest/jsdom 验证范围内。本项目的 A-01 检查使用 Node.js 24.19.0 通过。

## 安装

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
Set-Location web
npm install
```

## 开发入口

```powershell
# API: http://127.0.0.1:8000/health/live
& '.\.venv\Scripts\x-follow-list-api.exe'

# Worker：并行消费绑定会话和扫描任务
& '.\.venv\Scripts\x-follow-list-worker.exe'

# React 开发服务器（在 web 目录）
node .\node_modules\vite\bin\vite.js
```

## 数据库迁移与健康检查

开发环境默认使用仓库 `data` 目录。生产环境必须通过环境变量提供绝对数据目录和至少 32 字符的非默认主密钥。

```powershell
# 应用基线迁移
& '.\.venv\Scripts\python.exe' -m alembic upgrade head

# 回退全部迁移（会删除迁移管理的数据表，仅用于明确的回滚操作）
& '.\.venv\Scripts\python.exe' -m alembic downgrade base

# readiness 在迁移未应用或数据库不可写时返回 503
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

## 首次 OWNER 初始化

首次启动 API 时，如果没有配置 `X_FOLLOW_LIST_BOOTSTRAP_TOKEN`，系统会在受保护的数据目录生成 `bootstrap-token` 文件。读取该文件后，以配置的精确 Origin 创建唯一 OWNER：

```powershell
$headers = @{ Origin = 'http://127.0.0.1:8000' }
$body = @{
    login = 'owner@example.test'
    password = '<at-least-12-characters>'
    bootstrap_token = (Get-Content '.\data\bootstrap-token' -Raw).Trim()
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/auth/bootstrap' `
    -Headers $headers -ContentType 'application/json' -Body $body
```

成功初始化后生成的 token 文件会被删除；第二次 bootstrap 返回 409。登录使用 `/api/v1/auth/session`，服务端只保存 session/CSRF token 的 SHA-256 摘要。状态变更必须携带精确 `Origin`；登录后的状态变更还必须携带返回的 `X-CSRF-Token`。

## 验证

在仓库根目录运行：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath 'C:\path\to\supported\node.exe'
```

`check.ps1` 依次执行后端覆盖率、Ruff、mypy、前端覆盖率、ESLint、TypeScript 与 Vite 生产构建。如果 `node` 本身就是受支持版本，可省略 `-NodePath`。

## 文档

- [需求基线](./x-relationship-browser-monitor-requirements.md)
- [阶段 A 技术设计](./phase-a-technical-design.md)
- [阶段 A 开发计划](./phase-a-implementation-plan.md)
- [A-01 TDD 证据](./docs/tdd/a-01-bootstrap.tdd.md)
- [A-02 TDD 证据](./docs/tdd/a-02-config-logging-database.tdd.md)
- [A-03 TDD 证据](./docs/tdd/a-03-owner-auth.tdd.md)
- [A-04 TDD 证据](./docs/tdd/a-04-relationship-domain.tdd.md)
- [A-05 TDD 证据](./docs/tdd/a-05-snapshot-persistence.tdd.md)
- [A-14 发布门禁 TDD 证据](./docs/tdd/a-14-release-gates.tdd.md)
- [A-14 production worker 接线 TDD 证据](./docs/tdd/a-14-worker-runtime.tdd.md)
- [部署与回滚](./docs/deployment.md)
- [备份与恢复](./docs/backup-restore.md)
- [已知限制](./docs/known-limitations.md)
