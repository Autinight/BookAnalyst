"""Global bibliography sampling and shared rules, without reprocessing finished setup."""

import json

import pytest

from test_rebuild import make_run, result


def setup(**changes):
    return dict(documentclass="article", title="Book", author="", rules="Draft rules",
                toc=[], numbering={"rules": [], "initial": []}, public_tex="", read_pages=[]) | changes


@pytest.mark.asyncio
async def test_global_reads_bibliography_and_shares_its_rule_with_conversion(app, monkeypatch):
    engine, run = app.state.engine, make_run(app, setup_pages=[2, 4])
    count = run["source"]["page_count"]
    requests = []
    rule = r"Use the printed bibliography code as the key. Example: \ref{bibliography:AB7} / \item\label{bibliography:AB7}."

    async def no_image(*args):
        return None

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        requests.append((purpose, payload))
        if purpose == "convert":
            assert payload["rules"] == rule
            return result(payload["owned_pages"])
        if len(requests) == 1:
            assert payload["page_images"] == sorted({2, 4, count - 2, count - 1, count})
            return setup(read_pages=[8, 9], rules="Tail pages are an index; inspect bibliography and citation samples.")
        assert len(requests) == 2
        assert payload["page_images"] == [8, 9]
        assert payload["previous_setup"]["rules"].startswith("Tail pages are an index")
        assert {2, 4, 8, 9, count} <= set(payload["inspected_pages"])
        return setup(rules=rule)

    monkeypatch.setattr(engine, "image", no_image)
    monkeypatch.setattr(engine.providers, "generate", generate)
    final = await engine.global_setup(run)
    assert final["rules"] == rule and "read_pages" not in final
    # Restarting setup traverses saved responses, without paying for the same reads again.
    assert await engine.global_setup(run) == final
    assert len(requests) == 2
    await engine.convert(run, engine.store.tasks(run["id"], "convert")[0], final)
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [[0], [999999], list(range(1, 10))])
async def test_global_rejects_invalid_read_requests_before_reading(app, monkeypatch, requested):
    engine, run = app.state.engine, make_run(app)
    calls = []

    async def no_image(*args):
        return None

    async def generate(run, role, purpose, prompt, schema, images=()):
        payload = json.loads(prompt)
        calls.append(payload)
        if len(calls) == 1:
            return setup(read_pages=requested)
        assert payload["repair_feedback"]["code"] == "PAGE_SCOPE"
        assert payload["page_images"] == calls[0]["page_images"]
        return setup()

    monkeypatch.setattr(engine, "image", no_image)
    monkeypatch.setattr(engine.providers, "generate", generate)
    assert "read_pages" not in await engine.global_setup(run)
    assert len(calls) == 2
