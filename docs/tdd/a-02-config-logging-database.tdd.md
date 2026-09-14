# A-02 配置、日志与数据库基础 TDD 证据

> 日期：2026-09-14  
> 任务：A-02 配置、日志与数据库基础  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程

- 作为运维者，我希望错误生产配置在进程启动时立即失败，避免使用默认密钥运行。
- 作为开发者，我希望 API 与 worker 使用类型化配置和结构化关联日志，且凭据永不进入日志。
- 作为应用服务，我希望每条异步 SQLite 连接都启用外键、WAL 和 busy timeout。
- 作为部署者，我希望基线 schema 可升级、可回退，并由 readiness 拒绝未迁移或不可写的数据库。

## 2. RED 证据

第一轮先新增 10 个配置、日志、数据库、迁移和 readiness 测试，仅运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py tests\unit\test_redaction_logging.py tests\integration\test_database.py tests\integration\test_migrations.py tests\integration\test_readiness.py -q
```

结果：收集阶段出现 5 个 `ModuleNotFoundError`，分别指向尚不存在的 `config`、`observability` 和 `persistence` 模块；这是目标能力缺失导致的有效 RED。

第二轮为 HTTP request correlation 和 API/worker 日志初始化新增测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_api_bootstrap.py tests\unit\test_worker_bootstrap.py -q
```

结果：4 failed、3 passed；失败分别证明请求没有返回安全 `X-Request-ID`，且 API/worker 入口尚未初始化统一日志。

收尾部署复核将迁移目标改为尚不存在的嵌套数据目录并单独运行迁移测试，得到 `sqlite3.OperationalError: unable to open database file`。随后迁移环境在连接前创建明确数据库路径的父目录，并让默认 Alembic URL 从同一类型化环境配置解析；测试转为 PASS。

系统 pytest 临时根目录存在历史 ACL 拒绝访问；测试套件增加仓库内、自动清理且已忽略的 `.test-data` fixture。该环境错误不计作 RED。

## 3. GREEN 与重构证据

| 保证 | 测试 | 结果 |
| --- | --- | --- |
| 环境变量转换为强类型配置，无效值 fail fast | `tests/unit/test_settings.py` | PASS |
| 生产环境拒绝缺失、弱值和默认主密钥 | `tests/unit/test_settings.py` | PASS |
| 嵌套 token、Authorization、Cookie、CDP URL 与消息参数被统一脱敏 | `tests/unit/test_redaction_logging.py` | PASS |
| JSON 日志携带 request/scan/account correlation context | `tests/unit/test_redaction_logging.py` | PASS |
| 安全请求 ID 被传播，不安全值替换为服务端 UUID | `tests/unit/test_api_bootstrap.py` | PASS |
| 每条 SQLite 连接启用 foreign keys、WAL、busy timeout | `tests/integration/test_database.py` | PASS |
| async engine/session 可提交并读取数据 | `tests/integration/test_database.py` | PASS |
| Alembic 基线创建 7 张 A-02 表并可 downgrade 到 base | `tests/integration/test_migrations.py` | PASS |
| 已迁移、可读写数据库 readiness 返回 200 | `tests/integration/test_readiness.py` | PASS |
| 未迁移数据库 fail closed，返回脱敏 503 | `tests/integration/test_readiness.py` | PASS |

重构阶段将配置验证集中到不可变 `Settings`，将 SQLite engine/session 封装为可释放的 `Database`，将迁移 revision 作为 readiness 的单一版本常量，并把请求 ID 限制为安全字符与 64 字符长度。

## 4. Alembic 基线范围

本任务按 A-02 边界创建：`users`、`browser_provider_configs`、`x_accounts`、`scan_runs`、`resource_leases`、`idempotency_keys`、`audit_logs`。绑定会话、staging、snapshot、relationship 和 artifact 表由其对应后续任务按测试驱动加入，未提前创建空壳表。

## 5. 覆盖率与质量门禁

- 后端：17 tests passed。
- 后端 statements/branch 综合覆盖率：90.78%，高于 80% 门禁。
- Ruff：PASS。
- mypy strict：PASS，21 个 Python 源/测试文件无问题。
- Alembic upgrade/downgrade 集成测试：PASS。
- 新数据目录自动创建迁移测试：PASS。

最终统一质量门禁使用：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath '<Node 24 executable>'
```

完整门禁结果：后端 17 tests PASS、前端 1 test PASS、ESLint PASS、TypeScript PASS、Vite production build PASS，脚本输出 `All quality checks passed.`。

## 6. 已知边界

- A-02 readiness 仅检查 SQLite 可连接、当前 Alembic revision 和真实写事务；worker 心跳、主存储空间在对应 worker/artifact 任务加入。
- 主密钥本任务只负责安全配置入口；provider secret 加密持久化从 Provider 配置任务开始。
- 基线表只包含后续任务所需约束与索引，不在 A-02 实现身份验证、任务领取或业务 repository。
