# A-09 Direct Chrome Provider 与交互式绑定 TDD 证据

> 日期：2026-09-14  
> 任务：A-09 Direct Chrome Provider 与交互式绑定  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)  
> 工作流：`ecc:tdd-workflow`

## 1. 用户旅程与验收映射

| 用户旅程 | 自动化测试 | 验收项 |
| --- | --- | --- |
| 用户用系统管理的独立 profile 启动已安装 Google Chrome，不触碰日常 Chrome profile | `tests/contract/test_direct_chrome_provider.py` | AC-01、AC-12、AC-17 |
| 同一 profile 只被一个任务持有；关闭幂等且仅关闭当前任务启动的浏览器 | Direct Chrome contract suite、`tests/unit/test_browser_binding_job.py` | AC-12、AC-17 |
| API 创建 15 分钟交互式绑定会话，用户轮询身份、确认或取消；所有写操作要求认证、同源与 CSRF | `tests/integration/test_binding_api.py`、`tests/integration/test_browser_binding.py` | AC-01、AC-12 |
| worker 使用可见会话等待只读身份读取；超时、取消、健康失败均条件落终态并安全释放 | `tests/unit/test_browser_binding_job.py`、`tests/integration/test_browser_binding.py` | AC-01、AC-17 |
| 登录失效后重新验证只能接受原稳定 X user ID，成功恢复原账号，身份不符失败关闭 | `test_revalidation_restores_ready_only_for_the_same_detected_x_identity` | AC-01、AC-12 |
| Direct Chrome profile 跨关闭/重启保留状态，并通过本地 X 页完成首个真实 Provider→采集器纵向闭环；解绑删除受管 profile | `tests/e2e/test_direct_chrome_scan.py` | AC-01、AC-12、AC-17 |

## 2. RED 证据

| RED | 命令与失败原因 | 检查点 |
| --- | --- | --- |
| Direct Chrome 契约 | contract 目标因缺少 `x_follow_list.browser.direct_chrome` 在收集阶段失败 | `f8a043d` |
| 持久化绑定生命周期 | 集成目标因缺少 `x_follow_list.application.binding` 在收集阶段失败 | `0812d95` |
| 绑定 HTTP 契约 | 3 个旅程因路由不存在得到 404/缺少返回 ID | `eecf924` |
| 交互式绑定 worker | 收集阶段 `ModuleNotFoundError: x_follow_list.worker.browser_binding` | `47e3aca` |
| `REAUTH_REQUIRED` 重新验证 | 原有 5 项通过，新增旅程以缺少 `mark_reauth_required` 的 `AttributeError` 失败 | `74a20e1` |
| 重新验证 HTTP 契约 | 使用 `x_account_id` 创建重新验证任务时得到 422，而契约要求 202 | `1c2e64e` |

这些 RED 均由目标行为尚不存在产生，不是语法、依赖或环境故障。Playwright 在受限沙箱创建 Windows 命名管道时出现的 `WinError 5` 不计作业务 RED；同一 E2E 经授权运行后通过。

## 3. GREEN 与重构结果

- `DirectChromeProvider` 使用 `launch_persistent_context(channel="chrome")`，绑定与扫描默认可见，不加 stealth 参数；profile 引用经过格式、绝对路径、目录包含和日常 Chrome 根目录隔离校验。
- Provider 创建带系统标记的独立目录，进程内拒绝重复获取；启动错误只返回稳定错误码，不回显路径、凭据或 Playwright 原始诊断。
- revision `0005` 创建独立 `browser_bind_sessions` 队列、claim token、15 分钟到期、检测身份和用户确认字段；revision `0006` 增加重新验证目标账号，并使活动 profile 唯一约束只覆盖未终结任务。
- `BrowserBindingService` 用 `BEGIN IMMEDIATE` 和条件更新实现 create/claim/detect/confirm/cancel/expire/fail；只有确认后才创建账号和 OWNER membership。
- `BrowserBindingJob` 强制可见请求，先健康检查，再等待可注入的只读身份读取器；超时为 `BIND_TIMEOUT`，Provider 错误码经过白名单化，所有已有会话均在 `finally` 释放。
- 登录失效可将账号标记为 `REAUTH_REQUIRED`；扫描入队原语已只接受 `READY` 账号。重新验证复用原 provider/profile，且稳定 X ID 必须与原账号一致。
- `/api/v1/x-account-bind-sessions` 支持新绑定与重新验证、查询、确认、取消；请求模型禁止额外字段，响应不包含密码、cookie、claim token 或 worker ID。
- E2E 夹具服务器被提取为共享 fixture；真实安装版 Chrome 验证 profile 重启持久性、A-08 完整采集和解绑删除。

## 4. 测试规格与结果

| 测试层 | 重点保证 | 最终结果 |
| --- | --- | --- |
| Provider contract | 能力、可见/显式 headless、路径隔离、重复获取、关闭/删除、失败脱敏 | PASS |
| Worker unit | 身份检测、默认可见、超时、健康失败、所有路径释放 | 3 passed |
| Persistence integration | 15 分钟、并发 claim、确认事务、超时/取消/重启、owner 隔离、重新验证 | 6 passed |
| API integration | auth/CSRF、轮询/确认/取消、secret 字段拒绝、重新验证 | 3 passed |
| Chrome E2E | profile 重启持久、模拟 X 扫描、受管 profile 删除及 A-08 失败矩阵回归 | 2 passed |
| 全后端 | contract、unit、integration、E2E | 161 passed |

## 5. 覆盖率与质量门禁

最终统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- 后端：161 passed，无 skipped/disabled tests。
- statements/branch 综合覆盖率：88.73%，高于且未降低 80% 门禁。
- A-09 核心模块：`application.binding` 80%、`browser.direct_chrome` 81%、`worker.browser_binding` 84%、`api.binding` 94%。
- Ruff：PASS。
- mypy strict：PASS，75 个 Python 源/测试文件无问题。
- 前端：1 test passed，覆盖率 100%；ESLint、TypeScript、Vite build 全部 PASS。
- 统一脚本：`All quality checks passed.`

## 6. Git 检查点

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| RED 1 / GREEN 1 | `f8a043d` / `8141acb` | Direct Chrome 契约与隔离实现 |
| RED 2 / GREEN 2 | `0812d95` / `8b824ae` | 持久化绑定生命周期与迁移 |
| RED 3 / GREEN 3 | `eecf924` / `9d8ee10` | 绑定 API、认证与 CSRF |
| E2E | `b272d7f` | 真实 Chrome profile 重启、扫描和删除 |
| RED 4 / GREEN 4 | `47e3aca` / `ec720eb` | 交互式绑定 worker |
| RED 5 / GREEN 5 | `74a20e1` / `615cc5e` | `REAUTH_REQUIRED` 与稳定身份重新验证 |
| RED 6 / GREEN 6 | `1c2e64e` / `b87f5ae` | 重新验证 API |
| REFACTOR | `5218a53` | 共享 E2E fixture 与测试包边界 |

所有检查点位于当前 `main` 连续提交链，未 squash 或改写。

## 7. 已知边界

- 绑定 worker 将“等待并只读识别当前账号”定义为可注入、版本化的身份读取边界，自动化测试使用脱敏身份替身；没有访问 X 生产环境，也没有把易变 X 内部 endpoint/hash 固化为长期契约。
- worker 进程入口的持续轮询与实际 X 身份适配器将在扫描/账号应用服务组装阶段接线；本任务已交付其持久化 claim 和 Provider 无关执行单元。
- `/x-accounts/{id}` 的完整账号解绑事务、授权审计和历史保留策略属于 A-11；A-09 已实现并真实验证 Direct Chrome 受管 profile 的安全删除原语。
