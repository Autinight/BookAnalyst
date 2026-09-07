# BookAnalyst

数学文献 PDF → LaTeX 本地工作台。v0.6 直接从原页图像转换内容，使用固定公共 TeX 样式保留作者的数学表达、编号与标题架构。

## 启动

需要 Python 3.12、uv，以及 PATH 中的 XeLaTeX。

```powershell
uv sync --frozen --extra dev
powershell -File scripts/start.ps1 -Port 8766
```

打开 http://127.0.0.1:8766。已有虚拟环境时直接运行启动脚本即可；脚本使用已安装的 TeX，不下载第二份环境。

## 当前流程

全书设置 → 固定公共 TeX 样式 → 按页分批视觉转换 → 批次衔接 → 标题层级与编译。

每批页数默认 3，可调整；并发默认 2。每个转换任务收到自己分配的页图和简短公共约定。批内衔接随转换完成，批间问题由接缝任务处理。主路径已移除 MinerU、逐 block 核对、修正登记和数学命令白名单。不设置输入 token 门槛或输出 token 上限。

前端只轮询当前运行的轻量状态。图片、当前页 TeX 和请求账本按需加载，轮询不重建阅读区。完成的批次即时保存；恢复先检查已收到的模型结果，避免重复派发。

## 模型与数据

优先使用 ChatGPT 官方订阅，模型列表实时取自官方运行时；也支持自定义 OpenAI 兼容 API / vLLM。连接、鉴权及默认模型属于独立设置，见 [供应商配置](docs/llm-providers.md)。密钥只保存在本地环境变量或 `.env`，不进入前端或导出。

完整测试书仍保存在 [532 页数学书](tests/data/books/elliptic-pde-second-order.pdf) 和 [45 页 Wang 论文](tests/data/papers/wang-2022-g-invariant-min-max.pdf)。运行数据与原始请求保存在 `.bookanalyst/`。旧版记录可查看、下载已有产物；新建任务使用 v0.6。重构前的完整代码保存在 Git 提交 `5771789`。

## 验证与维护

```powershell
uv run --no-sync pytest -q
```

测试包括 532 页分批、恢复复用、手动重试、原子操作、页面归属和真实 XeLaTeX 编译。测试中的替代模型只验证程序行为；真实转换记录另见 [v0.6 验证记录](docs/validation-v0.6.md)。

- [当前严格工作流](docs/workflow-v0.6.md)：参与成员、输入和交付边界。
- [实现结构](docs/implementation.md)：模块职责、保存和恢复。
- [本地操作](docs/local-web.md)与 [启动部署](docs/deployment.md)。
- [旧 v0.5 验证](docs/validation-v0.5.md)：仅供历史对照，不代表 v0.6 验证。
