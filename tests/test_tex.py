import pytest
from bookanalyst.tex import compile_tex


@pytest.mark.asyncio
@pytest.mark.parametrize("log,expected,passes", [
    ("(D:/texlive/tex/latex/rerunfilecheck/rerunfilecheck.sty)\nOutput written on main.pdf.", "PASSED", 1),
    ("LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.", "REFERENCE_ERROR", 3),
])
async def test_package_filename_is_not_a_rerun_warning(tmp_path, monkeypatch, log, expected, passes):
    (tmp_path / "main.aux").write_text("", encoding="utf-8")
    (tmp_path / "main.pdf").write_bytes(b"%PDF-fixture")
    launches = []

    class Process:
        returncode = 0

        async def communicate(self):
            return log.encode(), None

    async def launch(*args, **kwargs):
        launches.append(args)
        return Process()

    monkeypatch.setattr("bookanalyst.tex.asyncio.create_subprocess_exec", launch)
    result = await compile_tex(tmp_path, [], {"nodes": []}, executable="test-xelatex")
    assert result.get("code", result["status"]) == expected
    assert len(launches) == passes
