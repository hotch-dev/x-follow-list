# A-12 React 后台纵向闭环 TDD 证据

> 日期：2026-09-15  
> 任务：A-12 React 后台纵向闭环  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)  
> 工作流：`ecc:tdd-workflow`，严格保留 RED → GREEN → 重构 → 覆盖率 → 证据检查点

## 1. 用户旅程与自动化测试

| 用户旅程 | 自动化证据 |
| --- | --- |
| 总览显示账号健康、最近任务、上一成功数据时间和互关后取关首屏指标 | `App.test.tsx` dashboard journey |
| 授权过期后重新登录，CSRF 仅保存在 session storage，并恢复原数据 | `App.test.tsx` session recovery journeys |
| 从受控 Provider config 中只选择已有 AdsPower profile，轮询身份并确认绑定 | `AccountsPage.test.tsx` binding journeys |
| `REAUTH_REQUIRED` 账号明确确认后复用既有 profile 重新验证 | `AccountsPage.test.tsx` revalidation journey |
| 解绑明确确认并携带当前资源版本和 CSRF；AdsPower 不删除外部 profile | `AccountsPage.test.tsx` unbind journey、后端 unbind 回归 |
| 手工扫描携带 UUID 幂等键，有界指数退避，终态停止，失败保留上一成功数据 | `ScansPage.test.tsx` polling journeys |
| 关系按账号、状态和搜索词筛选；重点事件按版本确认并处理 409 | `RelationshipsPage.test.tsx` relationship/action-item journeys |
| 五个管理页键盘可达，并区分加载、空态、授权和暂时失败 | `App.test.tsx` navigation/error/empty journeys |

映射 AC-08、AC-13、AC-19（P0 部分）。MSW 只访问本地模拟 API；本任务未访问 X 生产环境，也未提供任何 X 写操作。

## 2. RED → GREEN → 重构检查点

| 纵向切片 | RED | GREEN | 重构 |
| --- | --- | --- | --- |
| 总览与失败保真 | `51957a1` | `875e233` | `3a4c878` |
| 已有 AdsPower profile 选择 | `2320b59` | `89c2821` | `761309b` |
| 手工扫描与终态轮询 | `5f90549` | `241b10a` | `e3fa5b9` |
| 关系筛选与待处理确认 | `1a89287` | `877b867` | `ffcf6b7` |
| 五页导航 | `3b0403f` | `9f21afb` | `110d3d1` |
| 检测身份与绑定确认 | `29c8c38` | `d1d5df0` | `8aacd87` |
| session 恢复 | `0af3f36` | `8f2739a` | `1e69788` |
| 重新验证与解绑 | `af94536` | `f2a84ab` | `68c18a8` |
| 有界轮询退避 | `8c10c11` | `572868c` | `e800d21` |

每个 RED 都先运行并观察预期失败；对应 GREEN 只加入使旅程通过的最小实现；重构后重复运行目标测试。检查点均位于 `main` 连续提交链，未 squash 或改写。额外错误态与覆盖率验证提交为 `c2fb9d0`。

## 3. 关键实现结果

- React 19、TypeScript、Vite 与 TanStack Query 构成五页 SPA；API client 统一 cookie session、错误类型、CSRF mutation header 和 204 响应。
- 账号页只调用已有 Provider config/profile 查询，不暴露 AdsPower profile 创建、修改或删除能力。
- 重新验证只发送 `x_account_id`，由后端解析并锁定原 Provider config/profile，前端不能借此替换环境。
- 账号列表返回乐观锁 `version`；解绑发送 `{version, delete_history:false}`，409 时保留可操作错误而不假装成功。
- 扫描轮询从基础间隔指数退避并封顶 15 秒；SUCCESS、FAILED、CANCELLED 后停止。
- 失败扫描始终单独呈现错误与上一成功扫描时间，不将失败误画成空关系集。

## 4. 覆盖率与全量门禁

统一命令：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath C:\Users\15485\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

结果：

- 后端：204 passed；综合 statements/branch coverage 88.43%。
- Ruff：PASS。
- mypy strict：PASS，86 个 Python 源/测试文件无问题。
- 前端：4 files、15 tests passed。
- 前端 statements 98.04%、branches 83.67%、functions 96.59%、lines 98.96%。
- ESLint、TypeScript project build、Vite production build：PASS。
- 统一脚本：`All quality checks passed.`

所有覆盖率维度均保持在 80% 门禁以上，无 skipped/disabled 测试。

## 5. 真实 AdsPower 发布验证

真实 AdsPower `/status` 已在线，但有效 `ADSPOWER_API_TOKEN` 尚未通过环境 secret reference 提供，因此“环境 3”的真实本地模拟页冒烟没有被伪造为通过。该发布前验证状态和后续步骤记录在 [A-10 证据](./a-10-adspower-provider.tdd.md) 与 [Browser Provider 兼容性矩阵](../browser-provider-compatibility.md)。
