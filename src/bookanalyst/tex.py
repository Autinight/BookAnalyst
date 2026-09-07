"""Compile the assembled document using the installed XeLaTeX."""

import asyncio, os, re, shutil
from pathlib import Path


async def compile_tex(directory, executable=None):
    executable = executable or shutil.which("xelatex")
    if not executable:
        return {
            "status": "FAILED",
            "code": "DEPENDENCY_MISSING",
            "message": "未找到 XeLaTeX",
            "passes": 0,
            "evidence_origin": "live",
        }
    directory = Path(directory)
    output = ""
    for attempt in range(1, 4):
        process = await asyncio.create_subprocess_exec(
            executable,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-file-line-error",
            "-jobname=main",
            r"\tracinglostchars=3 \input{main.tex}",
            cwd=directory,
            env=dict(os.environ, openin_any="p", openout_any="p"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), 90)
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "status": "FAILED",
                "code": "COMPILE_TIMEOUT",
                "passes": attempt,
                "evidence_origin": "live",
            }
        output = raw.decode("utf-8", errors="replace")
        if process.returncode:
            return {
                "status": "FAILED",
                "code": "TEX_ERROR",
                "log": output[-12000:],
                "errors": [
                    {"file": f, "line": int(line), "message": message}
                    for f, line, message in re.findall(
                        r"((?:body|headings)\.tex):(\d+): ([^\r\n]+)", output
                    )
                ],
                "passes": attempt,
                "evidence_origin": "live",
            }
        rerun = bool(
            re.search(
                r"Rerun to get|Please rerun|Label\(s\) may have changed|There were undefined references|rerun LaTeX",
                output,
            )
        )
        if not rerun:
            break
    if rerun or re.search(
        r"LaTeX Warning:.*undefined|Reference .* undefined|Citation .* undefined|multiply defined|Undefined control sequence",
        output,
    ):
        return {
            "status": "FAILED",
            "code": "REFERENCE_ERROR",
            "log": output[-12000:],
            "passes": attempt,
            "evidence_origin": "live",
        }
    if not (directory / "main.pdf").is_file() or not (directory / "main.aux").is_file():
        return {
            "status": "FAILED",
            "code": "COMPILE_OUTPUT_MISSING",
            "passes": attempt,
            "evidence_origin": "live",
        }
    return {"status": "PASSED", "passes": attempt, "evidence_origin": "live"}
