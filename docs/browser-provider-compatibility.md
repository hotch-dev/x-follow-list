# Browser Provider 兼容性矩阵

> 更新日期：2026-09-15
> 适配器发布：阶段 A / A-10

## 支持矩阵

| Provider | Adapter | Provider / Local API | Browser | Playwright | 状态 |
| --- | --- | --- | --- | --- | --- |
| Direct Chrome | `DIRECT_CHROME` 1.0 | 已安装 Google Chrome | Playwright `channel=chrome` 可启动的版本 | 1.62.0 | 自动化 E2E 已通过 |
| AdsPower | `ADSPOWER` 1.0 | AdsPower 3.4.1+；Local API V2 | AdsPower 当前 profile 所用 Chrome 内核，须支持 CDP | 1.62.0 | 伪 Local API / 可控 CDP 自动化已通过；真实 AdsPower 冒烟待外部环境 |

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

## 发布前真实 AdsPower 冒烟

2026-09-15 在开发机探测 `http://127.0.0.1:50325/status`，返回 HTTP 200、`code=0`，真实 AdsPower Local API 已在线。当前进程和用户级环境没有 `ADSPOWER_API_TOKEN`，profile list 的无效凭据探测返回 `API Key mismatch`；因此用户指定的“环境 3”尚未被列出或启动。以下验证项必须在通过 `env://ADSPOWER_API_TOKEN` 提供有效 Local API 凭据后执行，才能将真实 AdsPower 状态改为通过：

1. 列出并选择已有测试 profile，不创建或修改 profile。
2. 对原本未运行的 profile：start → CDP → 本地模拟页采集 → detach → stop。
3. 对原本已运行的 profile：active → CDP → 本地模拟页采集 → detach，确认未调用 stop。
4. 记录 AdsPower 应用版本、浏览器内核版本、Playwright 版本和 adapter 版本。
5. 复验恶意 URL/重定向、无效 token、断线和重复释放均 fail closed 且日志无 token/CDP URL。

真实冒烟只访问本地模拟页，不访问 X 生产环境。
