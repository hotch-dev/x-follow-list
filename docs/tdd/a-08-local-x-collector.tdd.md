# A-08 模拟 X 页、导航器、响应收集器和解析器 TDD 证据

> 日期：2026-09-14
> 任务：A-08 模拟 X 页、导航器、响应收集器和解析器
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)
> 工作流：`ecc:tdd-workflow`

## 1. 用户旅程

- 作为扫描 worker，我希望在页面导航前注册响应监听，并从自然产生的 JSON 响应提取稳定 X user ID，以免漏掉首批载荷或依赖易变 endpoint hash。
- 作为关系数据用户，我希望只有 followers/following 两侧均到达可信终点并通过完整性阈值后才发布结果，以免失败扫描覆盖上一份成功基线。
- 作为系统用户，我希望未知 schema、重复 cursor、连续空载荷、异常骤降、挑战页和中途断线都失败关闭，并返回可操作错误，而不是伪造空关系集。
- 作为适配器开发者，我希望使用本地可配置 SPA 和版本化脱敏载荷稳定复现采集行为，而无需访问 X 生产环境。

## 2. 任务、RED 与 GREEN 证据

| 计划任务 | 测试目标 | RED 证据 | GREEN / 重构证据 |
| --- | --- | --- | --- |
| 版本化载荷、稳定 ID 去重、完整性阈值 | parser/guard unit + response integration | `pytest ...test_relationship_payload_parser.py ...test_completeness_guard.py ...test_response_collection.py -q`：3 个收集错误，`No module named 'x_follow_list.collector'` | 同一目标：20 passed；最终相关非浏览器目标：22 passed |
| 本地 SPA、DOM Navigator、失败关闭与双侧发布 | pipeline integration + Chrome E2E | `pytest tests/integration/test_collection_pipeline.py tests/e2e/test_local_x_fixture.py -q`：2 个收集错误，缺少 `collector.pipeline` 与 `DomNavigator` | 同一目标在真实 Chrome 中：3 passed；E2E 单独重构复验：1 passed |
| 不依赖固定 endpoint/hash 的结构分类 | response integration | `pytest tests/integration/test_response_collection.py -q`：1 failed，变化 URL 的有效载荷被过滤后触发 `INCOMPLETE_SUSPECTED` | 同一目标：3 passed；真实 Chrome E2E：1 passed |

RED 均由新测试引用或执行缺失目标行为导致，不是语法错误、依赖缺失或无关回归。Chrome E2E 在受限沙箱中创建 Windows 命名管道时出现 `PermissionError [WinError 5]`，按授权在沙箱外运行后通过；该权限错误不计作业务 RED。

## 3. 测试规格

| # | 保证 | 测试文件 | 类型 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | v1 载荷解析稳定 ID 与展示缓存，按 ID 去重且不以 username/display name 关联 | `tests/unit/test_relationship_payload_parser.py` | unit | PASS |
| 2 | 未知根结构、未知 schema 和错误关系侧失败关闭，缺失 ID 计入拒绝数 | `tests/unit/test_relationship_payload_parser.py` | unit | PASS |
| 3 | 必须到达显式终点；空账号必须有明确空确认 | `tests/unit/test_completeness_guard.py` | unit | PASS |
| 4 | 重复 cursor 无新增 ID、连续两页空载荷、拒绝比例/累计数越界均阻断 | `tests/unit/test_completeness_guard.py` | unit | PASS |
| 5 | 精确页面总数使用 `max(20, total×2%)` 容差 | `tests/unit/test_completeness_guard.py` | unit | PASS |
| 6 | 任一侧减少至少 50 且超过 20% 时要求第二次相同完整集合确认 | `tests/unit/test_completeness_guard.py` | unit | PASS |
| 7 | response listener 在导航前注册，结束或异常后移除 | `tests/integration/test_response_collection.py` | integration | PASS |
| 8 | JSON MIME + 结构分类替代固定 endpoint/hash；无关 JSON 被忽略 | `tests/integration/test_response_collection.py` | integration | PASS |
| 9 | 只有双侧全部完整后才调用发布回调，第二侧失败不发布第一侧结果 | `tests/integration/test_collection_pipeline.py` | integration | PASS |
| 10 | 本地 SPA 在真实 Chrome 中完成正常分页并得到 `101/102/103` 去重集合 | `tests/e2e/test_local_x_fixture.py` | E2E | PASS |
| 11 | 本地 SPA 可重复触发未知 schema、cursor 循环、空载荷、挑战页和第二页断线，全部失败关闭 | `tests/e2e/test_local_x_fixture.py` | E2E | PASS |

## 4. 实现结果

- `VersionedRelationshipParser` 与 `PayloadClassifier` 使用结构特征识别版本化载荷，输出不可变 `ParsedPage`；拒绝无稳定 ID 的条目并以 X user ID 去重。
- `CompletenessGuard` 实施终点、空账号确认、cursor、连续空载荷、精确总数、拒绝条目和异常骤降复核门禁，所有门禁不可关闭。
- `ResponseCollector` 在任何导航前安装 listener，按 JSON MIME 接收候选响应，再按结构分类，不依赖 X 内部 endpoint 或 query hash。
- `DomNavigator` 仅负责语义入口、分页加载、终点与挑战/断线识别，不从 DOM 卡片拼装关系数据。
- `RelationshipCollectionPipeline` 在两侧均完整前不调用发布边界，因此失败结果不会进入正式快照/event 流程。
- 本地 SPA 夹具可配置正常分页、重复 cursor、连续空载荷、未知 schema、挑战页和中途断线；全部使用脱敏固定数据。

## 5. 覆盖率与质量门禁

最终命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- 后端：137 tests passed，无 skipped/disabled tests。
- statements/branch 综合覆盖率：90.21%，高于 80% 门禁。
- A-08 模块：completeness 100%、models 100%、navigation 87%、parser 91%、pipeline 100%、response 100%。
- Ruff：PASS。
- mypy strict：PASS，64 个 Python 源/测试文件无问题。
- 前端：1 test passed，覆盖率 100%；ESLint、TypeScript、Vite build 全部 PASS。
- 统一脚本：`All quality checks passed.`

## 6. Git 检查点与合并证据

| 阶段 | 提交 | 证据 |
| --- | --- | --- |
| RED 1 | `a3e0736` | 缺失 parser/guard/response collector 的失败契约 |
| GREEN 1 | `7bbd821` | parser、完整性门禁和响应收集 20 tests passed |
| RED 2 | `dc73ad8` | 缺失 pipeline/DOM Navigator 的失败旅程 |
| GREEN 2 | `33923cf` | 本地 SPA + Chrome E2E 与双侧发布转绿 |
| REFACTOR | `eef3784` | 顺序解析、显式 classifier、真实中途断线后同一 E2E 通过 |
| RED 3 | `fd16080` | 固定 endpoint 过滤的回归测试失败 |
| GREEN 3 | `f883ba3` | MIME + 结构分类转绿且 Chrome E2E 继续通过 |

这些检查点均位于当前 `main` 的 A-08 连续提交链中，未 squash 或改写。

## 7. 已知边界

- 本任务只访问本地 SPA，不访问 X 生产环境，也不固化真实 X 内部字段路径；真实结构升级需新增脱敏版本化夹具和 parser 版本。
- `DomNavigator` 当前通过本地夹具的语义元素验证；A-09 将在 Direct Chrome Provider 的独立 profile 中组装真实浏览器会话。
- 异常骤降的第二次相同集合由调用方通过 `confirmed_drop_ids` 提供；持久化复核候选与任务状态将在扫描应用服务组装时接入。
- pipeline 的发布回调是正式 snapshot 边界的抽象；A-05 已独立验证事务原子性，后续 worker 组装会把二者连接并携带 A-06 fencing。
