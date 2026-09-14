# A-04 关系域模型与状态机 TDD 证据

> 日期：2026-09-14  
> 任务：A-04 关系域模型与状态机  
> 源计划：[phase-a-implementation-plan.md](../../phase-a-implementation-plan.md)

## 1. 用户旅程

- 作为结果查看者，我希望每个 X 用户在成功完整扫描后被准确归类为互关、未回关、只关注我或双方均不存在。
- 作为 OWNER，我希望首次扫描只建立事实基线，不产生虚假的关系变化事件。
- 作为 OWNER，我希望“上次互关、本次仅我关注”生成唯一的重点待处理事件。
- 作为通知逻辑，我希望连续未回关次数只在有效的连续状态中递增，并在回关或我停止关注后清零。
- 作为持久化层，我希望事件 dedupe key 对同一扫描、对象和类型稳定，对不同身份维度严格区分。

## 2. RED 证据

先新增覆盖完整迁移矩阵、首次基线、streak、事件与幂等键的表驱动测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\domain\test_relationship_state_machine.py -q
```

结果：测试收集阶段因 `ModuleNotFoundError: No module named 'x_follow_list.domain'` 失败，属于目标领域模块不存在产生的有效 RED。

## 3. GREEN 与重构证据

| 保证 | 自动化验证 | 结果 |
| --- | --- | --- |
| `MUTUAL`、`NOT_FOLLOWING_BACK`、`FOLLOWS_ME_ONLY`、`ABSENT` 四种事实状态 | membership 参数化测试 | PASS |
| 4×4 共 16 种前后状态迁移 | `test_all_relationship_state_transitions` | PASS |
| follower/following 布尔变化生成四类普通事件 | 全迁移矩阵断言 | PASS |
| 仅 `MUTUAL → NOT_FOLLOWING_BACK` 生成重点待处理事件 | 全矩阵 action category 断言 | PASS |
| 首次扫描四种状态均不生成事件 | 首次基线参数化测试 | PASS |
| 首次未回关 streak 为 1，连续未回关递增，其他状态清零 | streak 测试 | PASS |
| 相同事件身份生成稳定 key，不同 scan/subject/type 生成不同 key | dedupe key 测试 | PASS |
| 负 streak、空身份、前次 membership/state 不一致 fail closed | 非法输入测试 | PASS |

重构后，领域模块只依赖 Python 标准库；不引用 FastAPI、SQLAlchemy、Playwright 或配置基础设施。事件顺序固定为 follower 变化、following 变化、业务重点事件，便于持久化与测试稳定复现。`FOLLOWING_BLOCKLISTED_ACCOUNT` 只预留事件枚举，不实现阶段 B 规则逻辑。

失败或不完整扫描不属于状态机输入；上层 A-05 提交用例只允许在完整性通过后调用 `evaluate_transition`，从结构上避免失败扫描递增 streak 或生成事件。

## 4. 覆盖率与质量门禁

- A-04 领域测试：29 tests passed。
- `x_follow_list.domain` statements/branches：100%。
- 全后端：59 tests passed。
- 全后端 statements/branch 综合覆盖率：89.89%，高于 80% 门禁。
- Ruff：PASS。
- mypy strict：PASS，36 个 Python 源/测试文件无问题。

最终统一门禁：

```powershell
pwsh -File .\scripts\check.ps1 -NodePath '<Node 24 executable>'
```

## 5. 已知边界

- A-04 只产生不可变的领域结果，不写数据库；原子 snapshot/state/event 持久化由 A-05 实现。
- 用户名、显示名和头像不进入状态机，因此展示字段变化不会产生关系事件。
- streak 阈值通知、白名单抑制和业务黑名单冲突属于阶段 B，不在本任务提前实现。
