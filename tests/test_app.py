import asyncio
import json
import zipfile
import io

import pytest
from fastapi.testclient import TestClient
from bookanalyst.models import Operation, RunCreate

BOOK_ID="elliptic-pde-second-order"


def test_local_api_requires_session_token_and_rejects_foreign_origin(app):
    with TestClient(app) as client:
        assert client.get("/").status_code==200
        assert client.post("/api/runs",json={}).status_code==403
        token=client.get("/api/bootstrap").json()["token"]
        response=client.put("/api/settings",json={},headers={
            "x-bookanalyst-token":token,"origin":"https://unrelated.example"})
        assert response.status_code==403
        assert client.get("/api/bootstrap",headers={"host":"unrelated.example"}).status_code==400


def test_test_book_and_profile_limits_are_real(app):
    with TestClient(app) as client:
        state=client.get("/api/bootstrap").json()
        assert state["books"][0]["page_count"]==532
        headers={"x-bookanalyst-token":state["token"]}
        response=client.post("/api/runs",headers=headers,json={
            "book_id":BOOK_ID,"start_page":14,"end_page":18,"profile":"cloud_smoke"})
        assert response.status_code==422
        response=client.post("/api/runs",headers=headers,json={
            "book_id":BOOK_ID,"start_page":14,"end_page":14,"profile":"cloud_smoke",
            "max_llm_requests":100,"max_parse_submissions":100})
        assert response.status_code==200
        config=response.json()["config"]
        assert config["max_llm_requests"]==0 and config["max_parse_submissions"]==1
        assert config["visual_mode"]=="disabled"


def test_original_image_is_rendered_and_invalid_page_rejected(app):
    with TestClient(app) as client:
        response=client.get(f"/api/books/{BOOK_ID}/image?page_idx=13&dpi=72")
        assert response.status_code==200 and response.content.startswith(b"\x89PNG")
        assert client.get(f"/api/books/{BOOK_ID}/image?page_idx=9999").status_code==422


@pytest.mark.asyncio
async def test_offline_fixture_runs_real_gates_and_missing_compiler_never_accepts(app,monkeypatch):
    import bookanalyst.tex
    monkeypatch.setattr(bookanalyst.tex.shutil,"which",lambda _:None)
    async def forbidden(*args,**kwargs):
        raise AssertionError("Fixture attempted a live service")
    engine=app.state.engine
    monkeypatch.setattr(engine.providers,"generate",forbidden)
    monkeypatch.setattr(engine.mineru,"parse",forbidden)
    store=app.state.store
    config=RunCreate(book_id=BOOK_ID,profile="offline_fixture",start_page=14,end_page=14,
                     visual_mode="sampled",visual_pages=[14],max_llm_requests=0,
                     max_parse_submissions=0,max_submitted_pages=0).model_dump()
    run=store.create_run(config,store.get("book",BOOK_ID),store.get("settings","main")["mineru"])
    engine.start(run["id"],Operation(revision=1,operation_id="offline-start-1"))
    await engine.running[run["id"]]
    result=store.get("run",run["id"])
    assert result["state"]=="FAILED",result
    assert all(result["stages"][f"S{i}"]["state"]=="PASSED" for i in range(7)),result
    assert result["stages"]["S7"]["error"]["code"]=="DEPENDENCY_MISSING"
    assert result["stages"]["S8"]["state"]=="PENDING"
    assert result["usage"]=={"llm":0,"parse":0,"pages":0}
    visual=store.artifact(result,"S2","visual_review/index.json")
    assert visual["evidence_origin"]=="fixture" and visual["full_pages"]==[13]
    candidate=store.directory(run["id"],1,"S7")/"candidate/tex/body.tex"
    assert "\\begin{equation}" in candidate.read_text()
    assert "\\newcommand" not in candidate.read_text()
    await engine.close()
