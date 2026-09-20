"""One service per workspace, shared by browser and desktop entry points."""

import json
import os
import socket
import time
from pathlib import Path

import httpx
import uvicorn


class ServiceBusy(RuntimeError):
    pass


class WorkspaceService:
    def __init__(self, workspace, port=8766):
        self.workspace = Path(workspace).resolve()
        self.port = port
        self.root = self.workspace / ".bookanalyst"
        self.lock = None
        self.socket = None
        self.server = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / "service.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.lock.seek(0, 2)
                if not self.lock.tell():
                    self.lock.write(b"0")
                    self.lock.flush()
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock.close()
            self.lock = None
            raise ServiceBusy("此工作区的服务已经启动。") from exc
        try:
            # Bind before create_app: a failed launch must not recover active jobs.
            self.socket = socket.socket()
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.bind(("127.0.0.1", self.port))
            self.port = self.socket.getsockname()[1]
            self.url = f"http://127.0.0.1:{self.port}"
            from .app import create_app
            self.server = uvicorn.Server(uvicorn.Config(
                create_app(self.workspace), host="127.0.0.1", port=self.port,
                log_config=None, access_log=False,
            ))
            (self.root / "service.json").write_text(
                json.dumps({"port": self.port}), encoding="utf-8"
            )
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def run(self):
        self.server.run(sockets=[self.socket])

    def __exit__(self, *args):
        if self.socket:
            self.socket.close()
        if self.lock:
            (self.root / "service.json").unlink(missing_ok=True)
            self.lock.close()


def find_service(workspace):
    workspace = Path(workspace).resolve()
    try:
        port = json.loads((workspace / ".bookanalyst/service.json").read_text())["port"]
        if not isinstance(port, int) or not 1 <= port <= 65535:
            return None
        url = f"http://127.0.0.1:{port}"
        with httpx.Client(trust_env=False, timeout=1) as client:
            info = client.get(url + "/api/health").json()
        if isinstance(info, dict) and info.get("application") == "BookAnalyst" and isinstance(info.get("workspace"), str) and Path(info["workspace"]).resolve() == workspace:
            return url
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        pass
    return None


def wait_for_service(workspace, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = find_service(workspace)
        if url:
            return url
        time.sleep(0.1)
    raise RuntimeError("本地服务未能启动，请查看 tmp/desktop.log。")
