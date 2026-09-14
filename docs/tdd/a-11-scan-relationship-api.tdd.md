# A-11 扫描应用服务与 API TDD 证据

> 日期：2026-09-14  
> 任务：A-11 扫描应用服务与 API  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)  
> 工作流：`ecc:tdd-workflow`，严格保留 RED → GREEN → REFACTOR 检查点

## 1. 用户旅程与测试映射

| 用户旅程 | 自动化测试 |
| --- | --- |
| OWNER 查看授权账号、幂等创建一次扫描；同账号活动任务返回 409，越权资源返回 404 | `test_owner_lists_account_and_enqueues_one_idempotent_active_scan` |
| 失败任务显示分层错误，同时保留上一成功扫描时间和快照 | `test_scan_lists_and_details_keep_failure_separate_from_last_success` |
| 关系与事件按账号、状态和搜索过滤，事件使用乐观版本确认 | `test_relationship_and_event_queries_filter_and_acknowledge_with_version` |
| 扫描、关系和事件列表使用稳定 keyset cursor，非法 cursor fail closed | cursor 两项集成测试 |
| OWNER 受 CSRF、必填幂等键、角色和资源版本约束；关键变更写审计 | `test_mutations_require_idempotency_permission_version_and_write_audit` |
| 解绑保留历史，并按 Provider 能力删除 Direct Chrome 受管 profile；AdsPower 没有删除能力，只 detach | `test_unbind_executes_direct_chrome_managed_profile_deletion_policy` 及 A-10 Provider 契约回归 |
| 管理员创建受控 Provider config、脱敏验证并只列出现有 profile | `tests/integration/test_provider_config_api.py` |
| OpenAPI 固定 A-11 路径、必填 `Idempotency-Key` 与 204 解绑响应 | `test_a11_openapi_contract_exposes_required_routes_and_scan_idempotency` |

映射 AC-06、AC-08、AC-13。测试只使用 SQLite 临时库、Provider 替身和本地受管目录，不访问 X 生产环境。

## 2. RED 证据

| 检查点 | 失败证据 |
| --- | --- |
| `a502394` | 账号、扫描、关系与事件旅程均因路由不存在返回 404（3 failed） |
| `c96fb8c` | App 尚不能注入 Provider registry，扫描列表也没有稳定 cursor（2 failed） |
| `ff4f2a0` | `x_accounts.version` 不存在，关系/事件 cursor 未实现（2 failed） |
| `ccb5780` | 解绑虽返回 204，但 Direct Chrome 受管 profile 仍存在（1 failed） |

每个 RED 均由预期行为断言失败，而不是语法、导入或测试基础设施错误。

## 3. GREEN 证据

| 检查点 | 最小实现 |
| --- | --- |
| `350ce78` | 账号/扫描/关系/事件查询与事件确认 API，活动扫描冲突和失败状态投影 |
| `d5ca8cc` | Provider 发现、受控 config、脱敏验证、已有 profile 列表与扫描 cursor |
| `637915b` | 必填幂等键、角色边界、账号版本迁移、审计、解绑及三类 keyset cursor |
| `7a72f80` | 可选 `ManagedProfileDeletionProvider` 能力和 Provider 感知解绑策略 |

第三轮 A-11 回归 29 passed；删除策略聚焦测试 2 passed，Provider/API 扩大回归 45 passed。Ruff 和 strict mypy 均通过。

## 4. REFACTOR 证据

| 检查点 | 行为不变整理 |
| --- | --- |
| `2bbe094` | 统一 API 认证依赖和 keyset 分页收尾逻辑；扩大回归 32 passed |
| `8873074` | 收紧解绑 Provider 参数边界并集中 mutation role 规则；A-11 API 回归 7 passed |

重构后未放宽授权、端点策略、幂等、版本或覆盖率门禁。

## 5. 覆盖率与全量质量门禁

统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- 后端：204 passed，无 skipped/disabled tests。
- statements/branch 综合覆盖率：88.42%，高于 80% 门禁。
- A-11 `application/monitoring.py`：81%；`api/monitoring.py`：98%；`api/providers.py`：100%；共享认证依赖：100%。
- Ruff：PASS；mypy strict：PASS，86 个 Python 源/测试文件无问题。
- 前端：1 test passed，覆盖率 100%；ESLint、TypeScript、Vite build 全部 PASS。
- 统一脚本最终输出：`All quality checks passed.`

受限沙箱内首次覆盖率运行的两个既有 Playwright E2E 因 Windows 命名管道 `WinError 5` 失败；同一完整门禁在获准环境运行后两项本地 Chrome E2E 均通过。这是执行环境权限差异，不是跳过测试。

## 6. Git 检查点链

检查点位于 `main` 连续历史中，未 squash 或改写：

`a502394 → 350ce78 → c96fb8c → d5ca8cc → ff4f2a0 → 637915b → 2bbe094 → ccb5780 → 7a72f80 → 8873074`
