# LLM 供应商配置 v0.4

更新：2026-09-05。供应商配置独立于 PDF → TeX 工作流，仅管理连接、鉴权、默认模型与调用参数。当前已实现官方订阅和自定义 API 适配器。

## 优先渠道：ChatGPT 官方订阅

使用官方 Python 包 **openai-codex 0.147.0** 作为协议适配层，并优先连接本机已安装的官方运行时。登录、令牌保存和刷新由官方运行时管理，应用不接收 ChatGPT 密码、Cookie 或手工复制的订阅令牌。[官方 SDK 文档](https://learn.chatgpt.com/docs/codex-sdk)

在设置页点击“检查登录状态”。已有有效 ChatGPT 登录时，显示账户可用模型；尚未登录时点击“登录 ChatGPT”，由官方运行时返回浏览器授权入口。当前本机已读取到有效订阅登录，无需重新配置账户。[官方认证说明](https://learn.chatgpt.com/docs/auth)

默认模型可留空，由上游返回的默认模型解析；设置页和运行角色选择均使用实时返回的模型列表，没有固定模型名称白名单。程序在运行开始时固定实际模型；图像请求要求该模型元数据包含图像能力。

运行时选择顺序为：显式环境变量 `BOOKANALYST_CODEX_BIN` → PATH 中的 `codex` → Windows 官方桌面安装目录中的运行时 → SDK 捆绑运行时。显式配置无法启动时直接报错，不悄悄降级。设置页显示实际运行时版本，便于区分运行时过旧和账户不可用。

本次确认旧 SDK 捆绑运行时只返回 6 个模型；改为本机 PATH 的官方 `codex-cli 0.153.3` 后，上游返回 7 个模型，默认 `gpt-6-astra`，且声明支持图像。这是本机 2026-09-05 的实际读取结果，后续以账户实时返回为准。

每次生成使用新的只读持久会话，保存上游 thread / turn 标识以便读取未及时返回的结果，并传递任务文本、必要图像与输出 JSON Schema。应用校验结构化结果后再交回业务层；不会自动切换到另一账户或 API 渠道。

## 自定义 API

设置页启用“自定义 API”，填写以下字段：

| 字段 | 含义 |
| --- | --- |
| base_url | API 根地址；例如另行启动的 vLLM 可填写 `http://127.0.0.1:8001/v1` |
| protocol | 明确选择 `chat_completions` 或 `responses` |
| model_id | 服务实际部署的模型 ID |
| auth_mode | `bearer`，或本地服务明确使用 `none` |
| api_key_env | 保存密钥的环境变量名，默认 `LLM_CUSTOM_API_KEY` |
| image_support | `unknown`、`supported`、`unsupported`；由用户确认，当前不是自动探测结果 |
| max_in_flight | 该连接共享的最大并发，默认 2 |
| timeout_seconds | 单次应用调用截止时间，默认 600 秒 |

适配器按协议分别追加 `/chat/completions` 或 `/responses`，不通过失败后尝试另一协议来猜测服务。两种协议均传递实际图片内容；输出必须通过客户端 JSON Schema 校验。[协议参考](https://developers.openai.com/api/docs/guides/migrate-to-responses)

密钥从系统环境变量或项目 `.env` 读取；设置文件只保存变量名。MinerU 的 `MINERU_API_TOKEN` 与 LLM 的 `LLM_CUSTOM_API_KEY` 分属不同服务，互不复用。

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
      "api_key_env": "LLM_CUSTOM_API_KEY",
      "image_support": "unknown",
      "max_in_flight": 2,
      "timeout_seconds": 600
    }
  }
}
```

这是完整设置对象中的 LLM 部分，完整对象还包含独立的 MinerU 服务配置。当前 UI 编辑上述两个连接；后端可保存其他具名连接。连接中的默认模型与运行中的角色绑定分开保存。

## 调用约束与验证状态

实际请求同时受运行并发与连接并发约束。设置页不在任务运行时保存新连接配置。失败不会自动切换渠道。

应用预算按一次 `generate` 计数：官方渠道对应一次 Codex turn，自定义渠道对应一次 HTTP 模型请求。官方运行时内部可能包含多次推理或重试，当前无法完整观测，记录为 unknown；不能将应用调用数当作供应商实际推理次数。

请求超时或传输中断保留 RESULT_UNKNOWN，不假设远端没有执行。当前提供 `status`、`login`、`resolve`、`generate` 和 `reconcile`。官方订阅在提交时保存持久任务与 turn 标识，恢复先读取原 turn；缺少标识时保留未知。自定义 HTTP 接口尚无通用请求恢复协议。完整取消、细粒度能力探测与额度分类仍待补齐。自定义连接只检查配置时显示 CONFIGURED_UNTESTED，不能显示为实际调用成功。

截至 2026-09-06：官方订阅账户及模型列表读取成功，已使用上游 gpt-6-astra 跑通新版 Wang 45 页完整转换与编译，累计 46 次应用调用。自定义两种协议的文字／图片负载与错误处理通过模拟服务测试；真实全书原图审查尚未完成。

新版恢复接口依据[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)及本机安装的 SDK 验证；工作流参与步骤不包含供应商配置。新版真实验证状态见 [v0.5 记录](validation-v0.5.md)。
