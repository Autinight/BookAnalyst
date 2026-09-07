# 启动与独立模型服务

需要 Python 3.12、uv、PATH 中的 XeLaTeX。项目目录内运行：

```powershell
uv sync --frozen --extra dev
powershell -File scripts/start.ps1 -Port 8766
```

服务只监听 `127.0.0.1`，对同一 `.bookanalyst` 数据目录只启动一个服务进程。CLI 也可执行 `uv run --no-sync bookanalyst --port 8766 --workspace D:/Hanako/Projects/BookAnalyst`。启动脚本刷新已有用户 PATH；项目不安装或重复下载 TeX。

官方订阅复用官方 Codex 运行时登录，`BOOKANALYST_CODEX_BIN` 可指定运行时；否则优先使用 PATH。新建任务从上游读取实际可用模型。

vLLM 作为独立的自定义 API 服务接入。填写实际 API 根地址、模型 ID、协议和鉴权方式，并确认多模态输入能力。BookAnalyst 不加载本地模型权重，也不在启动时下载模型。真实本机 vLLM 部署尚未验证。

v0.6 主流程不使用 MinerU，不需要为启动工作台部署 MinerU。旧凭据和历史解析缓存仍保存在本地。
