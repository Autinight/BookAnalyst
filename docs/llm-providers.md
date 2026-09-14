# LLM 供应商配置 v0.7

更新：2026-09-14。供应商配置独立于 PDF → TeX 工作流，仅管理连接、鉴权、默认模型与调用参数。当前已实现 ChatGPT 官方订阅、Grok 官方订阅和自定义 API 适配器。

## 优先渠道：ChatGPT 官方订阅

使用官方 Python 包 **openai-codex 0.147.0** 作为协议适配层，并优先连接本机已安装的官方运行时。登录、令牌保存和刷新由官方运行时管理，应用不接收 ChatGPT 密码、Cookie 或手工复制的订阅令牌。[官方 SDK 文档](https://learn.chatgpt.com/docs/codex-sdk)

在设置页点击“检查登录状态”。已有有效 ChatGPT 登录时，显示账户可用模型；尚未登录时点击“登录 ChatGPT”，由官方运行时返回浏览器授权入口。当前本机已读取到有效订阅登录，无需重新配置账户。[官方认证说明](https://learn.chatgpt.com/docs/auth)

默认模型可留空，由上游返回的默认模型解析；新建运行的模型选择使用实时返回的模型列表，没有固定模型名称白名单。运行开始时解析并写入实际模型。运行页可以更换连接、模型、思考强度和并行数：已完成批次保留，已经发出的请求仍用开始时的绑定，之后的新请求使用新配置。图像请求要求该模型元数据包含图像能力。

运行时选择顺序为：显式环境变量 `BOOKANALYST_CODEX_BIN` → PATH 中的 `codex` → Windows 官方桌面安装目录中的运行时 → SDK 捆绑运行时。显式配置无法启动时直接报错，不悄悄降级。连接状态接口返回实际运行时版本。

本次确认旧 SDK 捆绑运行时只返回 6 个模型；改为本机 PATH 的官方 `codex-cli 0.153.3` 后，上游返回 7 个模型，默认 `gpt-6-astra`，且声明支持图像。这是本机 2026-09-05 的实际读取结果，后续以账户实时返回为准。

转换、衔接和结构化处理每次生成使用新的只读持久会话，保存上游 thread / turn 标识，传递任务文本、必要图像与输出 JSON Schema。最终编译修复使用独立的完整 Codex 运行时，持续使用同一个 thread，开启原生文件与命令工具，并使用运行时自动压缩；不再经过 JSON 输出适配器或十次请求限制。自定义 API 的最终编译由 pi.dev SDK 接入相同连接配置，使用持续 Pi 会话与原生工具；官方订阅继续使用 Codex SDK，不自动切换渠道。

前面结构化 worker 不配置上下文容量或输出 token 上限；请求规模由每批页数控制。官方订阅沿用运行时行为，结构化自定义 API worker 不发送 `max_tokens` 或 `max_output_tokens`。Pi 编译 Agent 使用其模型容量管理输出与压缩，未知模型默认 128000 上下文、16384 单次输出，并在 `pi-compiler.json` 记录。上游模型和部署自身的限制仍然适用；旧运行配置中的容量字段不再参与请求判断。

## Grok 官方订阅

使用 SuperGrok 或 X Premium+ 账户的设备码 OAuth，不接收 xAI 密码，也不把访问令牌回显到设置接口。登录复用 Grok CLI 的公开 OAuth client，走 RFC 8628 设备码，而不是本机回环端口。授权页可能显示 “Grok Build”。

在设置页点击“登录”，打开 `accounts.x.ai` 的验证页。若页面要求输入代码，填写本卡片显示的授权码。后台轮询换取令牌。登录完成后点击“检查连接”。令牌保存在本地 SQLite 的独立 `credential` 记录中，过期时自动刷新；公开设置只返回 `oauth_configured`。

结构化请求和收尾编译都走 `https://cli-chat-proxy.grok.com/v1` 的 Responses 协议，并带 grok-cli 鉴权头；收尾仍用 pi.dev SDK，不使用 Codex。模型列表优先读上游，读不到时使用 grok-4.6 等回退目录。图像生成类 `grok-imagine-*` 不会进入对话模型列表。部分订阅档在登录成功后仍可能对 API 返回 403，此时改用自定义 API 并填写 xAI 密钥。

旧安装升级后会自动补上该连接，不会覆盖已有 ChatGPT 或自定义供应商。

## 自定义 API

在“模型设置”中点击“添加 API 供应商”可连续添加任意多个 OpenAI 兼容供应商，每个供应商独立保存地址、密钥、协议、模型与超时设置。新供应商默认未启用；填写配置并启用后，即可在各阶段的“模型连接”中选择，最后点击“保存设置”。

直接修改“供应商名称”即可重命名，名称支持中文（1–100 个字符）。名称与内部连接 ID 分开保存，因此重命名不会改变密钥、阶段绑定或已有任务引用。旧配置没有名称时仍可正常加载和保存。“检查连接”使用已保存的配置，新供应商需保存后才能检查。

各供应商可配置以下字段：

| 字段 | 含义 |
| --- | --- |
| base_url | API 根地址；例如另行启动的 vLLM 可填写 `http://127.0.0.1:8001/v1` |
| protocol | 明确选择 `chat_completions` 或 `responses` |
| model_id | 服务实际部署的模型 ID |
| auth_mode | `bearer`，或本地服务明确使用 `none` |
| API 密钥 | 直接粘贴服务提供的密钥；保存后不回显，留空保留现有值 |
| image_support | `unknown`、`supported`、`unsupported`；由用户确认，当前不是自动探测结果 |
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
      "timeout_seconds": 600
    },
    "grok_subscription": {
      "kind": "grok_oauth",
      "enabled": true,
      "model_id": "",
      "timeout_seconds": 600,
      "oauth_configured": false
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
      "timeout_seconds": 600
    }
  }
}
```

这是 v0.7 设置接口的公开返回对象。`api_key_configured` 只表示自定义凭据存在，`oauth_configured` 只表示已保存 Grok 令牌，都不表示已向上游验证。保存时可向自定义连接提交只写字段 `api_key` 或 `clear_api_key`；两者不写入普通设置。旧 MinerU 设置保留为历史配置，不进入当前接口。当前 UI 编辑内置连接与用户添加的 API 连接。连接中的默认模型与运行中的角色绑定分开保存。

## 调用约束与验证状态

并行批次数由每个任务的 `llm_concurrency` 控制，不再设置供应商并发上限；多个任务使用同一供应商时，各自独立调度。旧配置中的 `max_in_flight` 字段会被忽略，并在下次保存设置时移除。设置页改连接会影响之后新建的运行；进行中的任务可在运行页调整“并行任务数”，点击“更新配置”一并读取设置页保存的模型配置；此处并发只应用到当前任务，不修改全局默认值。调高会补充派发，调低会等待已有请求结束后按新上限继续，已派发请求保持原模型配置。失败不会自动切换渠道。

应用调用按一次实际派发的 `generate` 计数：官方渠道对应一次 Codex turn，自定义渠道对应一次 HTTP 模型请求。官方回执已记录实际输入、输出、缓存、推理 token、耗时及工具条目。内部推理轮数没有完整计数，不能将应用调用数当作供应商实际推理次数。恢复复用已有响应不新增调用。Pi 编译记录每次 `session.prompt`，内部可包含多次 API 请求和工具调用；token 按实际 assistant 消息和上下文压缩的用量累计，逐次事件保留在请求目录。

请求超时、传输中断、限流和 5xx 在同一次 `generate` 内自动重试最多 5 次，间隔 2s、4s、8s、16s；用尽后暂停，不立刻进入 NEEDS_REVIEW。鉴权失败、SCHEMA_ERROR 和内容修复不走这套瞬时重试。超时或中断仍记 RESULT_UNKNOWN，不假设远端没有执行。自动重试用尽后可直接点“开始／恢复”；只有尚未确认上游是否完成的未知请求才需要“重试未返回请求”。当前提供 `status`、`login`、`test`、`resolve`、`generate` 和 `reconcile`。官方订阅在提交时保存持久任务与 turn 标识，恢复先读取原 turn；缺少标识时保留未知。自定义 HTTP 接口尚无通用请求恢复协议。完整取消、细粒度能力探测与额度分类仍待补齐。自定义连接的 `status` 只检查配置，显示 CONFIGURED_UNTESTED，不能据此宣称真实调用成功。设置页“检查连接”会调用 `test`：先请求 `/models`，若该接口不存在再对所选协议发一次最小 ping，用 401/403 判断鉴权，不进入文档转换。

v0.7 的真实订阅验证见 [当前记录](validation-v0.7.md)。自定义通道现在传递所选思考强度：Chat Completions 使用 `reasoning_effort`，Responses 使用 `reasoning.effort`。服务须支持该参数；不支持时明确报错，不静默省略。其图像能力仍需要实际服务确认。连接状态只检查配置时显示 CONFIGURED_UNTESTED；要确认密钥和地址是否可用，使用设置页的连接测试。

官方订阅的能力范围和当前 Windows 读隔离限制见 [工作流](workflow-v0.7.md)。恢复依据[官方 App Server 接口](https://learn.chatgpt.com/docs/app-server)及本机 SDK 验证。供应商配置不增加业务阶段。

运行中转换与衔接默认 `medium`，全书设置、最终标题与标签处理默认 `xhigh`。两者独立选择，实际强度写入每次请求账本；结构任务覆盖只作用于该请求，不修改其他并发任务的设置。
