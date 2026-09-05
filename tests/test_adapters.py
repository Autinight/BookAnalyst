import asyncio
import copy
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from bookanalyst.llm import Providers
from bookanalyst.mineru import MinerU
from bookanalyst.models import RunCreate
from bookanalyst.normalize import normalize
from bookanalyst.semantics import REVIEW
from bookanalyst.store import WorkflowError

BOOK_ID="elliptic-pde-second-order"


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol",["chat_completions","responses"])
async def test_custom_channel_protocol_and_actual_image_transport(app,tmp_path,protocol):
    store=app.state.store
    settings=store.get("settings","main")
    settings["connections"]["custom_api"].update(enabled=True,auth_mode="none",base_url="http://model.test/v1",
        protocol=protocol,model_id="test-model",image_support="supported")
    store.put("settings","main",settings)
    config=RunCreate(book_id=BOOK_ID,start_page=14,end_page=14).model_dump()
    config["reviewer"].update(connection_id="custom_api",model_id="test-model")
    run=store.create_run(config,store.get("book",BOOK_ID),settings["mineru"])
    image=tmp_path/"page.png";image.write_bytes(b"image-evidence")
    report={"decision":"PASS","source_ids":["a"],"evidence":"visible evidence","findings":[]}
    requests=[]
    def respond(request):
        body=json.loads(request.content)
        requests.append(body)
        if protocol=="chat_completions":
            assert request.url.path=="/v1/chat/completions"
            assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(report)},"finish_reason":"stop"}]})
        assert request.url.path=="/v1/responses"
        assert body["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
        return httpx.Response(200,json={"status":"completed","output":[{"type":"message","content":[
            {"type":"output_text","text":json.dumps(report)}]}]})
    providers=Providers(store,app.state.workspace,transport=httpx.MockTransport(respond))
    assert await providers.generate(run,"reviewer","test","Check original image",REVIEW,[image])==report
    assert len(requests)==1
    assert store.get("run",run["id"])["usage"]["llm"]==1


@pytest.mark.asyncio
async def test_unknown_model_result_is_recorded_without_retry(app):
    store=app.state.store
    settings=store.get("settings","main")
    settings["connections"]["custom_api"].update(enabled=True,auth_mode="none",base_url="http://model.test/v1",
                                                  model_id="test-model")
    store.put("settings","main",settings)
    config=RunCreate(book_id=BOOK_ID,start_page=14,end_page=14).model_dump()
    config["reviewer"].update(connection_id="custom_api",model_id="test-model")
    run=store.create_run(config,store.get("book",BOOK_ID),settings["mineru"])
    calls=[]
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("server may already have processed")
    provider=Providers(store,app.state.workspace,httpx.MockTransport(timeout))
    with pytest.raises(WorkflowError,match="超时或中断"):
        await provider.generate(run,"reviewer","test","Check",REVIEW)
    assert len(calls)==1
    assert store.calls(run["id"])[0]["state"]=="RESULT_UNKNOWN"


def test_normalization_retains_discarded_mathematical_footnote(tmp_path):
    block={"type":"text","bbox":[0,80,50,100],"lines":[{"spans":[{"type":"text","content":"A mathematical footnote."}]}]}
    layout={"pdf_info":[{"page_idx":0,"page_size":[100,100],"para_blocks":[],"discarded_blocks":[block]}]}
    chunk={"id":"chunk-0","sha256":"sha","pages":[13]}
    atoms,mapping=normalize([(chunk,layout,tmp_path)],"source")
    assert len(atoms)==1 and atoms[0]["discarded_candidate"]
    assert atoms[0]["text"]=="A mathematical footnote."
    assert mapping[atoms[0]["atom_id"]]["page_idx"]==13


@pytest.mark.asyncio
async def test_cloud_mineru_sequence_does_not_forward_token_to_signed_urls(app,tmp_path,monkeypatch):
    monkeypatch.setenv("MINERU_API_TOKEN","test-only-token")
    store=app.state.store
    config=RunCreate(book_id=BOOK_ID,start_page=14,end_page=14).model_dump()
    run=store.create_run(config,store.get("book",BOOK_ID),store.get("settings","main")["mineru"])
    data=io.BytesIO()
    layout={"pdf_info":[{"page_idx":0,"page_size":[100,100],"para_blocks":[]}]}
    with zipfile.ZipFile(data,"w") as archive:
        archive.writestr("layout.json",json.dumps(layout))
    seen=[]
    def response(request):
        seen.append((request.method,request.url.path))
        if request.url.host=="mineru.net":
            assert request.headers["authorization"]=="Bearer test-only-token"
            if request.method=="POST":
                return httpx.Response(200,json={"code":0,"data":{"batch_id":"batch-1","file_urls":["https://files.test/upload"]}})
            return httpx.Response(200,json={"code":0,"data":{"extract_result":[{
                "data_id":"chunk-0","state":"done","full_zip_url":"https://files.test/result"}]}})
        assert "authorization" not in request.headers
        return httpx.Response(200,content=data.getvalue() if request.method=="GET" else b"")
    parser=MinerU(store,app.state.workspace,httpx.MockTransport(response))
    pdf=tmp_path/"sample.pdf";pdf.write_bytes(b"pdf transport fixture")
    result,_,_=await parser.parse(run,{"id":"chunk-0","pages":[13],"sha256":"sha"},pdf,tmp_path/"output")
    assert result==layout
    assert len(seen)==4
    assert store.get("run",run["id"])["usage"]=={"llm":0,"parse":1,"pages":1}
    assert store.calls(run["id"])[0]["state"]=="COMPLETED"


@pytest.mark.asyncio
async def test_cloud_resume_reconciles_expired_job_without_resubmission(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_API_TOKEN", "test-only-token")
    store = app.state.store
    settings = store.get("settings", "main")
    config = RunCreate(book_id=BOOK_ID, start_page=14, end_page=14).model_dump()
    run = store.create_run(config, store.get("book", BOOK_ID), settings["mineru"])
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"fixture")
    target = tmp_path / "output"
    chunk = {"id": "chunk-0", "pages": [13], "sha256": "sha"}
    archive_data = io.BytesIO()
    layout = {"pdf_info": [{"page_idx": 0, "page_size": [100, 100], "para_blocks": []}]}
    with zipfile.ZipFile(archive_data, "w") as archive:
        archive.writestr("layout.json", json.dumps(layout))
    seen = []
    fail_poll = True

    def respond(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "existing-batch", "file_urls": ["https://files.test/upload"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.host == "mineru.net":
            if fail_poll:
                raise httpx.ReadTimeout("a signed URL or credential must not be logged")
            return httpx.Response(200, json={"code": 0, "data": {"extract_result": [
                {"data_id": "chunk-0", "state": "done", "full_zip_url": "https://files.test/result"}]}})
        return httpx.Response(200, content=archive_data.getvalue())

    parser = MinerU(store, app.state.workspace, httpx.MockTransport(respond))
    with pytest.raises(WorkflowError) as failure:
        await parser.parse(run, chunk, pdf, target)
    assert failure.value.code == "RESULT_UNKNOWN"
    receipt_path = target / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["last_error"]["type"] == "ReadTimeout"
    assert receipt["last_error"]["phase"] == "poll"
    assert "credential" not in receipt_path.read_text(encoding="utf-8")
    receipt["started_at"] = 0  # original deadline expired while the app was offline
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    fail_poll = False
    result, _, _ = await parser.parse(run, chunk, pdf, target)
    assert result == layout
    assert [method for method, _ in seen] == ["POST", "PUT", "GET", "GET", "GET"]
    assert store.get("run", run["id"])["usage"] == {"llm": 0, "parse": 1, "pages": 1}
    assert store.calls(run["id"])[0]["state"] == "COMPLETED"
    await parser.parse(run, chunk, pdf, target)
    assert len(seen) == 5  # completed local cache makes no network request
    (target / "result.zip").write_bytes(b"corrupt")
    with pytest.raises(WorkflowError) as failure:
        await parser.parse(run, chunk, pdf, target)
    assert failure.value.code == "CACHE_INTEGRITY"
    assert len(seen) == 5


@pytest.mark.asyncio
async def test_completed_remote_parse_download_failure_is_not_unknown_submission(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_API_TOKEN", "test-only-token")
    store = app.state.store
    config = RunCreate(book_id=BOOK_ID, start_page=14, end_page=14).model_dump()
    run = store.create_run(config, store.get("book", BOOK_ID), store.get("settings", "main")["mineru"])
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"fixture")
    seen = []
    def respond(request):
        seen.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "batch_id": "existing-batch", "file_urls": ["https://files.test/upload"]}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.host == "mineru.net":
            return httpx.Response(200, json={"code": 0, "data": {"extract_result": [
                {"data_id": "chunk-0", "state": "done", "full_zip_url": "https://files.test/result"}]}})
        raise httpx.ConnectError("TLS connection failed")
    parser = MinerU(store, app.state.workspace, httpx.MockTransport(respond))
    with pytest.raises(WorkflowError) as failure:
        await parser.parse(run, {"id": "chunk-0", "pages": [13], "sha256": "sha"}, pdf, tmp_path / "output")
    assert failure.value.code == "RESULT_DOWNLOAD_FAILED"
    assert failure.value.review
    assert store.calls(run["id"])[0]["state"] == "DOWNLOAD_FAILED"
    receipt = json.loads((tmp_path / "output/receipt.json").read_text(encoding="utf-8"))
    assert receipt["remote_state"] == "COMPLETED"
    assert receipt["remote_id"] == "existing-batch"
    assert seen == ["POST", "PUT", "GET", "GET"]

@pytest.mark.asyncio
async def test_subscription_uses_upstream_models_default_and_modalities(app, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    provider = Providers(app.state.store, app.state.workspace)
    # Deliberately unknown names ensure no fixed allowlist influences discovery.
    models = [{"id": "future-model", "model": "future-model", "isDefault": True, "inputModalities": ["text", "image"]},
              {"id": "text-model", "model": "text-model", "isDefault": False, "inputModalities": ["text"]}]
    sdk = SimpleNamespace(
        account=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **_: {"account": {"type": "chatgpt"}})),
        models=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **_: {"data": models})))
    monkeypatch.setattr(provider, "codex", AsyncMock(return_value=sdk))
    binding = RunCreate(book_id=BOOK_ID).model_dump()["converter"]
    state = await provider.status("openai_subscription")
    assert state["models"] == models
    resolved, _ = await provider.resolve(binding, require_image=True)
    assert resolved["model_id"] == "future-model"
    with pytest.raises(WorkflowError) as error:
        await provider.resolve(binding | {"model_id": "text-model"}, require_image=True)
    assert error.value.code == "CAPABILITY_UNSUPPORTED"
    with pytest.raises(WorkflowError) as error:
        await provider.resolve(binding | {"model_id": "invented-model"})
    assert error.value.code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize("configured", [None, "configured-codex"])
def test_official_runtime_prefers_explicit_then_path(monkeypatch, configured):
    from types import SimpleNamespace
    from bookanalyst.llm import discover_runtime
    if configured:
        monkeypatch.setenv("BOOKANALYST_CODEX_BIN", configured)
    else:
        monkeypatch.delenv("BOOKANALYST_CODEX_BIN", raising=False)
    monkeypatch.setattr("bookanalyst.llm.shutil.which", lambda _: "path-codex")
    launched = []
    def launch(args, **kwargs):
        launched.append(args)
        return SimpleNamespace(stdout="codex-cli current")
    monkeypatch.setattr("bookanalyst.llm.subprocess.run", launch)
    actual = discover_runtime()
    assert actual == {"path": configured or "path-codex", "source": "configured" if configured else "PATH",
                      "version": "codex-cli current"}
    assert launched == [[configured or "path-codex", "--version"]]


def test_invalid_explicit_runtime_does_not_silently_use_old_bundle(monkeypatch):
    from bookanalyst.llm import discover_runtime
    monkeypatch.setenv("BOOKANALYST_CODEX_BIN", "missing-codex")
    def unavailable(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr("bookanalyst.llm.subprocess.run", unavailable)
    with pytest.raises(WorkflowError) as error:
        discover_runtime()
    assert error.value.code == "RUNTIME_UNAVAILABLE"

@pytest.mark.asyncio
async def test_cdn_dns_fallback_preserves_https_host_and_does_not_forward_auth(app, monkeypatch):
    monkeypatch.setenv("MINERU_DOWNLOAD_PUBLIC_DNS", "1")
    seen = []
    host = "cdn-mineru.openxlab.org.cn"
    def respond(request):
        seen.append(request)
        assert "authorization" not in request.headers
        if request.url.host == host:
            raise httpx.ConnectError("Synthetic proxy DNS failure")
        if request.url.host == "dns.google":
            assert dict(request.url.params) == {"name": host, "type": "A"}
            return httpx.Response(200, json={"Answer": [{"type": 1, "data": "127.0.0.1"},
                                                       {"type": 1, "data": "47.251.51.149"}]})
        assert request.url.host == "47.251.51.149" and request.url.scheme == "https"
        assert request.headers["host"] == host and request.extensions["sni_hostname"] == host
        assert request.url.params["signature"] == "test-only"
        return httpx.Response(200, content=b"result")
    transport = httpx.MockTransport(respond)
    parser = MinerU(app.state.store, app.state.workspace, transport)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await parser.download(client, f"https://{host}/result.zip?signature=test-only")
    assert response.content == b"result" and len(seen) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,host", [("0", "cdn-mineru.openxlab.org.cn"), ("1", "another-service.test")])
async def test_cdn_dns_fallback_requires_opt_in_and_exact_service(app, monkeypatch, enabled, host):
    monkeypatch.setenv("MINERU_DOWNLOAD_PUBLIC_DNS", enabled)
    seen = []
    def respond(request):
        seen.append(request)
        raise httpx.ConnectError("Connection unavailable")
    transport = httpx.MockTransport(respond)
    parser = MinerU(app.state.store, app.state.workspace, transport)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await parser.download(client, f"https://{host}/result.zip")
    assert len(seen) == 1

def test_real_mineru_bare_resource_name_resolves_images_directory(tmp_path):
    from bookanalyst.normalize import resolve_asset
    image = tmp_path / "images/formula.jpg"
    image.parent.mkdir()
    image.write_bytes(b"official ZIP image resource")
    assert resolve_asset(tmp_path, "formula.jpg") == image.resolve()
    assert resolve_asset(tmp_path, "images/formula.jpg") == image.resolve()
    (tmp_path / "formula.jpg").write_bytes(b"conflicting resource")
    with pytest.raises(WorkflowError):
        resolve_asset(tmp_path, "formula.jpg")
    with pytest.raises(WorkflowError):
        resolve_asset(tmp_path, "../outside.jpg")
