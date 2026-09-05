# 本地运行与服务接入

更新：2026-09-05。

## BookAnalyst

在项目根目录执行：

```powershell
uv sync --frozen --extra dev
uv run --no-sync bookanalyst --port 8765
```

或者在虚拟环境已经安装后运行：

```powershell
powershell -File scripts/start.ps1 -Port 8765
```

服务只监听 127.0.0.1，使用一个执行器进程。不要对同一数据目录启动多个 worker。运行数据默认保存在项目 `.bookanalyst`，可用 `BOOKANALYST_DATA_DIR` 指定其他目录；从其他目录启动时传入 `--workspace` 指向项目根目录。

XeLaTeX 必须能通过 PATH 发现。检查命令为 `Get-Command xelatex` 和 `xelatex --version`。本机已使用现有 TeX Live 2026；项目不另行安装 TeX，也不使用绝对编译器路径配置。

## MinerU 线上 API

在 `.env` 中保存 `MINERU_API_TOKEN`，设置页默认根地址为 `https://mineru.net/api/v4`。已有凭据不会通过前端或导出文件返回。

主界面开始运行会按所选整本书与预算执行。页数与文件大小上限仅用于拆分 MinerU 提交；聚合后的模型任务按 block 与语义结构规划。开发 API 的 cloud_smoke 保留最多一次提交、最多三页、零 LLM 调用限制，用于独立排查连接。用户已扩大授权，完整 532 页真实解析已通过 3 次提交完成；恢复使用已有结果，本轮真实 LLM 联调上限为 100 次累计请求。

调用顺序是申请上传地址、上传实际 PDF 分块、查询同一批次、下载结果 ZIP。上传地址与结果地址不携带 API Bearer 请求头。下载失败时保留远端任务 ID，恢复不会重新申请解析任务。

本机结果 CDN 曾受代理 fake-DNS 影响。在项目 .env 显式设置 `MINERU_DOWNLOAD_PUBLIC_DNS=1` 时，只有官方结果域名连接失败才查询公开 DNS，以原始 Host／TLS SNI 连接返回的公共 IPv4 地址。保持证书验证，不向 DNS 或下载地址转发 MinerU Bearer，也不修改系统代理；默认关闭，服务网络正常时无需启用。

## MinerU 本地服务

当前已实现异步任务 API 客户端，尚未在本机安装模型和执行真实本地解析。服务应独立于 BookAnalyst Python 环境部署，避免 Torch、解析模型依赖影响工作台。

需要服务提供：

- `POST /tasks` 接受 PDF 与解析参数并返回 task_id。
- `GET /tasks/{task_id}` 查询同一任务。
- `GET /tasks/{task_id}/result` 返回包含 middle/layout JSON 和资源的 ZIP。

设置页默认本地地址为 `http://127.0.0.1:8000`，后端为 `pipeline`。部署时以 [MinerU 官方运行说明](https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/quick_usage.md) 与 [异步服务接口实现](https://github.com/opendatalab/MinerU/blob/master/mineru/cli/fast_api.py) 为准；安装版本及硬件支持范围需在实际部署时固定并验证。

启动服务后使用 `local_smoke`，范围固定为一页、一次解析、零 LLM 调用。服务未就绪时记录连接失败，不把离线夹具冒充为本地部署测试。

## vLLM

vLLM 是自定义 LLM API 的一种部署方式，BookAnalyst 本身不承载模型权重或 GPU 推理。独立部署后，在“自定义 API”填写其实际根地址、协议、模型 ID 和鉴权方式；多模态模型还需确认图像传输与能力。

当前已验证自定义 HTTP 协议适配器的模拟调用，未完成本机 vLLM 部署。后续部署按本机资源只做安装、服务启动与最小调用，不安排本机全书推理。
