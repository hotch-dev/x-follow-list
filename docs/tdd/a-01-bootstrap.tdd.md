# A-01 工程骨架 TDD 证据

> 日期：2026-09-13  
> 任务：A-01 初始化单仓库工程  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程

- 作为开发者，我希望启动 API 并读取 liveness，以确认服务进程可运行。
- 作为运维者，我希望 worker 可在收到停止请求时安全退出，以便后续加入长时间任务。
- 作为管理端用户，我希望 React shell 清楚识别当前产品，以确认前端启动成功。

## 2. RED 证据

| 目标 | 命令 | 预期失败 |
| --- | --- | --- |
| API + worker | `.venv\\Scripts\\python.exe -m pytest tests\\unit\\test_api_bootstrap.py tests\\unit\\test_worker_bootstrap.py -q` | `ModuleNotFoundError: x_follow_list.api` 和 `x_follow_list.worker` |
| React shell | `node node_modules\\vitest\\vitest.mjs run` | `Failed to resolve import "./App"` |

两组 RED 都在测试依赖安装完成后执行，失败直接来自目标实现尚不存在。首次 Vitest 在沙箱内因子进程 `spawn EPERM` 的运行不计作 RED；在允许本地构建子进程后重跑，才记录上表的有效 RED。

## 3. GREEN 与重构证据

| 保证 | 测试 | 类型 | 结果 |
| --- | --- | --- | --- |
| `GET /health/live` 返回稳定服务身份和 `ok` | `tests/unit/test_api_bootstrap.py::test_liveness_reports_service_identity` | API 单元/集成 | PASS |
| API CLI 使用工厂模式绑定 loopback:8000 | `tests/unit/test_api_bootstrap.py::test_api_entrypoint_starts_the_app_factory` | 单元 | PASS |
| worker 运行循环在停止请求后结束 | `tests/unit/test_worker_bootstrap.py::test_worker_can_be_stopped_safely` | async 单元 | PASS |
| worker CLI 启动 async runtime 并处理 Ctrl+C | `tests/unit/test_worker_bootstrap.py` 两个 entrypoint 测试 | 单元 | PASS |
| React shell 显示产品标题和初始化状态 | `web/src/App.test.tsx` | 前端组件 | PASS |

后端 GREEN：5 tests passed。前端 GREEN：1 test passed。重构将 FastAPI `TestClient` 替换为 `httpx.ASGITransport`，并将 worker 测试从“检查布尔值”增强为“等待运行循环实际退出”。

## 4. 覆盖率与质量门禁

- 后端：87.88% statements/lines，高于 80% 门禁。
- 前端：100% statements/branches/functions/lines（当前 React shell 范围）。
- Ruff：PASS。
- mypy strict：PASS，7 个 Python 源/测试文件无问题。
- ESLint：PASS。
- TypeScript project build：PASS。
- Vite 8.2.2 production build：PASS。

最终统一验证命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath '<Node 24 executable>'
```

结果：`All A-01 checks passed.`

## 5. 已知缺口

- A-01 只建立工程和生命周期骨架，readiness、数据库和真实 worker 轮询从 A-02 开始。
- 未下载 Playwright 浏览器二进制；Direct Chrome 在 A-09 使用本机 Google Chrome。
- 当前目录不是 Git 仓库，因此没有创建 RED/GREEN 检查点提交；本文档保留了等价证据。
