"""Actual XeLaTeX image sizes preserve source scale and only shrink to fit."""
import re
import shutil

import pytest
from PIL import Image

from bookanalyst.documents import public_preamble
from bookanalyst.tex import compile_tex


@pytest.mark.asyncio
async def test_figures_keep_physical_size_and_shrink_with_preserved_aspect_ratio(tmp_path):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    assets = tmp_path / "assets"
    assets.mkdir()
    cases = {
        "small": ((150, 75), 150, (72.27, 36.135)),
        "highres": ((300, 150), 300, (72.27, 36.135)),
        "wide": ((1500, 300), 150, (200, 40)),
        "tall": ((75, 1500), 150, (12, 240)),
        "both": ((600, 1500), 150, (96, 240)),
    }
    for name, (size, dpi, _) in cases.items():
        Image.new("RGB", size, "gray").save(
            assets / f"{name}.png", dpi=(dpi, dpi)
        )
    (tmp_path / "preamble.tex").write_text(
        public_preamble({"documentclass": "article"}), encoding="utf-8"
    )
    (tmp_path / "main.tex").write_text(
        r"""\documentclass{article}
\input{preamble}
\setlength{\textwidth}{200pt}
\setlength{\textheight}{300pt}
\newsavebox{\MeasuredFigure}
\newcommand{\MeasureFigure}[1]{%
  \sbox{\MeasuredFigure}{\BAFigure{#1}}%
  \typeout{BA-SIZE:#1:\the\wd\MeasuredFigure:\the\dimexpr\ht\MeasuredFigure+\dp\MeasuredFigure\relax}%
}
\begin{document}
"""
        + "\n".join(r"\MeasureFigure{" + name + "}" for name in cases)
        + r"""
\begin{minipage}{50pt}
\MeasureFigure{small}
\end{minipage}
Size checks.
\end{document}
""",
        encoding="utf-8",
    )
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED", report
    log = (tmp_path / "main.log").read_text(encoding="utf-8")
    sizes = re.findall(r"BA-SIZE:(\w+):([\d.]+)pt:([\d.]+)pt", log)
    assert len(sizes) == len(cases) + 1
    for name, width, height in sizes[:-1]:
        assert (float(width), float(height)) == pytest.approx(cases[name][2], abs=0.05)
    assert sizes[-1][0] == "small"
    assert tuple(map(float, sizes[-1][1:])) == pytest.approx((50, 25), abs=0.05)
