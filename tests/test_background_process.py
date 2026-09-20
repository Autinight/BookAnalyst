"""Exercise the SDK's actual launch path without changing global subprocess."""

import os
import subprocess
import sys

import pytest

from bookanalyst.background_process import hide_codex_console


@pytest.mark.skipif(os.name != "nt", reason="Windows process creation flags")
def test_codex_transport_hides_console_and_preserves_stdio(monkeypatch):
    from openai_codex import client

    original_popen = subprocess.Popen
    monkeypatch.setattr(client, "subprocess", subprocess)
    hide_codex_console()
    facade = client.subprocess
    hide_codex_console()
    assert client.subprocess is facade
    assert subprocess.Popen is original_popen

    calls = []

    def spawn(*args, **kwargs):
        calls.append(kwargs.copy())
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    transport = client.CodexClient(client.CodexConfig(launch_args_override=(
        sys.executable, "-c",
        "import ctypes, sys; print(sys.stdin.readline().strip(), flush=True); "
        "print(ctypes.windll.kernel32.GetConsoleWindow(), flush=True)",
    )))
    # The test talks directly to the redirected streams instead of JSON-RPC.
    monkeypatch.setattr(transport, "_start_reader_thread", lambda: None)
    monkeypatch.setattr(transport, "_start_stderr_drain_thread", lambda: None)
    try:
        transport.start()
        proc = transport._proc
        assert calls[0]["creationflags"] & subprocess.CREATE_NO_WINDOW
        proc.stdin.write("pipe-check\n")
        proc.stdin.flush()
        assert proc.stdout.readline().strip() == "pipe-check"
        assert proc.stdout.readline().strip() == "0"
        assert proc.wait(timeout=5) == 0
    finally:
        transport.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process creation flags")
def test_hidden_launch_preserves_other_creation_flags(monkeypatch):
    from bookanalyst.background_process import _WindowlessSubprocess

    received = {}
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: received.update(kwargs))
    _WindowlessSubprocess.Popen(["tool"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    assert received["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


@pytest.mark.asyncio
async def test_xelatex_background_launch(tmp_path, monkeypatch):
    from bookanalyst import tex

    calls = []

    class Process:
        returncode = 1

        async def communicate(self):
            return b"test compilation error", None

    async def spawn(*args, **kwargs):
        calls.append(kwargs)
        return Process()

    monkeypatch.setattr(tex.asyncio, "create_subprocess_exec", spawn)
    result = await tex.compile_tex(tmp_path, executable="xelatex")
    assert result["code"] == "TEX_ERROR"
    assert calls[0]["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert calls[0]["stdout"] == subprocess.PIPE
