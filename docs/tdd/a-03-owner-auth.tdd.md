# A-03 本地 OWNER 身份验证与授权边界 TDD 证据

> 日期：2026-09-14  
> 任务：A-03 本地 OWNER 身份验证与授权边界  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程

- 作为首次部署者，我希望用一次性 bootstrap token 创建唯一 OWNER，后续初始化请求必须被拒绝。
- 作为 OWNER，我希望密码以带随机盐的强哈希保存，并通过 HttpOnly、SameSite session cookie 登录。
- 作为已登录用户，我希望状态变更同时验证精确 Origin 和 CSRF token，退出后 session 立即失效。
- 作为任意用户，我不能通过资源 ID 判断其他用户的账号、任务或附件是否存在。
- 作为审计人员，我希望成功 bootstrap、登录和退出都留下不含凭据的审计记录。

## 2. RED 证据

第一轮新增密码、bootstrap token、认证 API、授权 repository 与 A-03 migration 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_passwords.py tests\unit\test_bootstrap_token.py tests\integration\test_auth_api.py tests\integration\test_authorization_repository.py tests\integration\test_migrations.py -q
```

结果：收集阶段 4 errors，分别因为 `security`、`application`、authorization repository 和 `HEAD_REVISION` 尚不存在，属于目标实现缺失产生的有效 RED。

安全重构阶段新增“验证错误不得回显认证输入”和“日志不得出现邮箱”测试。首次运行结果为 2 failed：FastAPI 默认 422 回显额外字段值，日志过滤器保留邮箱。随后统一 422 契约只保留 `type/loc/msg`，认证字段改为 `SecretStr`，日志增加邮箱脱敏，测试转为 GREEN。

迁移兼容阶段先在 A-02 revision 写入已有账号，再升级 A-03。首次结果为 1 failed，membership 查询为空；加入从 `x_accounts.owner_user_id` 的迁移回填后转为 GREEN。

重启语义复核在 token 消费后重新构造 manager，首次结果因缺少 `is_consumed` 为 1 failed；增加持久完成标记后，成功 bootstrap 的后续重启不再生成误导性的 token 文件。

## 3. GREEN 与重构证据

| 保证 | 自动化验证 | 结果 |
| --- | --- | --- |
| scrypt 密码哈希随机加盐、可验证、错误/恶意参数 fail closed | `tests/unit/test_passwords.py` | PASS |
| bootstrap token 只生成一次，可用环境注入，成功后删除生成文件 | `tests/unit/test_bootstrap_token.py`、认证 API 测试 | PASS |
| OWNER 只能 bootstrap 一次，第二次返回稳定 409 | `test_owner_bootstrap_succeeds_once_and_is_audited` | PASS |
| 登录失败不区分账号不存在和密码错误 | `test_session_expiry_and_bad_login_fail_without_account_enumeration` | PASS |
| session/CSRF token 只以摘要入库，cookie 为 HttpOnly/SameSite=Strict | `test_login_uses_secure_cookie_and_csrf_protected_logout` | PASS |
| production cookie 带 Secure | `test_production_session_cookie_is_secure` | PASS |
| session 过期、退出后立即拒绝 | 认证 API 集成测试 | PASS |
| 所有状态变更验证精确 Origin；退出验证 CSRF | Origin/退出集成测试 | PASS |
| 422 不回显密码、bootstrap token 或额外输入 | `test_validation_errors_do_not_echo_authentication_secrets` | PASS |
| 账号、任务、附件从 membership 查询，跨用户与不存在统一抛出 404 类错误 | `test_authorization_repository.py` | PASS |
| A-02 已有账号升级时自动回填 OWNER membership | `test_auth_migration_backfills_owner_memberships` | PASS |
| bootstrap、login、logout 成功状态变更写审计 | 认证 API 集成测试 | PASS |

重构使用标准库 `hashlib.scrypt` 避免新增运行依赖；未知账号仍执行同参数 dummy hash，降低账号枚举的时序差异。session 与 CSRF 使用独立随机 token，数据库仅保存 SHA-256 摘要。所有 UTC 时间显式序列化，消除了 Python 3.12 SQLite datetime adapter 弃用警告。

## 4. 数据库变更

revision `0002_a03_auth` 新增：

- `auth_sessions`：token/CSRF 摘要、过期与撤销时间；
- `x_account_memberships`：账号、用户、角色复合主键，是授权查询起点；
- `artifacts`：为本任务要求的附件跨用户隔离提供最小受保护元数据，下载与 XLSX 生成仍留在 A-13。

迁移支持 `0001 → 0002 → base`，并回填升级前已有账号的 OWNER membership。

## 5. 覆盖率与质量门禁

- 后端：30 tests passed。
- statements/branch 综合覆盖率：88.15%，高于 80% 门禁。
- Ruff：PASS。
- mypy strict：PASS，33 个 Python 源/测试文件无问题。
- Alembic upgrade/downgrade 与 owner membership 回填：PASS。

最终统一门禁：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath '<Node 24 executable>'
```

结果：后端 30 tests PASS；前端 1 test PASS 且覆盖率 100%；ESLint、TypeScript、Vite production build 全部 PASS；最终输出 `All quality checks passed.`。

## 6. 已知边界

- A-03 只实现本地 OWNER UI/API 所需认证能力；OPERATOR/VIEWER 界面后置，但 membership schema 与查询已保留角色边界。
- 本任务提供账号、扫描与附件 ownership repository；具体业务端点在对应后续任务接入，必须复用这些 membership-first 查询。
- session 清理与 30 天保留任务在后续运维任务实现；过期 session 当前已不可认证。
