# A-10 AdsPower Provider TDD 证据

> 日期：2026-09-14
> 任务：A-10 AdsPower Provider
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)
> 工作流：请求的 `ecc:tdd-workflow` 在本次会话可用技能清单和仓库中均不存在；按 A-08/A-09 已采用的等价 RED→GREEN→重构→覆盖率→证据纪律执行

## 1. 用户旅程与测试映射

| 用户旅程 | 自动化测试 |
| --- | --- |
| 管理员通过版本化受控配置和 secret reference 连接 AdsPower，不在请求或日志传播 token | `test_validate_config_checks_version_secret_reference_and_connectivity`、token/错误映射测试 |
| 用户只能列出并选择已有 profile，适配器无 create/update/delete 行为 | `test_lists_only_existing_profiles_without_mutating_them`、V2 client 安全字段测试 |
| Provider 在 profile 锁后检查 active，未运行时 start 并记录所有权 | `test_lock_is_taken_before_status_check_and_owned_profile_is_stopped` |
| Playwright 只连接策略允许的 CDP endpoint，恶意公网/协议/凭据/fragment 全部拒绝 | `test_rejects_untrusted_cdp_endpoints_and_stops_owned_profile` |
| 本任务启动的 profile 在 release 时 stop；原本运行的 profile 只 detach；释放并发幂等 | owned/already-running/repeated-close 测试 |
| API 超时、有限重试、重定向、响应大小、无效 token 和 Provider 错误均 fail closed 且脱敏 | `test_controlled_api_*`、`test_invalid_token_*`、`test_redirect_and_oversized_*`、V2 client 单元矩阵 |
| AdsPower session 的统一 context 直接复用 A-08 collector，不引入 AdsPower 业务分支 | `tests/integration/test_adspower_collection.py` |

映射 AC-01、AC-12、AC-17。所有测试使用脱敏固定数据、本地伪 API 或可控 CDP 替身，不访问 X 生产环境。

## 2. RED 证据

先添加完整 AdsPower Provider 契约测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\contract\test_adspower_provider.py -q
```

结果：收集阶段出现 `ModuleNotFoundError: No module named 'x_follow_list.browser.adspower'`，证明目标适配器尚不存在。RED 检查点：`738642e`。

## 3. GREEN 证据

- 新增 `AdsPowerApiClient`，只暴露 status/list/active/start/stop；Bearer token 由 secret reference 在执行进程解析。
- 配置版本固定为 1，Local API 固定为 V2，AdsPower 声明版本至少 3.4.1；超时 0.1–30 秒、最多 3 次请求、响应上限 1 KiB–4 MiB。
- 只对 transport error 和明确的短暂 HTTP 状态有限重试；401/403、Provider 业务错误、无效 JSON、重定向和超限响应不重试或 fail closed。
- 获取进程内 profile 锁后先列出已有 profile，再检查 active；仅未运行 profile 由当前任务 start。
- start/active 返回的 CDP endpoint 重新验证 scheme、credentials、query/fragment、端口和地址策略后才传给 `connect_over_cdp`。
- session 记录 adapter、声明的 provider 版本和 CDP browser 版本；release 并发幂等，并按启动所有权 stop 或只 detach。

目标测试结果：14 passed；共享 Provider/Direct Chrome/worker 回归共 35 passed；Ruff 与 strict mypy 通过。GREEN 检查点：`e2e8c95`。

## 4. 重构与覆盖率证据

- profile list 响应进入适配器前只保留 `profile_id` 与 `name`，不传播 AdsPower 返回的密码、代理等无关字段。
- Provider API base URL 禁止携带不受控路径前缀；所有 API 路径由 adapter 常量拼接。
- 增加 V2 client 的请求方法/body、分页响应、敏感字段最小化、错误分类、transport retry、配置边界与默认环境 secret 解析测试。
- 增加 AdsPower context → A-08 `ResponseCollector` 的纵向集成测试，结果得到稳定 ID `101/102`，证明采集器保持 Provider 无关。

相关目标：33 passed。A-10 `browser/adspower.py` statements/branch 综合覆盖率 85.47%，高于 80%。REFACTOR 检查点：`0a964dd`。

## 5. 全量质量门禁

统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- 后端：194 passed，无 skipped/disabled tests。
- statements/branch 综合覆盖率：88.25%，高于且未降低 80% 门禁。
- A-10 模块：86%（全量执行口径）。
- Ruff：PASS。
- mypy strict：PASS，79 个 Python 源/测试文件无问题。
- 前端：1 test passed，覆盖率 100%；ESLint、TypeScript、Vite build 全部 PASS。
- 统一脚本：`All quality checks passed.`

首次统一脚本在受限沙箱中启动 Playwright 时两个既有 Chrome E2E 因 Windows 命名管道 `WinError 5` 失败；同一完整命令在获准的沙箱外运行后 194 tests 全部通过。这是执行环境权限限制，不是业务 GREEN。

## 6. 真实 AdsPower 冒烟状态

2026-09-15 复验开发机 `http://127.0.0.1:50325/status` 返回 HTTP 200、`code=0`，确认真实 AdsPower Local API 已启动。但当前进程和用户级环境均没有 `ADSPOWER_API_TOKEN`；不带有效 token 的 profile list 返回 `API Key mismatch`，因此不能安全列出用户指定的“环境 3”，也不能执行 start/attach/collect/detach/stop。没有绕过认证或伪造通过；真实 AdsPower + 本地模拟 X 页冒烟仍是发布前验证项，待通过 `env://ADSPOWER_API_TOKEN` 提供有效凭据后执行，步骤和版本记录口径见 [Browser Provider 兼容性矩阵](../browser-provider-compatibility.md)。

## 7. Git 检查点

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| RED | `738642e` | 缺失 AdsPower adapter 的失败契约 |
| GREEN | `e2e8c95` | 受控 Local API V2 client、CDP 连接与所有权释放 |
| REFACTOR | `0a964dd` | 响应最小化、API client 覆盖率和 A-08 collector 纵向复用 |

三个检查点位于当前 `main` 连续提交链，未 squash 或改写。
