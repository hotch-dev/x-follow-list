# A-06 持久化任务领取、租约与恢复 TDD 证据

> 日期：2026-09-14  
> 任务：A-06 持久化任务领取、租约与恢复  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程与测试映射

| 用户旅程 | 自动化测试 |
| --- | --- |
| API 应用层以相同请求键重放时返回同一 `QUEUED` run，不同请求复用同一键时冲突 | `test_enqueue_is_idempotent_and_rejects_key_reuse_with_different_request` |
| 两个 worker 同时领取时，同一 run 只能从 `QUEUED` 条件更新为一次 `RUNNING` | `test_two_workers_can_only_claim_a_queued_run_once` |
| worker 必须按账号、profile 固定顺序原子取得两把租约，profile 冲突不得残留半把账号锁或孤儿 run | `test_same_profile_conflict_does_not_leave_partial_account_lease`、`test_worker_safely_closes_claim_when_resources_are_busy` |
| 活跃 worker 续租两把资源；心跳丢失时停止进行中的处理 | `test_heartbeat_renews_both_leases_and_fenced_failure_releases_them`、`test_worker_cancels_inflight_navigation_when_heartbeat_is_lost` |
| 过期资源可由新任务取得更大的 fencing token，旧 worker 的终态和快照提交均被拒绝 | `test_heartbeat_loss_stops_worker_and_expired_lease_gets_higher_fence`、两个 `test_late_worker_cannot_*` |
| worker 在已持有租约或 claim 后、首租约前被强制终止，启动恢复均标记 `FAILED/BROWSER_CRASH`，不从分页中间恢复 | 两个 `test_restart_recover*` |
| worker 启动时执行恢复，并在任务期间定期心跳 | `test_worker_recovers_on_start_and_heartbeats_while_job_runs` |

上述旅程映射 AC-05、AC-06、AC-14、AC-17。A-11 将在此应用服务之上公开鉴权后的 HTTP 扫描端点。

## 2. RED 证据

首次先创建持久化协调集成测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_scan_coordination.py -q
```

结果：收集阶段因 `ModuleNotFoundError: No module named 'x_follow_list.application.scan_coordination'` 失败，属于目标行为不存在产生的有效 RED。

随后先增加 worker 心跳/取消测试，收集阶段因 `ModuleNotFoundError: No module named 'x_follow_list.worker.scan_loop'` 再次有效 RED。

重构阶段还捕获并修复两个真实失败：

- profile 冲突后的 run 会停留在无租约的 `RUNNING`；新增测试先以 `AttributeError: finish_unleased_failed` 失败，随后由 claim token 校验的失败收口转绿。
- worker 在 claim 与首租约之间终止时无法被基于租约的恢复器发现；新增测试先得到 `recovered == []`，随后加入 run heartbeat 超时恢复转绿。

## 3. GREEN 与重构结果

- SQLite `BEGIN IMMEDIATE` 串行化幂等入队、条件领取和短租约事务。
- `scan_runs` 持久化 `worker_id`、不可猜测 `claim_token` 与 `heartbeat_at`。
- 资源键固定按 `x-account:{id}`、`browser-profile:{provider_config_id}:{profile_ref_hash}` 排序并在同一事务取得；第二把锁冲突时第一把一起回滚。
- 过期租约在原行递增 fencing token，不删除 token 历史；心跳和终态更新同时验证 claim 与两把活动租约。
- A-05 快照提交在其原有 `BEGIN IMMEDIATE` 事务内验证 fencing，消除检查与关键业务写之间的竞态窗口。
- worker 心跳失败会取消当前 handler；profile 冲突以 `RESOURCE_BUSY` 安全结束，不启动浏览器处理。
- 启动恢复同时识别过期租约与过期 run heartbeat，并以 `BROWSER_CRASH` 结束任务，不重新排队或中页续跑。
- revision `0004_a06_scan_coordination` 支持 upgrade/downgrade，Alembic head 常量同步更新。

## 4. 覆盖率与质量门禁

统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- A-06：12 tests passed（9 个持久化集成测试、3 个 worker loop 单元测试）。
- 全后端：81 tests passed。
- statements/branch 综合覆盖率：89.37%，高于且未降低 80% 门禁；A-06 协调模块为 87%。
- Ruff：PASS。
- mypy strict：PASS，42 个 Python 源/测试文件无问题。
- 前端：1 test passed，覆盖率 100%；ESLint、TypeScript、Vite build 全部 PASS。
- 统一脚本：`All quality checks passed.`

## 5. 检查点说明与已知边界

- 当前工作目录不是 Git 仓库，无法创建技能建议的 GREEN/重构提交检查点；本文件保留等价的 RED、GREEN、重构和门禁证据。
- A-06 提供可注入实际扫描 handler 的持久化 worker loop；A-07/A-08 接入 Provider 与采集器后，进程入口再组装真实 handler。
- A-11 负责 HTTP schema、授权、409/404 语义和重复活动扫描策略；A-06 已提供其所需的持久化幂等入队原语。
