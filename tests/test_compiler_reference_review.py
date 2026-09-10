"""User-owned unresolved references must not trigger another compilation agent."""
import json

import pytest
from fastapi.testclient import TestClient

from bookanalyst.reference_review import compiler_diagnostics, compiler_reference_policy
from bookanalyst.store import atomic_json, atomic_text
from bookanalyst.tex import compile_tex
from test_rebuild import make_run, BOOK
from test_numbering import batch
from test_library_outputs import completed
from bookanalyst.library_outputs import retain_run


KEY = "theorem:9.7"
NOTES = [{"id": "p1-reference-0", "key": KEY, "page": 1,
          "reason": "Source says theorem; only an unrelated lemma has this number.", "search_evidence": []}]


@pytest.mark.parametrize("kind", ["Reference", "Citation"])
def test_only_registered_warnings_are_excluded_even_when_wrapped(kind):
    output = (f"LaTeX Warning: {kind} `theorem:9.\n7' on page 194 undefined on input line 6363\n.\n"
              "LaTeX Warning: Reference `theorem:other' on page 1 undefined on input line 1.\n"
              "LaTeX Warning: There were undefined references.\n"
              "Undefined control sequence\nLabel `x' multiply defined.")
    active, keys = compiler_diagnostics(output, {KEY})
    assert keys == [KEY] and "theorem:9." not in active
    assert "theorem:other" in active and "Undefined control sequence" in active and "multiply defined" in active


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["only_pending", "unregistered", "tex_error", "duplicate"])
async def test_real_compiler_allows_registered_warning_but_not_other_failures(tmp_path, case):
    body = r"See \ref{theorem:9.7}."
    if case == "unregistered":
        body += r" See \ref{theorem:other}."
    elif case == "tex_error":
        body += r" \undefinedBookAnalystCommand"
    elif case == "duplicate":
        body += r" \section{One}\label{same}\section{Two}\label{same}"
    atomic_text(tmp_path / "main.tex", r"\documentclass{article}\begin{document}" + body + r"\end{document}")
    atomic_json(tmp_path / "reference-review.json", NOTES)
    before = (tmp_path / "main.tex").read_bytes()
    result = await compile_tex(tmp_path)
    assert (tmp_path / "main.tex").read_bytes() == before
    assert json.loads((tmp_path / "reference-review.json").read_text()) == NOTES
    assert "undefined" in (tmp_path / "main.log").read_text()
    if case == "only_pending":
        assert result["status"] == "PASSED" and result["unconfirmed_references"] == [KEY]
        assert (tmp_path / "main.pdf").is_file()
    else:
        assert result["status"] == "FAILED"
        assert result["code"] == ("TEX_ERROR" if case == "tex_error" else "REFERENCE_ERROR")


@pytest.mark.asyncio
async def test_pending_reference_does_not_invoke_agent(app, monkeypatch):
    engine, run = app.state.engine, make_run(app)
    base = engine.store.directory(run["id"])
    atomic_json(base / "reference-review.json", NOTES)
    async def forbidden(*args):
        pytest.fail("Pending reference must not be handed to the compiler agent")
    monkeypatch.setattr("bookanalyst.engine.repair_project", forbidden)
    await engine.compile(run, [batch(1, r"See \ref{theorem:9.7}.")], [], {"documentclass": "article"})
    report = json.loads((base / "finish-report.json").read_text())
    assert report["status"] == "PASSED"
    assert report["compile_result"]["unconfirmed_references"] == [KEY]
    assert r"\ref{theorem:9.7}" in (base / "tex/body.tex").read_text()


def test_report_is_for_user_and_is_retained_with_book(app):
    store = app.state.store
    run, project = completed(app)
    notes = [NOTES[0] | {"reason": '<script>alert("x")</script>'}]
    atomic_json(project.parent / "reference-review.json", notes)
    atomic_json(project / "reference-review.json", notes)
    retain_run(store, run["id"])
    with TestClient(app) as client:
        status = client.get(f"/api/runs/{run['id']}/status").json()
        assert status["reference_review_count"] == 1
        for url in (f"/api/runs/{run['id']}/reference-review", f"/api/books/{BOOK}/reference-review"):
            response = client.get(url)
            assert response.status_code == 200
            assert "用户核对" in response.text and KEY in response.text
            assert "<script>" not in response.text and "&lt;script&gt;" in response.text
            assert client.get(url + "?download=true").json() == notes
        book = next(b for b in client.get('/api/bootstrap').json()['books'] if b['id'] == BOOK)
        assert book['result']['review_count'] == 1
    policy = compiler_reference_policy(project)
    assert KEY in policy and "Do not investigate" in policy and "not resolved" in policy
    assert '<script>' not in policy and "Source says" not in policy
