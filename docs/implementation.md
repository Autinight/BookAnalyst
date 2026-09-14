# v0.7 实现结构

更新：2026-09-08。当前代码维护七步视觉转换路径，旧版数据兼容限制在历史列表和产物下载。

| 文件 | 职责 |
| --- | --- |
| `models.py` | 创建任务参数、状态和阶段定义 |
| `engine.py` | 七步调度、有限并行、接缝、断点恢复和编译修复 |
| `documents.py` | 小型模型输出契约、页归属、公共 TeX 样式和拼接 |
| `reference_tools.py` | label/ref 的全书只读搜索、按页正文读取和 PDF 文字共享缓存 |
| `finisher.py` | 编译连接分流、持续 Codex 会话与原生事件恢复 |
| `pi_compiler.py` / `pi_runtime/agent.mjs` | 自定义 API 的 pi.dev SDK 编译会话、原生工具、自动压缩和中断恢复 |
| `llm.py` | ChatGPT 官方订阅、Grok OAuth、自定义 API、真实用量、请求回执恢复 |
| `grok_oauth.py` | Grok SuperGrok / X Premium+ 的设备码登录、令牌刷新与 cli-chat-proxy 调用 |
| `store.py` | SQLite 小记录、幂等操作、任务和请求账本、原子文件保存 |
| `pdf.py` / `tex.py` | PDF 图像缓存 / PATH 中的 XeLaTeX 编译 |
| `app.py` | 本地 API、来源图片、按页 TeX、导出 |
| `static/api.js` / `views.js` / `app.js` | 请求、视图模板、交互和轻量轮询 |

前端每次只获取当前任务的状态摘要；状态变化时更新进度节点。阅读区仅在用户翻页、当前批次完成或最终编译完成时更新。图片按源文件哈希、页码和 DPI 缓存；书库缩略图延迟加载。页面卸载或切换时中止旧内容请求，页面隐藏时暂停轮询。

`.bookanalyst/bookanalyst.sqlite3` 存储书目、连接、运行摘要、任务和调用账本。正文不塞进运行记录：`.bookanalyst/runs/<id>/v7/` 下保存设置、批次、接缝、标题、最终页索引及 `tex/` 工程；原始请求在同级 `requests/` 下。

旧设置中 MinerU 字段迁存为 `settings/pre-v6`，新设置接口只提供模型连接。`.env` 和原始测试书没有改动。旧 v0.5 执行器、MinerU 适配器、block 规划与 KaTeX 依赖已从当前源码删除，可从提交 `5771789` 找回。

40 项程序检查已通过；v0.7 完整 Wang 45 页真实运行经修复和恢复完成；具体严格读隔离、真实验证范围和语义保真度限制见 [工作流](workflow-v0.7.md)和 [验证记录](validation-v0.7.md)，不以程序测试代替真实证据。

`numbering.py` 将全书计数规则编译为普通 TeX 声明，直接收集正文中的原生标签与引用，按出现位置应用独立标签与引用阶段的键修改；该阶段读取标题阶段的最终结果以计算章节作用域。引用按异常组并发处理，同一原始类型与编号的重名标签归同组；先完成重名消解，再处理缺失键。`reference-groups/` 保存成功组，程序按出现位置统一合并补丁。标题与引用分别保存和恢复，编号正确性由收尾 agent 结合原页和编译结果判断。转换员工不生成额外登记表。新数据目录为 `runs/<id>/v7/`；v0.6 已完成工程仍可下载。

自定义 API 密钥由设置页密码框输入，后台通过 SQLite 事务与连接设置一起保存到独立 `credential` 记录；公开设置只返回 `api_key_configured`。Grok OAuth 令牌同样写入 `credential`，公开设置只返回 `oauth_configured`。密钥和令牌仅在调用时用于鉴权头，自定义通道仍兼容已有环境变量引用。
