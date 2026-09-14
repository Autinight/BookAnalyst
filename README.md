# BookAnalyst

数学文献 PDF → LaTeX 本地工作台。v0.7.1 直接从原页图像转换内容，使用固定公共 TeX 样式保留作者的数学表达、编号与标题架构。

## 启动

需要 Python 3.12、uv，以及 PATH 中的 XeLaTeX。自定义 API 的最终编译使用 pi.dev SDK，另需 Node.js 22.19+。首次安装运行 `npm ci --prefix src/bookanalyst/pi_runtime --ignore-scripts`。

```powershell
uv sync --frozen --extra dev
powershell -File scripts/start.ps1 -Port 8766
```

打开 http://127.0.0.1:8766。已有虚拟环境时直接运行启动脚本即可；脚本使用已安装的 TeX，不下载第二份环境。

## 当前流程

全书设置 → 固定公共 TeX 样式 → 按页分批视觉转换 → 批次衔接 → 标题层级与附录 → 标签与引用 → 编译与修复。

全书设置与最终结构任务默认 `xhigh`；转换与衔接默认 `medium`。转换直接用 `\label{lemma:2.3}` 和相同键的 `\ref`，可见编号由全书计数规则自然生成，标题阶段先确定层级与附录起点，独立的标签与引用阶段读取已确定的标题，重新计算章节作用域；两步分别保存和恢复。

每批页数默认 3，可调整；并发默认 2，转换、衔接和标签／引用 worker 共用该设置。引用按异常组并发，每组初始仅接收相关上下文和候选目标，可按需搜索全书、读取正文上下文及查看原页；查询不扩大修改范围，成功组独立保存与恢复。每个转换任务收到自己分配的页图和简短公共约定。批内衔接随转换完成；worker 显式报告未闭合环境，程序核对并定位。seam 将其作为必须解决的待办，按需续读后页，确认匹配闭合后才能通过。主路径已移除 MinerU、逐 block 核对、修正登记和数学命令白名单。结构化 worker 不设置输入 token 门槛或输出 token 上限。标签／引用 worker 和最终编译 Agent 不设轮数上限，可手动暂停；其他阶段同一处理单元每轮最多 8 次自动修复。收尾先编译当前工程，失败时交给持续 Agent 会话（ChatGPT 官方订阅用 Codex，Grok 官方订阅与自定义 API 用 Pi），直接读写工程、执行编译并自动管理上下文；恢复沿用原会话和当前文件。程序重新编译通过并生成有效 PDF 即完成，不再额外进行全书质量验收。

前端只轮询当前运行的轻量状态。图片、当前页 TeX 和请求账本按需加载，轮询不重建阅读区。完成的批次即时保存；恢复先检查已收到的模型结果，避免重复派发。

## 模型与数据

优先使用 ChatGPT 官方订阅，模型列表实时取自官方运行时；也可使用 Grok 官方订阅（SuperGrok / X Premium+ 的浏览器 OAuth），以及自定义 OpenAI 兼容 API / vLLM。连接、鉴权及默认模型属于独立设置，见 [供应商配置](docs/llm-providers.md)。自定义 API 密钥直接在设置页输入，由后台本地保存；保存后不回显，不进入导出。旧环境变量配置继续兼容。

完整测试书仍保存在 [532 页数学书](tests/data/books/elliptic-pde-second-order.pdf) 和 [45 页 Wang 论文](tests/data/papers/wang-2022-g-invariant-min-max.pdf)。运行数据与原始请求保存在 `.bookanalyst/`。旧版记录可查看、下载已有产物；新建任务使用 v0.7。重构前的完整代码保存在 Git 提交 `5771789`。

## 验证与维护

```powershell
uv run --no-sync pytest -q
```

测试包括 532 页分批、恢复复用、自动错误反馈与收尾、原子操作、页面归属和真实 XeLaTeX 编译。测试中的替代模型只验证程序行为；真实转换记录另见 [v0.7 验证记录](docs/validation-v0.7.md)与 [v0.7.1 收尾恢复记录](docs/validation-v0.7.1.md)。

- [当前严格工作流](docs/workflow-v0.7.md)：参与成员、输入和交付边界。
- [实现结构](docs/implementation.md)：模块职责、保存和恢复。
- [本地操作](docs/local-web.md)与 [启动部署](docs/deployment.md)。
- [旧 v0.5 验证](docs/validation-v0.5.md)：仅供历史对照，不代表 v0.7 验证。
