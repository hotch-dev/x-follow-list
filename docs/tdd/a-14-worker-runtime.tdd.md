# A-14 Production Worker 接线 TDD 证据

> 日期：2026-09-15  
> 来源：A-14 完成后的代码审计发现 `x-follow-list-worker` 仍只等待停止信号  
> 工作流：`ecc:tdd-workflow`

## 1. 用户旅程

1. 运维启动 `x-follow-list-worker` 后，进程同时消费持久化的绑定会话和扫描任务。
2. 扫描任务从数据库读取账号、Provider 配置和 profile，不依赖测试代码手工装配。
3. handler 失败时以固定脱敏摘要结束任务，worker 可继续领取下一项。
4. 绑定任务启动可见 Direct Chrome，只从明确的 `Viewer` 响应读取稳定 X user ID，随后等待用户确认。

AdsPower 不在本次实现或测试范围。默认 production worker 仅注册 Direct Chrome；所有自动化浏览器回归只访问本地模拟页。

## 2. RED 证据

第一组 RED：`a126550`。

- `ConfiguredScanJob` 不存在，集成测试在目标模块导入处失败。
- `WorkerRuntime(loop)` 抛出 `TypeError`，证明入口无法委托持久化循环。
- handler 异常原样冒泡，包含诊断文本并终止 `run_once`。

第二组 RED：`a64d47f` 与 `39b9288`。

- `BindingWorker`、`ConfiguredBindingJob`、`CurrentIdentityReader` 均不存在。
- `WorkerRuntime(binding_loop, scan_loop)` 抛出 `TypeError`。
- 测试预先限定只接受 `data.viewer.user_results.result`，拒绝他人用户或缺失稳定 ID 的载荷。

这些均为目标能力缺失导致的有效 compile-time/runtime RED，不是依赖或沙箱故障。

## 3. GREEN 与重构

扫描 GREEN：`9c6b21b`。

- `ConfiguredScanJob` 从 `x_accounts` 和 `browser_provider_configs` 加载版本化配置、profile 和 username。
- `ScanWorker` 统一校验错误码并只持久化 `scan execution failed`，处理失败后继续轮询。
- 入口构造数据库、Direct Chrome registry、artifact service、完整关系扫描任务和持久化 `ScanWorker`。
- 目标结果：9 passed；Ruff、mypy strict 通过。

扫描重构：`b822460`。失败终态统一由 `ScanWorker` 所有，移除 `RelationshipScanJob` 的重复结束逻辑。目标 9 passed，完整 Direct Chrome 本地 E2E 1 passed。

绑定 GREEN：`3ac50d3`。

- `BindingWorker` 启动时回收过期会话并持续领取新 claim。
- `ConfiguredBindingJob` 仅加载当前 RUNNING 且 claim token 匹配的持久化配置。
- `CurrentIdentityReader` 在监听响应后访问 home，忽略非 `Viewer` 响应，严格解析稳定 ID，并始终移除 listener。
- `WorkerRuntime` 使用 `TaskGroup` 同时运行绑定和扫描循环，并共享停止信号。
- 目标结果：12 passed；Ruff、mypy strict 通过。

绑定重构：`7b5443c`。两条任务装配路径共用 Provider JSON、版本和 secret reference 解码器。worker 目标回归 17 passed，Ruff、mypy strict 通过。

## 4. 测试规格

| # | 保证 | 测试 | 类型 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | runtime 将停止信号委托给一个或多个任务循环 | `test_worker_runtime_*task_loop*` | 单元 | PASS |
| 2 | handler 错误码不安全时持久化固定 `SCAN_FAILED` 和脱敏摘要 | `test_worker_records_sanitized_handler_failure_and_remains_available` | 单元 | PASS |
| 3 | 扫描从数据库加载 provider/profile/username 并关闭自启浏览器 | `test_configured_scan_loads_account_provider_and_profile_from_database` | 集成 | PASS |
| 4 | 绑定 worker 启动恢复过期会话并消费 claim | `test_binding_worker_expires_stale_sessions_and_processes_claims` | 单元 | PASS |
| 5 | 绑定从数据库加载 claim 对应配置并记录检测身份 | `test_configured_binding_loads_claimed_provider_and_records_identity` | 集成 | PASS |
| 6 | 身份解析拒绝歧义载荷且 reader 忽略非 Viewer 响应 | `test_identity_reader.py` | 单元 | PASS |
| 7 | 原有绑定 → 双扫描 → diff/event → XLSX 真实 Chrome 本地流程无回归 | `test_phase_a_release_journey.py` | E2E | PASS |

## 5. 最终门禁

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- 后端：193 passed；综合 statements/branch coverage 80.57%。
- Ruff：PASS；mypy strict：PASS，111 个 Python 源/测试文件无问题。
- 前端：4 files、17 tests passed。
- 前端 statements 98.08%、branches 83.82%、functions 96.70%、lines 98.98%。
- ESLint、TypeScript、Vite build：PASS。
- 统一脚本：`All quality checks passed.`

没有降低 80% 覆盖率门禁，没有 skipped/disabled 非 AdsPower 测试，没有访问 X 生产环境。
