# A-14 端到端、安全、性能与发布门禁 TDD 证据

> 日期：2026-09-15  
> 任务：A-14 端到端、安全、性能与发布门禁  
> 工作流：`ecc:tdd-workflow`，保留 RED → GREEN → 重构 → 覆盖率 → 证据检查点

## 1. 范围决定

用户明确决定 AdsPower 部分不再测试。A-10 代码和历史证据保留，但 A-14 及后续统一门禁不运行 AdsPower 专属 contract/integration/unit 测试，也不执行实机冒烟；这不是延期项，且不声明真实 AdsPower 环境通过。所有浏览器 E2E 只访问 `127.0.0.1` 本地模拟 X 页，从未访问 X 生产环境。

## 2. 用户旅程与风险映射

| 旅程/风险 | 自动化证据 |
| --- | --- |
| 可见 Direct Chrome 绑定、身份确认、两次 worker 扫描、快照 diff、`UNFOLLOWED_ME_AFTER_MUTUAL` 与 XLSX | `test_direct_chrome_completes_scan_diff_event_and_xlsx_journey` |
| 下载文件被篡改或 storage path 越界时统一 404 | `test_download_rejects_path_escape_and_hash_mismatch` |
| worker 崩溃后任务失败、租约可重新调度且 partial staging 立即清除 | `test_restart_recovery_immediately_removes_partial_staging` 及 A-06 fencing/kill 测试 |
| 5 万 followers + 5 万 following 的 diff、峰值 RSS | `test_fifty_thousand_members_per_side_stays_within_baseline` |
| 10 万 snapshot memberships 的关系分页/筛选 API p95 | `test_relationship_query_p95_with_fifty_thousand_per_side` |
| 在线一致备份、SHA-256、原子恢复和损坏备份不覆盖目标 | `test_backup_restore_is_atomic_verified_and_preserves_target_on_corruption` |
| API/worker/web、共享数据卷、健康检查、密钥、迁移、回滚和已知限制 | `test_release_package_documents_operable_deploy_recovery_and_limitations` |
| UI 的扫描终态、事件确认、授权错误和 XLSX 下载入口 | A-12/A-13 React 流程测试 |

既有安全套件继续覆盖 SSRF/CDP endpoint policy、重定向、凭据脱敏、CSRF/origin、跨用户资源枚举、公式注入及受管 profile 路径。故障套件继续覆盖事务每个检查点、未知 schema、中途断线、重复 cursor、租约丢失和迟到 worker fencing。

## 3. RED 证据

RED 检查点：`f01baf7`。

定向命令收集失败，明确暴露三个尚不存在的发布能力：

```text
ModuleNotFoundError: x_follow_list.worker.relationship_scan
ModuleNotFoundError: x_follow_list.release
ModuleNotFoundError: x_follow_list.operations
```

同一 RED 还预先定义了缺失发布文件、artifact 哈希篡改、恢复 staging 清理、完整 Direct Chrome 旅程和容量基线。没有用基础设施错误冒充 RED。

## 4. GREEN 与重构证据

GREEN 检查点：`217d717`。

- 新增 provider-independent `RelationshipScanJob`，由 `ScanWorker` 和 `BrowserSessionJob` 驱动采集、staging、原子快照与 artifact 发布。
- 成功提交在同一事务释放账号/profile 租约；启动恢复在标记遗留 run 失败时立即删除 partial staging。
- 下载前重新计算 SHA-256/大小，篡改、缺失或越界文件统一 404。
- 备份使用 SQLite Online Backup API，恢复前校验 SHA-256 与 integrity，并通过同目录临时文件原子替换。
- 增加 Docker Compose、生产配置模板、健康检查、迁移/回滚、备份恢复和已知限制文档。

GREEN 定向结果：27 passed；Direct Chrome 完整本地旅程 1 passed。

重构检查点：`f7ed3bf`。XLSX 下载与数据库备份共用 `storage.files.file_metadata`，SQLite copy 连接管理归并为单一实现。重构回归 5 passed，Ruff 与 mypy strict 通过。

后续校正 `1fe25f7` 将内存口径从结束时 RSS 改为进程峰值 RSS；`3d8e91b` 将完整 E2E 扩展到真实可见绑定和身份确认，而非预置已绑定账号。

## 5. 性能结果

本机固定夹具结果：

- 5 万/5 万 staging + diff + 状态提交：2.235342 秒，门禁 `< 60 秒`。
- 同一 Python worker 进程峰值 RSS：171,638,784 bytes（约 163.69 MiB），门禁 `< 1.5 GiB`。
- 10 万 snapshot membership、状态筛选和 50 条分页，20 次请求的 p95：0.141368 秒，门禁 `< 0.5 秒`。

数字是 2026-09-15 当前 Windows 开发机的回归基线，不外推为其他硬件的性能承诺。

## 6. 最终质量门禁

统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

结果：

- 后端：182 passed；综合 statements/branch coverage 80.37%。
- Ruff：PASS；mypy strict：PASS，102 个 Python 源/测试文件无问题。
- 前端：4 files、17 tests passed。
- 前端 statements 98.08%、branches 83.82%、functions 96.70%、lines 98.98%。
- ESLint、TypeScript project build、Vite production build：PASS。
- `docker compose -f deploy/docker-compose.yml config --quiet`：PASS（使用 `.env.example` 的临时副本，仅验证配置解析，随后删除）。
- 统一脚本输出：`All quality checks passed.`

覆盖率保持高于 80% 门禁。后端数量相较 A-13 减少，是因为按用户决定明确排除了三组 AdsPower 专属测试；没有跳过或禁用其他测试。

## 7. Git 检查点

`f01baf7 (RED) → 217d717 (GREEN) → f7ed3bf (重构) → 1fe25f7 (峰值 RSS 校正) → 3d8e91b (完整绑定 E2E)`

检查点位于 `main` 连续历史中，未 squash 或改写。
