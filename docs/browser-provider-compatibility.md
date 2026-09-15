# Browser Provider 兼容性矩阵

> 更新日期：2026-09-15
> 适配器发布：阶段 A / A-10

## 支持矩阵

| Provider | Adapter | Provider / Local API | Browser | Playwright | 状态 |
| --- | --- | --- | --- | --- | --- |
| Direct Chrome | `DIRECT_CHROME` 1.0 | 已安装 Google Chrome | Playwright `channel=chrome` 可启动的版本 | 1.62.0 | 自动化 E2E 已通过 |
| AdsPower | `ADSPOWER` 1.0 | AdsPower 3.4.1+；Local API V2 | AdsPower 当前 profile 所用 Chrome 内核，须支持 CDP | 1.62.0 | 保留 A-10 历史自动化证据；自 A-14 起不再测试 |

AdsPower 3.4.1 是 V2 browser start 官方文档列出的最低应用版本。A-10 使用以下受控端点：

- `GET /status`
- `POST /api/v2/browser-profile/list`
- `GET /api/v2/browser-profile/active`
- `POST /api/v2/browser-profile/start`
- `POST /api/v2/browser-profile/stop`

适配器不提供 create、update、delete profile，也不传入或修改指纹、代理、Cookie、平台密码或 CDP mask。参考 AdsPower 官方 [Local API 概览](https://localapi-doc-en.adspower.com/docs/Rdw7Iu)、[V2 profile 查询](https://localapi-doc-en.adspower.com/docs/Query-Profile-V2)、[V2 browser start](https://localapi-doc-en.adspower.com/docs/Open-Browser-V2)、[V2 browser status](https://localapi-doc-en.adspower.com/docs/Check-Browser-Status-V2) 与 [V2 browser stop](https://localapi-doc-en.adspower.com/docs/Close-Browser-V2)。

## 版本记录口径

- `adapter version`：代码常量 `ADSPOWER` 1.0，由 Provider registry/能力报告提供。
- `provider version`：管理员受控配置中声明的 AdsPower 应用版本；A-10 校验其不低于 3.4.1。AdsPower `/status` 不返回应用版本，因此该字段是声明值，不伪装成远程探测结果。
- `browser version`：成功 CDP 连接后读取 Playwright `Browser.version`。
- CDP 或 Provider 版本不在矩阵内时 fail closed，不自动切换 profile、内核或 Provider。

## AdsPower 验证范围决定

2026-09-15 的历史探测确认 Local API 进程在线，但 profile list 因订阅权限被拒绝。用户随后决定 AdsPower 部分不再测试。因此以下原计划步骤已从当前及后续发布门禁移除，不再作为延期项：

1. 列出并选择已有测试 profile，不创建或修改 profile。
2. 对原本未运行的 profile：start → CDP → 本地模拟页采集 → detach → stop。
3. 对原本已运行的 profile：active → CDP → 本地模拟页采集 → detach，确认未调用 stop。
4. 记录 AdsPower 应用版本、浏览器内核版本、Playwright 版本和 adapter 版本。
5. 复验恶意 URL/重定向、无效 token、断线和重复释放均 fail closed 且日志无 token/CDP URL。

上述步骤均未执行，也未伪造为通过。A-10 代码和历史测试记录保留，仅供将来用户重新明确纳入范围时参考。
