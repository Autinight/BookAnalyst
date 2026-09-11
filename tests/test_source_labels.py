"""Source labels must preserve legal author identifiers without repeated conversion."""

import shutil
import pytest
from bookanalyst.documents import validate_conversion, namespace, render_document
from bookanalyst.engine import REPAIR_ATTEMPTS_PER_ROUND
from bookanalyst.numbering import LABEL_NAME, collect_symbols, symbol_problems
from bookanalyst.store import WorkflowError
from bookanalyst.tex import compile_tex
from test_rebuild import make_run, result


@pytest.mark.parametrize(
    "key", ["equation:4'", "equation:4′", "equation:α.2", "lemma:A(iii):chapter-第二章"]
)
def test_source_identifiers_survive_validation_and_indexing(key):
    candidate = result([1])
    candidate["pages"][0]["tex"] = "\\label{" + key + "} See \\ref{" + key + "}."
    validate_conversion(candidate, [1])
    index = collect_symbols([namespace(candidate, "batch-0001")], [])
    assert index["targets"][0]["key"] == key
    assert not symbol_problems(index)["unresolved_references"]


@pytest.mark.parametrize(
    "key",
    [
        "equation:bad key",
        r"equation:4\prime",
        "equation:4%comment",
        "equation:{4}",
        "equation:4#1",
    ],
)
def test_source_keys_still_exclude_tex_syntax(key):
    assert not LABEL_NAME.fullmatch(key)


@pytest.mark.asyncio
async def test_unicode_source_key_works_with_actual_native_label_and_ref(tmp_path):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    candidate = result([1]) | {"task_id": "batch-0001"}
    candidate["pages"][0]["tex"] = (
        r"\section{Source}\label{section:1′}See Section~\ref{section:1′}."
    )
    files, _ = render_document({"documentclass": "article"}, [candidate], [])
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED", report
    # The identifier remains unchanged while ref obtains the target's native counter.
    assert r"\newlabel{section:1′}{{1}" in (tmp_path / "main.aux").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_invalid_batch_is_not_passed_and_validator_fix_reuses_saved_response(
    app, monkeypatch
):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    task = store.tasks(run["id"], "convert")[0]
    calls = []
    candidate = result(task["pages"])
    candidate["pages"][0]["tex"] = r"\label{equation:4′}See \eqref{equation:4′}."

    async def generate(*args, **kwargs):
        calls.append(1)
        return candidate

    def old_validator(value, pages):
        current = next(t for t in store.tasks(run["id"]) if t["id"] == task["id"])
        assert current["state"] != "PASSED"
        raise WorkflowError(
            "LABEL_NAME", "Old ASCII-only validator rejected source identifier"
        )

    monkeypatch.setattr(engine.providers, "generate", generate)
    monkeypatch.setattr("bookanalyst.engine.validate_conversion", old_validator)
    with pytest.raises(WorkflowError, match=f"{REPAIR_ATTEMPTS_PER_ROUND} 次自动修复上限"):
        await engine.convert(run, task, {"rules": ""})
    current = next(t for t in store.tasks(run["id"]) if t["id"] == task["id"])
    assert current["state"] == "NEEDS_REVIEW"
    path = store.directory(run["id"]) / "batches" / (task["id"] + ".json")
    assert not path.exists() and len(calls) == REPAIR_ATTEMPTS_PER_ROUND + 1
    monkeypatch.setattr("bookanalyst.engine.validate_conversion", validate_conversion)
    fixed = await engine.convert(run, task, {"rules": ""})
    assert fixed["pages"][0]["tex"] == candidate["pages"][0]["tex"]
    assert path.exists() and len(calls) == REPAIR_ATTEMPTS_PER_ROUND + 1
    assert (
        next(t for t in store.tasks(run["id"]) if t["id"] == task["id"])["state"]
        == "PASSED"
    )
