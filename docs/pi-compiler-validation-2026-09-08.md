# Pi 编译内核接入验证（2026-09-08）

采用 https://pi.dev/ 的官方 SDK `@earendil-works/pi-coding-agent` 0.85.1。
最终编译按用户选择分流：官方订阅保留 Codex SDK；自定义 API 使用 Pi SDK。
前面的转换、衔接、标题与标签引用 worker 沿用原调用方式。

- Pi 使用配置中的 API 根地址、密钥、模型、协议和结构思考强度。
- 工具循环、瞬时重试、自动压缩与 JSONL 会话由 Pi 负责。
- Python 仅负责派发、记录事件与用量、暂停，以及结束后的真实编译检查。
- 每次继续读取同一 Pi 会话与当前工程。密钥通过标准输入进入子进程内存。
- Pi 的本地工具继承服务进程权限，不提供 Codex 的系统沙箱；已关闭技能、扩展及工程指令自动加载。
- 未知模型采用 128000 上下文与 16384 单次输出的保守默认容量；实际值记录于 `pi-compiler.json`。

验证：

1. 真实 Pi SDK + 本地模型接口：原生 read/edit/PowerShell 修改 TeX、运行 XeLaTeX，生成有效 PDF。
2. 同一会话继续，包含此前消息；单次用量不重复累计整个会话。
3. 上游错误保留；暂停调用 Pi abort，并保留工程与会话。
4. 长会话触发 Pi 原生自动压缩，记录摘要事件及摘要请求用量。
5. Responses 流协议保留原模型、思考强度和工具配置。
6. 已配置的真实 DeepSeek API（deepseek-v4.1-flash-expires-on-0910、max）：独立小工程包含未定义命令，Pi 自行定位修改并执行两次 XeLaTeX；应用复查 PASSED。回执位于 `.bookanalyst/pi-sdk-probe/`。
7. Python wheel 包含运行入口及 npm 锁文件，不包含 node_modules。
