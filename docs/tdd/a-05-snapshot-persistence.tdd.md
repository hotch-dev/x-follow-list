# A-05 快照持久化与原子提交 TDD 证据

> 日期：2026-09-14  
> 任务：A-05 快照持久化与原子提交  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程

- 作为扫描 worker，我希望分页结果先去重写入 staging，只有 followers 与 following 都明确到达终点后才提升为正式快照。
- 作为 OWNER，我希望失败或崩溃扫描绝不覆盖上一成功基线、递增 streak 或生成虚假事件。
- 作为任务恢复器，我希望同一 run 重放不会创建重复快照或事件。
- 作为运维者，我希望过期 staging 可按时间安全清理，并能处理每侧 5 万成员。

## 2. RED 证据

先新增首次/二次快照、重复 ID、事件/streak、重放、不完整阻断和故障注入集成测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_snapshot_commit.py -q
```

结果：收集阶段因 `ModuleNotFoundError: No module named 'x_follow_list.application.snapshots'` 失败，属于目标应用服务不存在产生的有效 RED。

重构阶段增加“ABSENT snapshot 后重新出现”回归测试。首次运行在 `previous=None + previous_state=ABSENT` 处 fail closed；提交服务随后把持久化 ABSENT 状态还原为逻辑空 membership，测试转为 GREEN。

## 3. GREEN 与原子性证据

| 保证 | 自动化验证 | 结果 |
| --- | --- | --- |
| staging 以 run/type/X user ID 去重并更新展示缓存 | 二次重复 `99` 夹具 | PASS |
| 两侧 terminal 后才允许提升 | incomplete scan 测试 | PASS |
| 首次 snapshot 只建立状态，不生成变化事件 | 两快照集成测试 | PASS |
| 第二次 snapshot 生成 LOST_FOLLOWER 与 UNFOLLOWED_ME_AFTER_MUTUAL | 两快照集成测试 | PASS |
| streak 跨成功快照递增并在状态变化时更新 | state 查询断言 | PASS |
| 同 run 重放返回同一 snapshot，不重复事件 | replay 断言 | PASS |
| snapshot、memberships、states、events、run、account 为单事务 | 6 阶段故障注入 | PASS |
| 任一注入故障后无正式 snapshot，run 未伪装成功 | rollback 查询断言 | PASS |
| ABSENT 不写 membership，但之后可正确重新出现 | ABSENT 回归测试 | PASS |
| 24 小时式 cutoff 清理旧 staging，保留新任务 | stale cleanup 测试 | PASS |
| followers/following 各 50,000 成员批量处理 | 固定 5 万/5 万夹具 | PASS（低于 60 秒基线） |

故障注入点为：`after_snapshot`、`after_memberships`、`after_states`、`after_events`、`after_run_update`、`after_account_update`。所有点均验证事务完整回滚。

## 4. 数据库变更

revision `0003_a05_snapshots` 新增：

- `scan_staging_memberships`、`scan_staging_progress`；
- `relationship_snapshots`、`snapshot_memberships`；
- `relationship_states`、`relationship_events`。

正式快照以 `scan_run_id` 唯一，事件以领域 dedupe key 唯一。SQLite 写事务使用 `BEGIN IMMEDIATE` 串行化同库提交；staging 仅在正式事务提交后清理。

## 5. 覆盖率与质量门禁

- A-05 集成测试：10 tests passed（含参数化故障点与大夹具）。
- 全后端：69 tests passed。
- statements/branch 综合覆盖率：91.39%，高于 80% 门禁。
- Ruff：PASS。
- mypy strict：PASS，38 个 Python 源/测试文件无问题。
- 前端测试：1 passed，覆盖率 100%。
- ESLint、TypeScript、Vite production build：PASS。
- 统一脚本结果：`All quality checks passed.`

## 6. 已知边界

- A-05 接收“完整性已通过”的 terminal 标记；游标、骤降、解析拒绝等完整性判断由 A-08 collector/guard 产生。
- worker claim、lease、fencing 与崩溃恢复在 A-06 接入；当前事务通过 SQLite 写锁和 run/snapshot 唯一键保证本地重放安全。
- staging cleanup 提供可调用用例；A-06 worker 启动流程负责调用它。
