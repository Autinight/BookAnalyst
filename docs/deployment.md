# 启动与独立模型服务

需要 Python 3.12、uv、PATH 中的 XeLaTeX。项目目录内运行：

```powershell
uv sync --frozen --extra dev
powershell -File scripts/start.ps1 -Port 8766
```

服务只监听 `127.0.0.1`，对同一 `.bookanalyst` 数据目录只启动一个服务进程。CLI 也可执行 `uv run --no-sync bookanalyst --port 8766 --workspace D:/Hanako/Projects/BookAnalyst`。启动脚本刷新已有用户 PATH；项目不安装或重复下载 TeX。

## Windows 桌面入口

```powershell
uv sync --frozen --extra desktop --extra dev
powershell -File scripts/start-desktop.ps1
```

也可双击根目录的 `BookAnalyst.vbs`，启动过程没有终端窗口。原有 `scripts/start-desktop.cmd` 入口也会启动桌面窗口。桌面运行时为 pywebview + 系统 Edge WebView2，需安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。桌面依赖是可选项，纯浏览器使用不需要安装。

桌面与命令行服务共用工作区锁；桌面启动会根据 `.bookanalyst/service.json` 发现服务，并核对 `/api/health` 返回的工作区。重复启动不会重新恢复或重置进行中的任务。桌面关闭只会停止由该窗口启动的服务；连接已有服务时不会停止它。关闭自有服务时如有运行中任务，会提示当前请求将中断、已完成结果保留，下次可从运行记录恢复。

端口被其他程序占用时会自动选择空闲端口，也可用 `scripts/start-desktop.ps1 -Port 8767` 指定。升级前启动的旧服务尚未记录工作区信息，需先结束任务并关闭旧服务。启动错误会弹窗，详细日志在 `tmp/desktop.log`。此入口使用项目的 `.venv`；当前提供源码工作区的桌面启动方式，并非打包安装程序。

PDF 预览和官方登录链接通过系统浏览器打开；TeX 工程下载使用原生保存对话框。相关设置依据 [pywebview API](https://pywebview.flowrl.com/api/)。

官方订阅复用官方 Codex 运行时登录，`BOOKANALYST_CODEX_BIN` 可指定运行时；否则优先使用 PATH。新建任务从上游读取实际可用模型。

vLLM 作为独立的自定义 API 服务接入。填写实际 API 根地址、模型 ID、协议和鉴权方式，并确认多模态输入能力。BookAnalyst 不加载本地模型权重，也不在启动时下载模型。真实本机 vLLM 部署尚未验证。

v0.6 主流程不使用 MinerU，不需要为启动工作台部署 MinerU。旧凭据和历史解析缓存仍保存在本地。

自定义 API 最终编译还需 Node.js 22.19+；在项目根目录运行 `npm ci --prefix src/bookanalyst/pi_runtime --ignore-scripts`。依赖固定为 pi.dev 官方 `@earendil-works/pi-coding-agent` 0.85.1，Python 安装包包含运行入口和锁文件，Node 依赖需另外安装。官方订阅继续使用 Codex SDK。
