# A-07 Browser Provider 注册表与契约套件 TDD 证据

> 日期：2026-09-14
> 任务：A-07 Browser Provider 注册表与契约套件
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程与测试映射

| 用户旅程 | 自动化测试 |
| --- | --- |
| 新 Provider 通过稳定 code 注册并向上层暴露统一能力，不要求采集逻辑识别具体实现 | `test_registry_resolves_stable_provider_codes_and_lists_capabilities`、`test_provider_implements_shared_protocol` |
| Provider 配置带明确版本，只保存 secret reference，并拒绝内联凭据 | `test_config_is_versioned_and_does_not_accept_inline_secrets` |
| Provider API 端点默认限于 loopback/私网或明确许可主机，且拒绝危险协议、凭据、公网地址和 fragment | 三个 `test_*endpoint*` |
| Provider 可验证配置、列出 profile、获取并健康检查统一会话 | `test_provider_validates_config_and_lists_profiles`、`test_acquired_session_is_healthy_and_close_is_idempotent` |
| 同一 profile 在释放前不可被第二个任务获取，释放后可安全复用 | `test_profile_cannot_be_acquired_twice_until_the_session_is_released` |
| 会话幂等释放；任务只停止自己启动的浏览器，对原本已运行的浏览器只断开 | 三个 `test_*close*` / `test_session_only_stops_*` |
| worker 通过注册表执行 Provider 无关的浏览器工作，成功或异常均释放会话 | 两个 `test_browser_session_job_*` |
| Provider 异常、配置表示与结构化日志不泄露 token、CDP URL 或危险端点 | `test_provider_failures_do_not_expose_sensitive_values`、两个日志/异常脱敏测试 |

上述旅程主要映射 AC-17，并为 AC-12 的 fail-closed Provider 边界提供基础。Direct Chrome 与 AdsPower 的真实适配分别由 A-09、A-10 实现并加入同一契约套件。

## 2. RED 证据

先创建 Provider 共享契约、注册表与端点/凭据安全测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\contract\test_browser_provider_contract.py tests\unit\test_browser_provider_registry.py tests\unit\test_browser_provider_security.py -q
```

结果：收集阶段出现 3 个 `ModuleNotFoundError: No module named 'x_follow_list.browser'`，证明目标契约与实现尚不存在。

重构阶段再先增加 worker/Provider 组合测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_browser_session_job.py -q
```

结果：收集阶段出现 `ModuleNotFoundError: No module named 'x_follow_list.worker.browser_session'`，证明 worker 尚无 Provider 无关的会话执行适配层。

## 3. GREEN 与重构结果

- 定义运行时可检查的 `BrowserProvider` / `BrowserSession` Protocol，以及版本化配置、profile 摘要、能力报告和会话请求值对象。
- 注册表只接受稳定大写 `provider_code`，拒绝重复注册，并用不回显查询 code 的安全错误处理未知 Provider。
- 配置递归拒绝 token/password/secret 等内联敏感字段，只接受受限格式的 `env://`、`keyring://` 或 `secret://` 引用。
- 端点策略拒绝危险 scheme、URL credentials、fragment、公网 IP 和未许可 hostname；错误消息不回显原始 URL。
- `FakeBrowserProvider` 实现完整共享契约、profile 互斥、启动所有权、健康失败和幂等/并发安全释放，不启动真实浏览器。
- `BrowserSessionJob` 将 A-06 worker handler 与注册表组合，采集代码仅接收统一 session；健康检查或业务处理失败时仍在 `finally` 中释放。
- 重构补充并发 `close()`、profile 重入、处理异常释放、配置版本/不可变性、嵌套端点和敏感错误文本测试，目标测试共 32 个全部通过。

## 4. 覆盖率与质量门禁

统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

- 全后端：113 tests passed。
- statements/branch 综合覆盖率：89.18%，高于且未降低 80% 门禁。
- A-07 模块：contracts 82%、fake 93%、registry 95%、security 90%、worker adapter 100%。
- Ruff：PASS。
- mypy strict：PASS，52 个 Python 源/测试文件无问题。
- 前端：1 test passed，覆盖率 100%；ESLint、TypeScript、Vite build 全部 PASS。
- 统一脚本：`All quality checks passed.`

首次统一脚本在受限沙箱内启动 Vite 子进程时遇到 Windows `spawn EPERM`；同一命令在获准的沙箱外环境重跑后完整通过。这是执行环境限制，不是产品测试失败。

## 5. 已知边界

- A-07 只冻结抽象契约与安全配置入口，不发起真实网络连接；DNS 重解析、重定向、响应大小和实际 CDP 校验由 A-10 AdsPower 客户端结合网络 I/O 实现。
- profile 的持久化跨进程互斥继续由 A-06 `resource_leases` 保证；Fake Provider 的进程内互斥用于共享契约及 worker 组合测试，不能代替数据库租约。
- A-08 将使用 `BrowserSessionJob` 和 Fake Provider 接入本地模拟 X 页、导航器、响应收集器与解析器。
