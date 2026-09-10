"""Automatic feedback and real compiler recovery without repeating conversion."""

import copy
import json
import shutil
import pytest
from bookanalyst.documents import Conversion
from bookanalyst.llm import parse_json
from bookanalyst.numbering import Numbering, validate_numbering
from bookanalyst.store import WorkflowError, atomic_json
from test_numbering import example, rule
from test_rebuild import result, make_run


def require_tex():
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")


@pytest.mark.asyncio
async def test_counter_repair_rejected_plan_is_automatically_corrected(
    app, monkeypatch
):
    require_tex()
    engine = app.state.engine
    run = make_run(app)
    setup, results, headings = example()
    expected = copy.deepcopy(setup["numbering"])
    setup["numbering"] = {"rules": [], "initial": []}
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        assert purpose == "counter_repair"
        assert run["config"]["model"]["reasoning_effort"] == "xhigh"
        if len(calls) == 1:
            return {
                "rules": expected["rules"]
                + [rule("lemma", "section", "theorem", True)],
                "initial": [],
            }
        assert payload["repair_feedback"]["code"] == "COUNTER_RULE"
        assert (
            payload["repair_feedback"]["candidate"]["rules"][-1]["shared_with"]
            == "theorem"
        )
        return expected

    monkeypatch.setattr(engine.providers, "generate", generate)
    plan = await engine.checked_ask(
        run,
        "counter-rules",
        "counter_repair",
        {},
        Numbering,
        [],
        lambda value: validate_numbering(value, "article"),
    )
    assert plan == expected and len(calls) == 2


@pytest.mark.asyncio
async def test_repair_can_resolve_more_than_three_distinct_failures(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    setup = {"documentclass": "article", "numbering": {"rules": [], "initial": []}}
    data = result([1, 2, 3, 4]) | {"task_id": "batch-0001"}
    for page in data["pages"]:
        page["tex"] = f"BAD-{page['page']}"
    seen = []

    async def compile_tex(directory):
        body = (directory / "body.tex").read_text()
        for line, text in enumerate(body.splitlines(), 1):
            if text.startswith("BAD-"):
                return {
                    "status": "FAILED",
                    "code": "TEX_ERROR",
                    "errors": [{"file": "body.tex", "line": line, "message": text}],
                }
        (directory / "main.aux").write_text("")
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(directory / "main.pdf")
        return {"status": "PASSED"}

    async def agent(providers, current, project, report):
        assert report["status"] != "PASSED"
        bad = report["errors"][0]["message"]
        seen.append(bad)
        path = project / "body.tex"
        path.write_text(path.read_text().replace(bad, "Fixed."))

    monkeypatch.setattr("bookanalyst.engine.compile_tex", compile_tex)
    monkeypatch.setattr("bookanalyst.engine.repair_project", agent)
    await engine.compile(run, [data], [], setup)
    assert seen == ["BAD-1", "BAD-2", "BAD-3", "BAD-4"]
    assert (
        "BAD-" not in (engine.store.directory(run["id"]) / "tex/body.tex").read_text()
    )




@pytest.mark.asyncio
@pytest.mark.parametrize("old_stage_count", [5, 6])
async def test_old_combined_run_resumes_directly_into_finisher(app, monkeypatch, old_stage_count):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    run["stages"].pop("references")
    if old_stage_count == 5:
        run["stages"].pop("finish")
    run["stages"] = dict.fromkeys(run["stages"], "PASSED")
    run["stages"]["headings"] = "NEEDS_REVIEW"
    if old_stage_count == 6:
        run["stages"]["finish"] = "PENDING"
    run.update(state="RUNNING", stage="headings")
    run["config"]["resolved"] = True
    store.put("run", run["id"], run)
    base = store.directory(run["id"])
    setup, results, headings = example()
    for name, value in [
        ("setup", setup),
        ("structured", results),
        ("headings", headings),
        ("symbols", {}),
    ]:
        atomic_json(base / f"{name}.json", value)
    called = []

    async def compile(*args):
        called.append(1)

    async def forbidden(*args, **kwargs):
        pytest.fail("Completed setup/conversion/headings must not be repeated")

    monkeypatch.setattr(engine, "compile", compile)
    monkeypatch.setattr(engine.providers, "generate", forbidden)
    await engine.execute(run["id"])
    current = store.get("run", run["id"])
    assert current["state"] == "COMPLETED" and current["stages"]["finish"] == "PASSED"
    assert current["stages"]["references"] == "PASSED"
    assert called == [1]


@pytest.mark.asyncio
async def test_invalid_json_is_feedback_but_external_failures_are_not_retried(
    app, monkeypatch
):
    engine = app.state.engine
    run = make_run(app)
    calls = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if len(calls) == 1:
            return parse_json("invalid JSON", schema)
        assert payload["repair_feedback"]["candidate"] == "invalid JSON"
        assert run["config"]["model"]["reasoning_effort"] == "xhigh"
        return {"rules": [], "initial": []}

    monkeypatch.setattr(engine.providers, "generate", generate)
    assert await engine.checked_ask(
        run,
        "rules",
        "counter_repair",
        {},
        Numbering,
        [],
        lambda x: validate_numbering(x, "article"),
    ) == {"rules": [], "initial": []}
    assert len(calls) == 2

    for code in ("RESULT_UNKNOWN", "AUTH_REQUIRED", "RATE_LIMITED", "PAUSED"):
        calls.clear()

        async def fail(*args, **kwargs):
            calls.append(code)
            raise WorkflowError(code, code)

        monkeypatch.setattr(engine.providers, "generate", fail)
        with pytest.raises(WorkflowError, match=code):
            await engine.checked_ask(
                run, code, "convert", {"case": code}, Conversion, [], lambda x: None
            )
        assert calls == [code]


@pytest.mark.asyncio
async def test_final_repair_checkpoint_survives_pause(app, monkeypatch):
    engine = app.state.engine
    run = make_run(app)
    setup, results, headings = example()
    wrong = dict(setup, numbering={"rules": [], "initial": []})
    base = engine.store.directory(run["id"])
    from bookanalyst.store import digest

    atomic_json(
        base / "compile-candidate.json",
        {
            "input_hash": digest(
                {"results": results, "headings": headings, "setup": wrong}
            ),
            "results": results,
            "headings": headings,
            "setup": setup,
        },
    )
    require_tex()

    async def forbidden(*args, **kwargs):
        pytest.fail("The restored fixed project compiles without an agent call")

    monkeypatch.setattr(engine.providers, "generate", forbidden)
    await engine.compile(run, results, headings, wrong)
    assert (
        json.loads((base / "finish-report.json").read_text())["accepted_by"] == "compiler"
    )




@pytest.mark.asyncio
async def test_compiled_formatted_numbers_do_not_trigger_quality_review(
    app, monkeypatch
):
    require_tex()
    engine = app.state.engine
    run = make_run(app)
    data = result([1]) | {"task_id": "batch-0001"}
    data["pages"][0]["tex"] = (
        r"\begin{equation*}a=b\tag{\ensuremath{1^{\prime}}}\label{equation:1′}\end{equation*}See \eqref{equation:1′}."
    )
    calls = []

    async def generate(*args, **kwargs):
        pytest.fail("Compiled formatted numbers need no separate acceptance")

    monkeypatch.setattr(engine.providers, "generate", generate)
    await engine.compile(run, [data], [], {"documentclass": "article"})
    base = engine.store.directory(run["id"])
    assert len(calls) == 0
    assert (
        json.loads((base / "finish-report.json").read_text())["accepted_by"] == "compiler"
    )
    assert not (base / "numbering-report.json").exists()
    await engine.compile(run, [data], [], {"documentclass": "article"})
    assert len(calls) == 0


