"""Native window over the same local workbench used in a browser."""

import argparse
import errno
import logging
import os
import sys
import threading
from contextlib import ExitStack
from pathlib import Path

import httpx

from .service import ServiceBusy, WorkspaceService, find_service, wait_for_service


def launch(workspace, port=8766):
    import webview

    workspace = Path(workspace).resolve()
    with ExitStack() as stack:
        service = None
        thread = None
        url = find_service(workspace)
        if not url:
            try:
                service = stack.enter_context(WorkspaceService(workspace, port))
            except ServiceBusy:
                url = wait_for_service(workspace)
            except OSError as exc:
                if exc.errno not in (errno.EADDRINUSE, 10048):
                    raise
                # An older service has no discovery file. Do not open its data
                # under another port while it may still be processing a book.
                old_service = False
                try:
                    with httpx.Client(trust_env=False, timeout=2) as client:
                        response = client.get(f"http://127.0.0.1:{port}/api/bootstrap")
                        payload = response.json()
                        old_service = isinstance(payload, dict) and "token" in payload and "stages" in payload
                except (httpx.HTTPError, ValueError):
                    pass
                if old_service:
                    raise RuntimeError(
                        f"端口 {port} 上已有未关联到此工作区的 BookAnalyst。"
                        "请结束任务并关闭旧服务后重试，或为不同工作区指定 --port。"
                    ) from exc
                service = stack.enter_context(WorkspaceService(workspace, 0))
            if service:
                thread = threading.Thread(target=service.run, name="bookanalyst-service")
                thread.start()
        try:
            if not url:
                url = wait_for_service(workspace)
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            window = webview.create_window(
                "BookAnalyst", url + "/?desktop=1", width=1360, height=900,
                min_size=(800, 600), background_color="#f5f4f0",
            )

            def on_closing():
                if service:
                    try:
                        with httpx.Client(trust_env=False, timeout=2) as client:
                            runs = client.get(url + "/api/bootstrap").json()["runs"]
                        if any(r["state"] == "RUNNING" for r in runs):
                            return window.create_confirmation_dialog(
                                "关闭 BookAnalyst",
                                "还有任务正在运行。关闭窗口将中断当前请求，已完成结果会保留。确定关闭？",
                            )
                    except (httpx.HTTPError, ValueError, KeyError):
                        return window.create_confirmation_dialog(
                            "关闭 BookAnalyst", "暂时无法确认任务状态。仍要关闭本地服务？"
                        )
                return True

            window.events.closing += on_closing
            webview.start(
                gui="edgechromium" if os.name == "nt" else None,
                private_mode=False, storage_path=str(workspace / ".bookanalyst/desktop"),
            )
        finally:
            if service:
                service.server.should_exit = True
                thread.join()


def main():
    parser = argparse.ArgumentParser(description="BookAnalyst desktop workbench")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    logs = args.workspace.resolve() / "tmp"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=logs / "desktop.log", encoding="utf-8", level=logging.INFO)
    try:
        launch(args.workspace, args.port)
    except Exception as exc:
        logging.exception("Desktop startup failed")
        message = f"BookAnalyst 启动失败：{exc}\n\n首次使用请运行 uv sync --frozen --extra desktop --extra dev。\nWindows 需要 Microsoft Edge WebView2 Runtime。\n日志：{logs / 'desktop.log'}"
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "BookAnalyst", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
