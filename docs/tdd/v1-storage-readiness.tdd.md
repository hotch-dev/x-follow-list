# V1 主存储与磁盘就绪检查 TDD 证据

> 日期：2026-09-16  
> 来源：阶段 A 后投产门禁审计；无新增计划文件  
> 工作流：`ecc:tdd-workflow`

## 用户旅程

运维希望在 API 接入流量前发现数据库、主存储不可写或磁盘余量不足，且健康响应不泄露本机路径。部署方可按容量策略配置最低剩余字节数，默认 2 GiB。

## RED → GREEN → 重构

| 阶段 | 检查点 | 实际命令与结果 |
| --- | --- | --- |
| RED | `98af624` | `.\.venv\Scripts\python.exe -m pytest tests/integration/test_readiness.py tests/unit/test_settings.py -q`：`5 failed, 2 passed`；失败原因是缺少存储/磁盘响应字段、阈值配置与写入探针。 |
| GREEN | `6db1059` | 同一目标命令：`7 passed in 5.09s`。 |
| 重构 | `db0a228` | 同一目标命令：`7 passed in 6.35s`；Ruff `All checks passed!`，mypy `Success: no issues found in 3 source files`。 |

## 测试规格

| 保证 | 测试 | 类型 | 结果 |
| --- | --- | --- | --- |
| 已迁移数据库、可写主存储与足够磁盘返回 200，并逐项报告状态 | `tests/integration/test_readiness.py::test_readiness_reports_migrated_writable_database` | 集成 | PASS |
| 未迁移数据库仍 fail closed | `tests/integration/test_readiness.py::test_readiness_fails_closed_when_database_is_not_migrated` | 集成 | PASS |
| 磁盘低于配置阈值返回 503，不暴露路径 | `tests/integration/test_readiness.py::test_readiness_rejects_low_disk_without_exposing_paths` | 集成 | PASS |
| 主存储写入失败返回 503，不暴露异常路径 | `tests/integration/test_readiness.py::test_readiness_rejects_unwritable_storage` | 集成 | PASS |
| 最低剩余空间从环境变量加载且拒绝负值 | `tests/unit/test_settings.py` | 单元 | PASS |

## 完整门禁与已知缺口

`pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`：后端 `195 passed`，综合覆盖率 `80.68%`，高于原有 `80%` 门禁；前端 `17 passed`，statements `98.08%`、branches `83.82%`、functions `96.70%`、lines `98.98%`；Ruff、mypy strict、ESLint、TypeScript、Vite build 均通过，最终 `All quality checks passed.`。

首次沙箱内运行时，三个本地 Chrome E2E 因 Windows 命名管道 `WinError 5` 无法启动 Playwright，导致该次运行 `3 failed, 192 passed`、覆盖率 `79.67%`。获准沙箱外重跑同一门禁后全部通过；不得将首次运行记为通过。没有访问 X 生产环境，也没有运行 AdsPower 测试。

本切片只覆盖 API 主存储与磁盘就绪，不包含 worker 心跳、浏览器能力检查或新扫描的磁盘预检；这些仍是投产前待办。
