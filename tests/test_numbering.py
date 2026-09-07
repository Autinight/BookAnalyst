"""Native counters and cross-batch references, verified with actual XeLaTeX."""

import shutil
import pytest
from bookanalyst.documents import (
    namespace,
    validate_conversion,
    apply_seam,
    render_document,
    Conversion,
)
from bookanalyst.numbering import (
    Numbering,
    collect_symbols,
    symbol_problems,
    apply_symbol_edits,
    counter_preamble,
    validate_numbering,
    numbering_report,
)
from bookanalyst.models import RunCreate, STAGES
from bookanalyst.store import WorkflowError
from bookanalyst.tex import compile_tex


def rule(name, reset_by="", shared_with="", prefix_parent=False):
    return dict(
        name=name,
        reset_by=reset_by,
        shared_with=shared_with,
        style="arabic",
        prefix_parent=prefix_parent,
    )


def batch(page, tex, headings=()):
    data = dict(
        pages=[dict(page=page, tex=tex)],
        headings=list(headings),
        assets=[],
        head="closed",
        tail="closed",
    )
    validate_conversion(data, [page])
    return namespace(data, f"b{page}") | {"task_id": f"b{page}"}


def heading(id, page, number):
    return dict(id=id, page=page, level="section", number=number, title="Section title")


def example():
    results = [
        batch(
            1,
            r"\BAHeading{h}\label{section:1}\begin{theorem}\label{theorem:1.1}Statement.\end{theorem} See \eqref{equation:1.1}.",
            [heading("h", 1, "1")],
        ),
        batch(
            2,
            r"\begin{lemma}\label{lemma:1.2}Statement.\end{lemma}\begin{equation}a=b\label{equation:1.1}\end{equation}",
        ),
        batch(
            3,
            r"\BAHeading{h}\label{section:2}\begin{theorem}\label{theorem:2.1}Statement.\end{theorem}",
            [heading("h", 3, "2")],
        ),
    ]
    heads = [h for r in results for h in r["headings"]]
    setup = dict(
        documentclass="article",
        numbering=dict(
            rules=[
                rule("theorem", "section", prefix_parent=True),
                rule("equation", "section", prefix_parent=True),
            ],
            initial=[],
        ),
    )
    return setup, results, heads


@pytest.mark.asyncio
async def test_native_labels_share_counters_reset_and_resolve_forward_refs(tmp_path):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    setup, results, heads = example()
    resolved, index = apply_symbol_edits(
        results, heads, dict(label_edits=[], reference_edits=[])
    )
    files, _ = render_document(setup, resolved, heads)
    assert r"\eqref{equation:1.1}" in files["body.tex"]
    assert r"\label{lemma:1.2}" in files["body.tex"]
    assert r"\section{Section title}" in files["headings.tex"]
    assert r"\tag" not in files["body.tex"] and r"\setcounter" not in files["body.tex"]
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED", report
    numbers = numbering_report((tmp_path / "main.aux").read_text(), index)
    assert not numbers["mismatches"], numbers
    actual = {t["label"]: t["rendered"] for t in numbers["targets"]}
    assert (
        actual["theorem:1.1"] == "1.1"
        and actual["lemma:1.2"] == "1.2"
        and actual["theorem:2.1"] == "2.1"
    )
    # Inserting a theorem changes the rendered counter without changing a label key.
    path = tmp_path / "body.tex"
    path.write_text(
        path.read_text().replace(
            r"\begin{lemma}", r"\begin{theorem}Inserted.\end{theorem}\begin{lemma}"
        )
    )
    assert (await compile_tex(tmp_path))["status"] == "PASSED"
    numbers = numbering_report((tmp_path / "main.aux").read_text(), index)
    assert (
        next(t["rendered"] for t in numbers["targets"] if t["label"] == "lemma:1.2")
        == "1.3"
    )


@pytest.mark.parametrize(
    "tex", [r"\tag{1.1}", r"\setcounter{theorem}{1}", r"\BATarget{t}", r"\BARef{r}"]
)
def test_workers_cannot_override_counters_or_use_reference_sidecars(tex):
    with pytest.raises(WorkflowError, match="隐藏"):
        batch(1, tex)


def test_native_names_survive_batch_namespace_and_seam():
    _, results, heads = example()
    assert r"\BAHeading{b1-h}\label{section:1}" in results[0]["pages"][0]["tex"]
    assert r"\eqref{equation:1.1}" in results[0]["pages"][0]["tex"]
    with pytest.raises(WorkflowError):
        apply_seam(
            r"\label{lemma:1}",
            "text",
            dict(left_suffix=r"\label{lemma:1}", right_prefix="", replacement=""),
        )
    assert set(Conversion.model_fields) == {
        "pages",
        "headings",
        "assets",
        "head",
        "tail",
    }
    assert STAGES == ["setup", "style", "convert", "seams", "headings"]


def test_repeated_local_numbers_are_disambiguated_by_occurrence_and_scope():
    results = [
        batch(
            1,
            r"\BAHeading{h}\begin{lemma}\label{lemma:1}A.\end{lemma}",
            [heading("h", 1, "1")],
        ),
        batch(
            2,
            r"\BAHeading{h}\begin{lemma}\label{lemma:1}B.\end{lemma} Here \ref{lemma:1}; earlier \ref{lemma:1}.",
            [heading("h", 2, "2")],
        ),
    ]
    heads = [h for r in results for h in r["headings"]]
    index = collect_symbols(results, heads)
    assert symbol_problems(index)["duplicate_labels"] == ["lemma:1"]
    assert index["targets"][1]["scope"][-1]["number"] == "2"
    edits = dict(
        label_edits=[
            dict(id="p1-label-0", key="lemma:1:section-1"),
            dict(id="p2-label-0", key="lemma:1:section-2"),
        ],
        reference_edits=[
            dict(id="p2-reference-0", key="lemma:1:section-2"),
            dict(id="p2-reference-1", key="lemma:1:section-1"),
        ],
    )
    fixed, updated = apply_symbol_edits(results, heads, edits)
    assert (
        r"Here \ref{lemma:1:section-2}; earlier \ref{lemma:1:section-1}"
        in fixed[1]["pages"][0]["tex"]
    )
    assert not symbol_problems(updated)["unresolved_references"]
    with pytest.raises(WorkflowError):
        apply_symbol_edits(results, heads, dict(label_edits=[], reference_edits=[]))
    edits["label_edits"][0]["key"] = "lemma:2:section-1"
    with pytest.raises(WorkflowError, match="原书"):
        apply_symbol_edits(results, heads, edits)


def test_index_ignores_comments_and_retains_exact_edit_offsets():
    results = [
        batch(
            1,
            "% ignore \\label{lemma:999}\n"
            + r"\begin{lemma}\label{lemma:1}A\end{lemma} \ref{lemma:2}",
        )
    ]
    index = collect_symbols(results, [])
    assert [t["key"] for t in index["targets"]] == ["lemma:1"]
    fixed, _ = apply_symbol_edits(
        results,
        [],
        dict(
            label_edits=[], reference_edits=[dict(id="p1-reference-0", key="lemma:1")]
        ),
    )
    assert fixed[0]["pages"][0]["tex"].endswith(r"\ref{lemma:1}")


def test_counter_rules_reject_explicit_and_default_cycles():
    for rules in [
        [rule("theorem", shared_with="lemma")],
        [rule("section", "subsection"), rule("subsection", "section")],
    ]:
        with pytest.raises(WorkflowError):
            validate_numbering(dict(rules=rules, initial=[]), "article")
    assert r"\newtheorem{lemma}[theorem]" in counter_preamble(
        dict(rules=[rule("theorem", "section", prefix_parent=True)], initial=[]),
        "article",
    )


@pytest.mark.asyncio
async def test_structure_roles_dispatch_xhigh_and_do_not_mutate_conversion_binding(
    app, monkeypatch
):
    engine = app.state.engine
    store = app.state.store
    run = store.create_run(
        RunCreate(
            book_id="wang-2022-g-invariant-min-max",
            end_page=3,
            model={"model_id": "test-model", "reasoning_effort": "medium"},
        ).model_dump(),
        store.get("book", "wang-2022-g-invariant-min-max"),
    )
    seen = []

    async def generate(run, role, purpose, prompt, schema, images=()):
        seen.append((purpose, run["config"][role]["reasoning_effort"]))
        return {"rules": [], "initial": []}

    monkeypatch.setattr(engine.providers, "generate", generate)
    for purpose in [
        "setup",
        "convert",
        "seams",
        "headings",
        "counter_repair",
    ]:
        await engine.ask(run, purpose, purpose, {"purpose": purpose}, Numbering, [])
    assert dict(seen) == {
        "setup": "xhigh",
        "convert": "medium",
        "seams": "medium",
        "headings": "xhigh",
        "counter_repair": "xhigh",
    }
    assert run["config"]["model"]["reasoning_effort"] == "medium"


def test_completed_v6_export_remains_available_after_upgrade(app):
    store = app.state.store
    book = store.get("book", "wang-2022-g-invariant-min-max")
    run = store.create_run(RunCreate(book_id=book["id"], end_page=3).model_dump(), book)
    run.update(workflow_version="0.6", state="COMPLETED")
    store.put("run", run["id"], run)
    assert store.summary(run)["legacy"] and store.directory(run["id"]).name == "v6"


def test_seam_cannot_bypass_native_counter_rules():
    with pytest.raises(WorkflowError, match="计数器"):
        apply_seam(
            "left",
            "right",
            dict(
                left_suffix="left",
                right_prefix="",
                replacement=r"\setcounter{theorem}{5}left",
            ),
        )


@pytest.mark.asyncio
async def test_invalid_setup_rule_can_be_retried_without_reusing_invalid_cache(
    app, monkeypatch
):
    import json

    engine = app.state.engine
    store = app.state.store
    book = store.get("book", "wang-2022-g-invariant-min-max")
    run = store.create_run(RunCreate(book_id=book["id"], end_page=1).model_dump(), book)
    seen = []

    async def resolve(binding, require_image=False):
        return binding | {"model_id": "test-model"}, {}

    async def generate(run, role, purpose, prompt, schema, images=()):
        if purpose == "setup":
            seen.append(json.loads(prompt))
            rules = [rule("theorem", shared_with="lemma")] if len(seen) == 1 else []
            return dict(
                documentclass="article",
                title="",
                author="",
                rules="",
                toc=[],
                numbering=dict(rules=rules, initial=[]),
            )
        return dict(
            pages=[dict(page=1, tex="Content.")],
            headings=[],
            assets=[],
            head="closed",
            tail="closed",
        )

    async def compile(*args):
        pass

    monkeypatch.setattr(engine.providers, "resolve", resolve)
    monkeypatch.setattr(engine.providers, "generate", generate)
    monkeypatch.setattr(engine, "compile", compile)
    store.change(run["id"], lambda r: r.update(state="RUNNING"))
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["error"]["code"] == "COUNTER_RULE"
    store.change(
        run["id"], lambda r: r.update(state="RUNNING", revision=r["revision"] + 1)
    )
    await engine.execute(run["id"])
    assert store.get("run", run["id"])["state"] == "COMPLETED"
    assert len(seen) == 2 and seen[1]["retry"]["error"]


@pytest.mark.asyncio
async def test_structure_selected_appendix_switch_uses_native_counters(tmp_path):
    if not shutil.which("xelatex"):
        pytest.skip("Requires installed XeLaTeX")
    setup, results, heads = example()
    results.append(
        batch(
            4,
            r"\BAHeading{a}\label{section:A}\begin{equation}x=1\label{equation:A.1}\end{equation}",
            [heading("a", 4, "A")],
        )
    )
    results.append(
        batch(
            5,
            r"\BAHeading{b}\label{section:B}\begin{lemma}\label{lemma:B.1}Statement.\end{lemma}",
            [heading("b", 5, "B")],
        )
    )
    heads = [h for r in results for h in r["headings"]]
    setup["appendix_start"] = "b4-a"
    files, _ = render_document(setup, results, heads)
    assert files["headings.tex"].count(r"\appendix") == 1
    assert (
        r"\setcounter" not in files["body.tex"]
        and r"\appendix" not in files["body.tex"]
    )
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    report = await compile_tex(tmp_path)
    assert report["status"] == "PASSED", report
    numbers = numbering_report(
        (tmp_path / "main.aux").read_text(), collect_symbols(results, heads)
    )
    assert not numbers["mismatches"], numbers
