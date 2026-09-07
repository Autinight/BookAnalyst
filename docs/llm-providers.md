# LLM 供应商配置 v0.7

更新：2026-09-07。供应商配置独立于 PDF → TeX 工作流，仅管理连接、鉴权、默认模型与调用参数。当前已实现官方订阅和自定义 API 适配器。

## 优先渠道：ChatGPT 官方订阅

使用官方 Python 包 **openai-codex 0.147.0** 作为协议适配层，并优先连接本机已安装的官方运行时。登录、令牌保存和刷新由官方运行时管理，应用不接收 ChatGPT 密码、Cookie 或手工复制的订阅令牌。[官方 SDK 文档](https://learn.chatgpt.com/docs/codex-sdk)

在设置页点击“检查登录状态”。已有有效 ChatGPT 登录时，显示账户可用模型；尚未登录时点击“登录 ChatGPT”，由官方运行时返回浏览器授权入口。当前本机已读取到有效订阅登录，无需重新配置账户。[官方认证说明](https://learn.chatgpt.com/docs/auth)

默认模型可留空，由上游返回的默认模型解析；新建运行的模型选择使用实时返回的模型列表，没有固定模型名称白名单。程序在运行开始时固定实际模型；图像请求要求该模型元数据包含图像能力。

运行时选择顺序为：显式环境变量 `BOOKANALYST_CODEX_BIN` → PATH 中的 `codex` → Windows 官方桌面安装目录中的运行时 → SDK 捆绑运行时。显式配置无法启动时直接报错，不悄悄降级。连接状态接口返回实际运行时版本。

本次确认旧 SDK 捆绑运行时只返回 6 个模型；改为本机 PATH 的官方 `codex-cli 0.153.3` 后，上游返回 7 个模型，默认 `gpt-6-astra`，且声明支持图像。这是本机 2026-09-05 的实际读取结果，后续以账户实时返回为准。

每次生成使用新的只读持久会话，保存上游 thread / turn 标识以便读取未及时返回的结果，并传递任务文本、必要图像与输出 JSON Schema。应用校验结构化结果后再交回业务层；不会自动切换到另一账户或 API 渠道。

应用不配置上下文容量或输出 token 上限；请求规模由每批页数控制。官方订阅沿用运行时行为，自定义 API 不发送 `max_tokens` 或 `max_output_tokens`。上游模型和部署自身的限制仍然适用；旧运行配置中的容量字段不再参与请求判断。

## 自定义 API

设置页启用“自定义 API”，填写以下字段：

| 字段 | 含义 |
| --- | --- |
| base_url | API 根地址；例如另行启动的 vLLM 可填写 `http://127.0.0.1:8001/v1` |
| protocol | 明确选择 `chat_completions` 或 `responses` |
| model_id | 服务实际部署的模型 ID |
| auth_mode | `bearer`，或本地服务明确使用 `none` |
| API 密钥 | 直接粘贴服务提供的密钥；保存后不回显，留空保留现有值 |
| image_support | `unknown`、`supported`、`unsupported`；由用户确认，当前不是自动探测结果 |
| max_in_flight | 该连接共享的最大并发，默认 2 |
| timeout_seconds | 单次应用调用截止时间，默认 600 秒 |

适配器按协议分别追加 `/chat/completions` 或 `/responses`，不通过失败后尝试另一协议来猜测服务。两种协议均传递实际图片内容；输出必须通过客户端 JSON Schema 校验。[协议参考](https://developers.openai.com/api/docs/guides/migrate-to-responses)

设置页直接使用密码输入框接收密钥。后台在本地 SQLite 的独立 `credential` 记录中保存；普通设置、GET/PUT 响应、请求正文与导出不包含密钥。保存后输入框清空，只显示是否已配置；留空保留，填写新值替换，勾选清除后保存则清除。

本地凭据当前不额外加密，应用数据目录受本机文件权限管理并被 Git 忽略。旧的系统环境变量和 `.env` 配置继续兼容，但用户不需要填写变量名；直接保存的密钥优先，显式清除后不会回退使用旧环境变量。MinerU 与 LLM 的凭据互不复用。

## 实际配置

连接配置由独立设置接口保存到本地 SQLite。下面对应当前实现字段，不含业务阶段或审查角色：

```json
{
  "default_connection": "openai_subscription",
  "connections": {
    "openai_subscription": {
      "kind": "codex_chatgpt",
      "enabled": true,
      "model_id": "",
      "max_in_flight": 2,
      "timeout_seconds": 600
    },
    "custom_api": {
      "kind": "openai_compatible",
      "enabled": false,
      "base_url": "",
      "protocol": "chat_completions",
      "model_id": "",
      "auth_mode": "bearer",
      "api_key_configured": false,
      "image_support": "unknown",
      "max_in_flight": 2,
      "timeout_seconds": 600
    }
  }
}
```

这是 v0.7 设置接口的公开返回对象。`api_key_configured` 只表示凭据存在，不表示已向上游验证。保存时可向自定义连接提交只写字段 `api_key` 或 `clear_api_key`；两者不写入普通设置。旧 MinerU 设置保留为历史配置，不进入当前接口。当前 UI 编辑上述两个连接；后端可保存其他具名连接。连接中的默认模型与运行中的角色绑定分开保存。

## 调用约束与验证状态

实际请求同时受运行并发与连接并发约束。连接修改会影响后续派发，请在运行前完成配置。失败不会自动切换渠道。

应用调用按一次实际派发的 `generate` 计数：官方渠道对应一次 Codex turn，自定义渠道对应一次 HTTP 模型请求。官方回执已记录实际输入、输出、缓存、推理 token、耗时及工具条目。内部推理轮数没有完整计数，不能将应用调用数当作供应商实际推理次数。恢复复用已有响应不新增调用。

请求超时或传输中断保留 RESULT_UNKNOWN，不假设远端没有执行。当前提供 `status`、`login`、`resolve`、`generate` 和 `reconcile`。官方订阅在提交时保存持久任务与 turn 标识，恢复先读取原 turn；缺少标识时保留未知。自定义 HTTP 接口尚无通用请求恢复协议。完整取消、细粒度能力探测与额度分类仍待补齐。自定义连接只检查配置时显示 CONFIGURED_UNTESTED，不能显示为实际调用成功。

v0.7 的真实订阅验证见 [当前记录](validation-v0.7.md)。自定义通道现在传递所选思考强度：Chat Completions 使用 `reasoning_effort`，Responses 使用 `reasoning.effort`。服务须支持该参数；不支持时明确报错，不静默省略。其图像能力仍需要实际服务确认。连接状态只检查配置时显示 CONFIGURED_UNTESTED，不能据此宣称真实调用成功。

官方订阅的能力范围和当前 Windows 读隔离限制见 [工作流](workflow-v0.7.md)。恢复依据[官方 App Server 接口](https://learn.chatgpt.com/docs/app-server)及本机 SDK 验证。供应商配置不增加业务阶段。

运行中转换与衔接默认 `medium`，全书设置、最终标题与标签处理默认 `xhigh`。两者独立选择，实际强度写入每次请求账本；结构任务覆盖只作用于该请求，不修改其他并发任务的设置。
