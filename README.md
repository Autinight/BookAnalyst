# BookAnalyst

基于 MinerU 与 LLM 的数学书籍 PDF → LaTeX 本地工作台。恢复数学表达、标题层级与作者编号，由统一 TeX 模板重新排版。原书布局只作为定位和核对依据。

## 启动

需要 Python 3.12、uv，以及 PATH 中的 XeLaTeX。在项目根目录执行：

```powershell
uv sync --frozen --extra dev
uv run --no-sync bookanalyst
```

打开 **http://127.0.0.1:8765**，也可运行 `powershell -File scripts/start.ps1`。本机已有 TeX Live 已加入用户 PATH；脚本使用现有环境，不下载 TeX。旧终端需重新打开才能收到环境更新。

## 书籍转换

主界面选择整本书、MinerU 线上或本地策略、LLM 并发数量与累计请求预算，然后开始运行。可复用相同书籍的整本解析缓存。模型连接在独立设置页管理：优先支持 ChatGPT 官方订阅，也支持自定义 Chat Completions／Responses API，包括另行部署的 vLLM。

处理单位随阶段明确区分：

1. 程序根据 MinerU 的页数与文件大小上限拆分 PDF，用于提交解析。
2. 聚合全部 MinerU JSON，保留稳定来源 ID、原始 block 与资源。后续不按页分派模型任务。
3. 分批分析 block，传递标题路径、开放的数学环境、相邻原文与续接证据；建立全书标题树，统一校验目录、引用和作者编号。
4. 优先将完整语义子树组成转换任务；超出容量的章节或长证明在子节点边界拆分。完整公式不拆开，过大的原子对象阻断并保留证据。
5. 并行转换、独立审查与相邻边界核对，通过后由程序统一生成 TeX、编译、核验和导出。

原始 PDF／解析内容／TeX 三栏按来源 ID 联动。页序号仅用于查看证据和视觉核对。完整原图核对采用“独立看图读数 → 比较解析内容”两次调用；验收报告明确记录实际核对范围。

分析、转换和边界审查均有持久化检查点。失败后可从阶段恢复；输入和审查配置未变的已通过工作可复用。局部转换重跑会更新对应任务与相邻边界，累计预算不清零。

## 测试用书与验证

完整测试书已保存为 [elliptic-pde-second-order.pdf](tests/data/books/elliptic-pde-second-order.pdf)，共 532 页；[书籍清单](tests/data/books.json)记录大小与 SHA-256。

[整书回归](tests/test_book_scale.py)对真实 PDF 做 532 页分块，使用明确标记的模拟 MinerU JSON 和模型服务，检查跨解析分块公式、长证明、全书目录与引用、故障恢复、局部重跑和真实 XeLaTeX 编译。模拟测试验证程序在整书规模下的行为，不能代表这本书的 OCR 与真实模型转换质量。

开发 API 保留 `offline_fixture`、`cloud_smoke`、`local_smoke`、`llm_smoke` 用于隔离适配器故障；这些有限范围测试不作为主界面的书籍处理分类。

官方订阅模型列表读取成功，包括上游实际返回的 `gpt-6-astra`。用户授权扩展后，532 页真实 MinerU 解析已全部完成，已开始最多 100 次官方订阅真实 LLM 联调。下载连接和真实资源路径兼容问题已修复。全书 TeX 验收及本机 MinerU／vLLM 部署尚未完成。详见 [验证记录](docs/validation.md) 与 [实现状态](docs/implementation.md)。

## 配置与数据

真实密钥放在本地 `.env` 或环境变量；示例见 [.env.example](.env.example)。MinerU 与自定义 LLM 使用不同凭据；官方订阅由官方运行时管理登录。连接字段见 [供应商配置](docs/llm-providers.md)。

运行状态、SQLite 调用账本和产物保存在忽略版本控制的 `.bookanalyst/`，临时验证文件位于 `tmp/`。关闭浏览器不会终止后台服务；停止服务后需要恢复运行，不自动重发结果不明的请求。

## 测试与文档

```powershell
uv run --no-sync pytest -q
```

整书测试需要 PATH 中的 XeLaTeX，否则会明确跳过。浏览器测试 [tests/browser_smoke.cjs](tests/browser_smoke.cjs)需要 Node.js、Playwright 与 Microsoft Edge；设置 `BOOKANALYST_PLAYWRIGHT` 为已有模块路径后执行 `node tests/browser_smoke.cjs`。它只读取账户模型元数据、创建离线夹具，不上传书籍或执行真实推理。

- [严格工作流](docs/workflow.md)与 [执行契约](docs/workflow.contract.json)：参与成员、阶段、关卡及恢复。
- [本地 Web 操作](docs/local-web.md)：来源定位、修正和导出。
- [实现状态](docs/implementation.md)：已实现行为与尚未完成部分。
- [服务部署](docs/deployment.md)：本地启动与独立服务接入。
