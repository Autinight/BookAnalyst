# BookAnalyst

基于 MinerU 与 LLM 的数学书籍 PDF → LaTeX 本地工作台。恢复数学表达、标题层级与作者编号，由统一 TeX 模板重新排版。原书布局只作为定位和核对依据。

## 启动

需要 Python 3.12、uv，以及 PATH 中的 XeLaTeX。在项目根目录执行：

```powershell
uv sync --frozen --extra dev
uv run --no-sync bookanalyst
```

打开 **http://127.0.0.1:8765**，也可运行 `powershell -File scripts/start.ps1`。本机已有 TeX Live 已加入用户 PATH；脚本使用现有环境，不下载 TeX。旧终端需重新打开才能收到环境更新。

## 新版工作流

后续改造方案见 [v0.6 视觉独立转换工作流](docs/workflow-v0.6.md)：全书设置 → 固定公共 TeX 样式 → 按页分批视觉独立转换 → 衔接处理 → 标题层级修复。当前服务仍执行以下 v0.5 流程。

本工作树采用 [工作流 v0.5](docs/workflow.md)：用户指定每个任务处理几页，程序把这些页全部保留的 block 分给转换 LLM，并允许按需查询前后一页。转换时直接对照原图、修正符号并生成 TeX；程序按来源合并和编译。默认只对具体问题追加局部 LLM 处理，逐批独立审查为可选模式。

不做前置疑点登记，不先逐页修完符号，也不把建立整书语义树作为转换前提。页码只用于分派和定位，不生成原书排版。

新版执行器与面板已实现。每批页数和并发可设置；默认 `review_mode=on_demand`，可选 `all`；没有前置整书语义树、逐页转录或固定边界审查。实现与真实联调状态见 [实现状态](docs/implementation.md)。

## 测试资料与验证

完整测试资料包括 [532 页数学书](tests/data/books/elliptic-pde-second-order.pdf) 和 [45 页 Wang 论文](tests/data/papers/wang-2022-g-invariant-min-max.pdf)，来源哈希见 [书籍清单](tests/data/books.json)。

[新版程序测试](tests/test_pageflow.py)使用真实 MinerU 解析的 45 页、893 个原始块检查分派和来源覆盖，并验证邻页只读、直接转换纠错、断点恢复、局部修复与实际 XeLaTeX 编译。程序测试中的替代模型只检验程序行为，不证明真实转换保真度。

新版 Wang 完整 45 页真实流程已跑通（运行修订 8）：上游 `gpt-6-astra` 完成 23 批转换，804 个保留块按序唯一覆盖，138 个显式编号核对通过，生成 52 页 PDF。两遍 XeLaTeX 编译通过，最终没有缺字或纸面越界。累计 46 次应用层 LLM 调用，新增 MinerU 提交 0；程序回归 90 项通过。

这证明完整流程能够运行，不代表逐行无损还原。默认按需审查，未做全篇独立语义复核；抽查发现原文末页邮箱未进入 MinerU JSON，输出也缺少该行。真实用量、修复及已知局限见 [新版验证记录](docs/validation-v0.5.md)。[旧版记录](docs/validation.md)单独保留。

开发 API 中的 `offline_fixture`、`cloud_smoke`、`local_smoke`、`llm_smoke` 用于隔离适配器故障，不代表整书验证。

## 配置与数据

真实密钥放在本地 `.env` 或环境变量；示例见 [.env.example](.env.example)。MinerU 与自定义 LLM 使用不同凭据；官方订阅由官方运行时管理登录。连接字段见 [供应商配置](docs/llm-providers.md)。

运行状态、SQLite 调用账本和产物保存在忽略版本控制的 `.bookanalyst/`，临时验证文件位于 `tmp/`。关闭浏览器不会终止后台服务；停止服务后需要恢复运行，不自动重发结果不明的请求。

## 测试与文档

```powershell
uv run --no-sync pytest -q
```

编译回归需要 PATH 中的 XeLaTeX，否则会明确跳过。新版浏览器检查 [tests/browser_pageflow.cjs](tests/browser_pageflow.cjs)需要 Node.js、Playwright 与 Microsoft Edge。设置 `BOOKANALYST_PLAYWRIGHT`、`BOOKANALYST_URL` 及可选 `BOOKANALYST_RUN` 后执行；检查面板和设置，不创建推理请求。

- [严格工作流](docs/workflow.md)与 [执行契约](docs/workflow.contract.json)：参与成员、阶段、关卡及恢复。
- [本地 Web 操作](docs/local-web.md)：来源定位、修正和导出。
- [实现状态](docs/implementation.md)：已实现行为与尚未完成部分。
- [服务部署](docs/deployment.md)：本地启动与独立服务接入。
