# B-01 名单规则与黑名单冲突闭环 TDD 证据

> 日期：2026-09-19  
> 工作流：`ecc:tdd-workflow`  
> 范围：系统内部白名单/业务黑名单；不调用 X 拉黑或取关接口

## 用户旅程

OWNER 可以为自己的 X 账号添加或移除互斥的白名单/业务黑名单规则并记录原因。新增业务黑名单时，系统立即依据最新成功快照判断是否仍在关注；后续每次成功完整扫描再次评估 `G ∩ B`。命中项出现在总览与重点待处理页，并进入该快照的 XLSX。持续命中不重复创建事件，取关或删除规则后自动解决，再次关注则开启新 episode。

## RED → GREEN → 重构

| 阶段 | 检查点 | 实际命令与结果 |
| --- | --- | --- |
| RED（领域/API） | `bb59ddb` | `pytest tests/integration/test_account_rules.py tests/integration/test_migrations.py -q`：`4 failed, 1 passed`；规则 API 为 404，迁移缺少 `account_rules`。 |
| GREEN（领域/API） | `6b81494` | 同一目标用例 `5 passed`；含快照提交回归的目标集 `14 passed`，Ruff 与 mypy strict 通过。 |
| RED（React） | `1daba4e` | `ActionItemsPage` 因 API 仍只查询取消关注事件而失败；`RulesPage` 因页面尚不存在而编译失败。 |
| GREEN（React） | `9c6a044` | `vitest` 目标集 `3 files, 10 tests passed`；TypeScript 与 ESLint 通过。 |
| RED（终态保护） | `6691259` | 已解决黑名单事件仍可确认，预期 409、实际 200：`1 failed`。 |
| GREEN（终态保护） | `9e426d2` | 事件查询/确认目标集 `12 passed`。 |
| RED（XLSX） | `c683332` | 快照固定的 `BlocklistConflicts`、`Rules` 工作表缺失：`2 failed`。 |
| RED（历史固定） | `9af60a7` | 迁移测试因缺少 `snapshot_rule_hits` 失败，证明仅查询当前规则无法可靠重建历史导出。 |
| GREEN（历史固定） | `4fb9524` | 快照提交时固定规则命中并生成 8 张工作表；XLSX/迁移目标集 `11 passed`，Ruff 与 mypy strict 通过。 |
| 重构 | `b7ebe21` | 复用快照规则命中和前端 mutation/invalidation；后端目标集 `17 passed`、前端目标集 `10 passed`，静态检查通过。 |
| 安全回归 | `0e48400` | 规则目标集 `6 passed`；覆盖幂等键载荷冲突、删除版本冲突以及跨用户账号/规则统一 404。 |

## 验收覆盖

| 保证 | 自动化证据 |
| --- | --- |
| 规则增删、唯一性与白名单/业务黑名单互斥 | `tests/integration/test_account_rules.py::test_rule_api_is_owner_scoped_mutually_exclusive_and_audited` |
| 创建请求幂等；相同键不同载荷返回 409；删除使用版本防止覆盖并发修改 | 同上 |
| OWNER/membership 授权隔离，跨用户账号和规则均返回 404 | `test_foreign_account_and_rule_ids_are_not_discoverable` |
| 规则变更记录操作人、原因和前后值 | `test_rule_api_is_owner_scoped_mutually_exclusive_and_audited` |
| 最新快照即时命中；无成功快照时明确提示需扫描 | `test_blocklist_conflict_uses_latest_snapshot_and_opens_new_episode_after_resolution`、`test_rule_without_snapshot_requests_scan_and_delete_resolves_active_conflict` |
| 首次基线可产生规则冲突；持续命中只更新；自动解决；再次发生新 episode | 同上及 `test_scan_commit_reconciles_persistent_and_resolved_blocklist_conflicts` |
| 已解决事件不能再次确认 | `tests/integration/test_monitoring_api.py::test_resolved_action_item_cannot_be_acknowledged` |
| 总览、重点待处理和规则管理 UI | `web/src/pages/DashboardPage.test.tsx`、`ActionItemsPage.test.tsx`、`RulesPage.test.tsx` |
| XLSX 使用指定 scan/snapshot 的规则命中，不受后续规则修改影响 | `tests/integration/test_xlsx_artifacts.py::test_xlsx_rule_sheets_are_pinned_to_the_requested_snapshot` |
| XLSX 外部文本公式注入防护继续生效 | `tests/integration/test_xlsx_artifacts.py::test_xlsx_generation_is_atomic_idempotent_and_formula_safe` |

## 边界与未完成项

- 黑名单是本系统内部业务标记；没有访问 X 生产环境，也不会代理拉黑或取关。
- 按用户决定不再执行 AdsPower 自动化或实机测试；现有 Provider 代码及历史证据保留，但本切片不声明其真实环境通过。
- 本切片包含站内待处理项和 XLSX，不包含 Telegram/SMTP、通知意图/投递与重试；这些仍是阶段 B 后续工作。
- 白名单规则已可维护并与黑名单互斥；长期未回关阈值提醒尚未实现，因此其白名单抑制也仍待该模块完成。

## 完整门禁

`pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe` 最终结果：

- 后端 `201 passed`，综合覆盖率 `80.42%`，满足 `80%` 门禁；
- 前端 `6 files / 21 tests passed`，statements `97.95%`、branches `84.41%`、functions `96.29%`、lines `98.71%`；
- Ruff、mypy strict、ESLint、TypeScript 和 Vite production build 全部通过；
- 最终输出 `All quality checks passed.`。

第一次最终门禁为 `1 failed, 200 passed`：`test_readiness_rejects_low_disk_without_exposing_paths` 使用“运行开始时可用空间 + 1 字节”作为阈值，迁移期间系统空间增加会让测试误判。`c20f931` 将阈值改为确定性不可满足值，目标用例 `4 passed`，随后上述全量门禁通过。这次失败不记为通过。

门禁执行了仅访问本地模拟页的 Direct Chrome E2E；`scripts/check.ps1` 明确排除了 AdsPower 测试，全程未访问 X 生产环境。
