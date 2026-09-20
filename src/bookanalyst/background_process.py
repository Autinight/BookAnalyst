"""Keep background tools from opening consoles in the Windows desktop app."""

import os
import subprocess


class _WindowlessSubprocess:
    def __getattr__(self, name):
        return getattr(subprocess, name)

    @staticmethod
    def Popen(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(*args, **kwargs)


def hide_codex_console():
    if os.name != "nt":
        return
    from openai_codex import client

    # The pinned SDK 0.147 does not expose process creation flags. Give only
    # its transport a subprocess facade; do not patch process-wide Popen or
    # modify the installed package. Arguments and stdio pipes stay unchanged.
    if not isinstance(client.subprocess, _WindowlessSubprocess):
        client.subprocess = _WindowlessSubprocess()
