"""Native TOC generation is a compiler task, not an extra LLM review."""

import shutil
import pytest
import pypdfium2 as pdfium
from bookanalyst.tex import compile_tex
from bookanalyst.documents import public_preamble


def pdf_text(path):
    fragments = []
    with pdfium.PdfDocument(str(path)) as document:
        for i in range(len(document)):
            page = document[i]
            text = page.get_textpage()
            fragments.append(text.get_text_range())
            text.close()
            page.close()
    return "".join(fragments)


@pytest.mark.asyncio
async def test_native_contents_populates_and_refreshes_without_reference_warnings(
    tmp_path,
):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    # No hyperref or label/ref warnings: main.toc changes alone must cause another pass.
    source = r"""\documentclass{article}
\begin{document}
\tableofcontents
\section{FirstTopic}
Original content.
\end{document}
"""
    main = tmp_path / "main.tex"
    main.write_text(source, encoding="utf-8")
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED" and report["passes"] >= 2, report
    assert pdf_text(tmp_path / "main.pdf").count("FirstTopic") == 2
    main.write_text(source.replace("FirstTopic", "UpdatedTopic"), encoding="utf-8")
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED" and report["passes"] >= 2, report
    text = pdf_text(tmp_path / "main.pdf")
    assert text.count("UpdatedTopic") == 2 and "FirstTopic" not in text


@pytest.mark.asyncio
async def test_global_contents_title_and_depth_are_used_by_native_tex(tmp_path):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    setup = {
        "documentclass": "article",
        "public_tex": r"\renewcommand{\contentsname}{BookOutline}\setcounter{tocdepth}{1}",
    }
    (tmp_path / "preamble.tex").write_text(public_preamble(setup), encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        r"""\documentclass{article}
\input{preamble}
\begin{document}
\tableofcontents
\section{FirstTopic}
Original content.
\subsection{DetailedTopic}
More original content.
\end{document}
""",
        encoding="utf-8",
    )
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED", report
    text = pdf_text(tmp_path / "main.pdf")
    assert "BookOutline" in text and text.count("FirstTopic") == 2
    assert text.count("DetailedTopic") == 1  # Present in body, excluded from the TOC.
