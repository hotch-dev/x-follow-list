# A-13 XLSX 生成与安全下载 TDD 证据

> 日期：2026-09-15
> 任务：A-13 XLSX 生成与安全下载
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)
> 工作流：`ecc:tdd-workflow`，保留 RED → GREEN → 重构 → 覆盖率 → 证据检查点

## 1. 用户旅程与测试映射

| 用户旅程 | 自动化证据 |
| --- | --- |
| OWNER 从一次指定成功 scan/snapshot 生成阶段 A 的 6 张 XLSX 工作表 | `test_builds_six_snapshot_pinned_sheets_and_downloads_with_safe_headers` |
| 外部用户名/显示名不能成为 Excel 公式，空值保持为空 | `test_external_text_is_never_interpreted_as_an_excel_formula` 与工作簿内容断言 |
| UTC 与部署 IANA 时区同时呈现 | `captured_at_utc`、`captured_at_local` 和 `Asia/Taipei` 断言 |
| 5 万行使用 openpyxl write-only 流式写入并保持有界内存 | `test_write_only_workbook_keeps_fifty_thousand_rows_below_memory_budget` |
| 文件只通过同卷临时文件原子发布，中途失败不留下半文件，失败记录可幂等重建 | `test_failed_publish_leaves_no_partial_file_and_can_be_rebuilt` |
| artifact 保存 SHA-256、大小、30 天过期时间和删除状态；重复请求返回同一 artifact | artifact API 集成测试 |
| OWNER/成员可下载，跨用户和已删除 artifact 均返回 404 | `test_membership_download_authorization_hides_foreign_and_deleted_artifacts` |
| 下载返回附件 Content-Disposition、正确 XLSX MIME 和 `nosniff` | 安全响应头断言 |
| React 扫描页仅对成功任务提供生成入口，成功后显示下载链接，失败时不显示陈旧链接 | `ScansPage.test.tsx` 两个 A-13 旅程 |

映射 AC-09、AC-13、AC-14。测试只使用 SQLite 临时库、内存 HTTP 客户端和本地文件，不访问 X 生产环境，也不执行 AdsPower 实机测试。

## 2. RED 证据

RED 检查点：`4f3fcae`。

后端命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_xlsx_streaming.py tests\integration\test_xlsx_artifacts.py -q
```

结果：两个测试模块均因 `ModuleNotFoundError: No module named 'x_follow_list.artifacts'` 收集失败，目标 artifact 模块尚不存在。

前端命令：

```powershell
npm test -- --run src/ScansPage.test.tsx
```

结果：4 tests 中既有 2 passed；新增成功和失败旅程 2 failed，均因找不到名为“生成 XLSX”的按钮。Windows 受限沙箱首次出现 `spawn EPERM`，同一命令在获准环境重跑后才记录上述有效业务 RED；基础设施失败未计作 RED。

## 3. GREEN 证据

GREEN 检查点：`c616222`。

- 新增 write-only 工作簿生成器、6 张阶段 A 工作表和公式注入防护。
- 所有查询以传入 `scan_run_id` 对应的唯一 snapshot 为根；事件也限定相同 run，不读取漂移的“当前最新”结果。
- 同卷临时文件完成后计算 SHA-256 和大小，再使用原子替换发布；元数据记录 `PENDING/READY/FAILED/DELETED`、过期和删除时间。
- 同一 `scan_run_id + kind` 有数据库唯一约束；重复生成返回同一 READY artifact，FAILED artifact 可重建。
- 下载从 `x_account_memberships → scan_runs → artifacts` 授权链查询，跨用户、过期、删除、越界路径和缺失文件统一 404。
- React 成功任务可生成并下载；生成错误保持可见且不提供陈旧下载链接。

验证结果：A-13 后端 5 passed；A-13 加迁移、授权和扫描 API 回归 16 passed；前端目标 4 passed；Ruff、strict mypy、ESLint、TypeScript 和 Vite build 通过。

## 4. 重构与门禁回归证据

重构检查点：`9c1bb2d`。

- 将附件生成/下载端点从监控路由拆分到 `api/artifacts.py`。
- 合并正常写入和故障注入的 workbook 循环，使两条路径共用相同的转义与 write-only 逻辑。
- 重构后后端目标/扫描 API 回归 13 passed，前端 4 passed，Ruff 与 strict mypy 通过。

首次完整门禁发现迁移给 `users` 添加时区列后，25 个既有无列名测试插入与表列数失配。没有降低或修改既有测试；时区来源改为类型化 `X_FOLLOW_LIST_DISPLAY_TIMEZONE` 部署配置（默认 UTC），artifact 唯一约束保留。修正提交：`d440b6a`。受影响的 browser binding、scan coordination、snapshot commit 与 A-13 回归随后 30 passed。

## 5. 测试规格

| # | 保证 | 测试类型 | 结果 |
| --- | --- | --- | --- |
| 1 | 工作簿严格包含 `Summary`、`UnfollowedMe`、`NonFollowers`、`NewFollowers`、`Followers`、`Following` | 集成 | PASS |
| 2 | Summary 固定 scan/snapshot ID、统计和 UTC/本地时区 | 集成 | PASS |
| 3 | 6 张表的账号行来自指定 snapshot；事件表限定指定 run | 集成 | PASS |
| 4 | `= + - @` 开头的外部文本被写为纯文本，空显示名仍为空 | 单元/集成 | PASS |
| 5 | 50,000 数据行以 write-only 模式生成，Python 分配峰值低于 128 MiB | 单元/性能 | PASS |
| 6 | 写入故障清理 `.tmp` 和 `.xlsx`，记录 FAILED 后可重建 READY | 集成/故障注入 | PASS |
| 7 | 重复构建保持相同 artifact ID，SHA-256 和大小与下载体一致 | 集成 | PASS |
| 8 | account 成员可下载，外部用户、已删除或不可用 artifact 返回 404 | 集成/授权 | PASS |
| 9 | 下载头包含安全附件名、正确 MIME 和 `X-Content-Type-Options: nosniff` | 集成/安全 | PASS |
| 10 | React 提供成功/失败完整下载旅程 | 前端流程 | PASS |

独立 50,000 行测量命令使用与测试相同的生成器和 `tracemalloc`，结果为 `peak_bytes=473301`（0.45 MiB），生成文件 662,736 bytes。该数字是 Python 分配峰值，不冒充进程 RSS；测试硬门槛为 128 MiB。

## 6. 最终覆盖率与质量门禁

统一命令（用户提供的路径少一个反斜杠，使用已验证存在的 `15485\.cache` 路径）：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

最终结果：

- 后端：209 passed；综合 statements/branch coverage 88.52%。
- A-13 `api/artifacts.py`：90%；`artifacts/xlsx.py`：90%。
- Ruff：PASS；mypy strict：PASS，91 个 Python 源/测试文件无问题。
- 前端：4 files、17 tests passed。
- 前端 statements 98.08%、branches 83.82%、functions 96.70%、lines 98.98%。
- ESLint、TypeScript project build、Vite production build：PASS。
- 统一脚本最终输出：`All quality checks passed.`

所有覆盖率维度保持在 80% 门禁以上，无 skipped/disabled 测试。

## 7. Git 检查点与后续范围决定

检查点位于 `main` 连续历史中，未 squash 或改写：

`4f3fcae (RED) → c616222 (GREEN) → 9c1bb2d (重构) → d440b6a (全门禁兼容修正)`

本任务没有执行 AdsPower 实机测试，也没有将其伪造为通过。用户在 A-14 已决定 AdsPower 不再测试，该项不再作为延期；状态以 [A-10 证据](./a-10-adspower-provider.tdd.md) 和 [Browser Provider 兼容性矩阵](../browser-provider-compatibility.md) 为准。
