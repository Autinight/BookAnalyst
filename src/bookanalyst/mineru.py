"""MinerU cloud and local adapters, with durable remote IDs and no blind resubmission."""
import asyncio
import io
import ipaddress
import json
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx
from .config import secret
from .store import WorkflowError, atomic_json, file_hash


def unpack_result(data, target):
    target = Path(target)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
        if len(entries) > 20000 or sum(i.file_size for i in entries) > 1_000_000_000:
            raise WorkflowError("INVALID_ARCHIVE", "解析压缩包超出资源上限")
        for entry in entries:
            path = PurePosixPath(entry.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or any(":" in p for p in path.parts):
                raise WorkflowError("INVALID_ARCHIVE", "解析压缩包包含越界路径")
            if entry.is_dir():
                continue
            out = target.joinpath(*path.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(archive.read(entry))
    except zipfile.BadZipFile:
        raise WorkflowError("INVALID_ARCHIVE", "MinerU 未返回有效 ZIP 结果") from None
    layouts = sorted(p for p in target.rglob("*.json")
                     if p.name in ("layout.json", "middle.json") or p.name.endswith("_middle.json"))
    if len(layouts) != 1:
        raise WorkflowError("MISSING_LAYOUT", "结果必须包含唯一的 MinerU 完整布局 JSON")
    layout = json.loads(layouts[0].read_text(encoding="utf-8"))
    if not isinstance(layout.get("pdf_info"), list):
        raise WorkflowError("INVALID_LAYOUT", "布局 JSON 缺少 pdf_info")
    return layout, layouts[0].relative_to(target).as_posix()


class MinerU:
    def __init__(self, store, workspace, transport=None):
        self.store, self.workspace, self.transport = store, workspace, transport

    async def download(self, client, url):
        try:
            return await client.get(url)
        except httpx.ConnectError:
            host = urlparse(url).hostname
            # Opt-in workaround for local proxy fake-DNS failures. Keep HTTPS host verification.
            if (secret(self.workspace, "MINERU_DOWNLOAD_PUBLIC_DNS") != "1"
                    or host != "cdn-mineru.openxlab.org.cn"):
                raise
        answer = await client.get("https://dns.google/resolve", params={"name": host, "type": "A"})
        self._http(answer)
        addresses = []
        for row in answer.json().get("Answer", []):
            if row.get("type") == 1:
                try:
                    address = ipaddress.ip_address(row["data"])
                except ValueError:
                    continue
                if address.version == 4 and address.is_global:
                    addresses.append(str(address))
        if not addresses:
            raise httpx.ConnectError("No public CDN address")
        async with httpx.AsyncClient(timeout=60, trust_env=False, transport=self.transport,
                                     follow_redirects=False) as direct:
            for address in addresses[:4]:
                try:
                    return await direct.get(httpx.URL(url).copy_with(host=address),
                        headers={"Host": host}, extensions={"sni_hostname": host})
                except httpx.ConnectError:
                    continue
        raise httpx.ConnectError("CDN connection unavailable")

    async def parse(self, run, chunk, path, target):
        settings = run["parser_settings"]
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        receipt_path = target / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
        strategy = run["config"]["parser"]
        headers = {}
        if strategy == "cloud":
            token = secret(self.workspace, settings["api_key_env"])
            if not token:
                raise WorkflowError("AUTH_REQUIRED", "MinerU API 凭据尚未配置", 422)
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=60, transport=self.transport, follow_redirects=False) as client:
            try:
                if receipt and receipt["state"] == "COMPLETED":
                    cached_zip = target / "result.zip"
                    if not cached_zip.is_file() or (receipt.get("result_sha256") and
                            file_hash(cached_zip) != receipt["result_sha256"]):
                        raise WorkflowError("CACHE_INTEGRITY", "已完成的解析缓存缺失或哈希不符")
                    layout, layout_path = unpack_result(cached_zip.read_bytes(), target / "result")
                    if len(layout["pdf_info"]) != len(chunk["pages"]):
                        raise WorkflowError("PAGE_COVERAGE", "解析缓存页数与范围不同")
                    return layout, target / "result", layout_path
                if receipt and receipt["state"] in ("REJECTED", "FAILED"):
                    raise WorkflowError(receipt.get("error_code", "PARSE_FAILED"),
                                        "已有任务明确失败；恢复不会再次提交解析", review=True)
                if not receipt:
                    # Receipt is written before dispatch: a crash can never justify blindly submitting again.
                    call_id = self.store.reserve(run["id"], run["revision"], "parse", len(chunk["pages"]),
                                                 {"chunk_id": chunk["id"], "source_hash": chunk["sha256"]})
                    receipt = dict(call_id=call_id, state="SUBMITTING", started_at=time.time())
                    atomic_json(receipt_path, receipt)
                    if strategy == "cloud":
                        response = await client.post(settings["cloud_url"].rstrip("/") + "/file-urls/batch",
                            headers=headers, json={"files": [{"name": path.name, "data_id": chunk["id"]}],
                            "model_version": settings["model_version"], "language": settings["language"],
                            "enable_formula": True, "enable_table": True})
                        payload = self._cloud(response)
                        receipt.update(remote_id=payload["batch_id"], state="UPLOADING")
                        atomic_json(receipt_path, receipt)
                        url = payload["file_urls"][0]
                        self._external_url(url)
                        response = await client.put(url, content=path.read_bytes())
                        self._http(response)
                    else:
                        response = await client.post(settings["local_url"].rstrip("/") + "/tasks",
                            files={"files": (path.name, path.read_bytes(), "application/pdf")},
                            data={"backend": settings["backend"], "lang_list": settings["language"],
                                  "return_middle_json": "true", "return_content_list": "true",
                                  "return_images": "true", "response_format_zip": "true",
                                  "formula_enable": "true", "table_enable": "true"})
                        self._http(response)
                        payload = response.json()
                        receipt.update(remote_id=payload["task_id"])
                    receipt["state"] = "POLLING"
                    atomic_json(receipt_path, receipt)
                    self.store.finish_call(call_id, "SUBMITTED", remote_id=receipt["remote_id"])
                if not receipt.get("remote_id"):
                    raise WorkflowError("RESULT_UNKNOWN", "提交结果未知，需要核对服务端任务，不能重复提交", review=True)
                remote_id = receipt["remote_id"]
                if not all(c.isalnum() or c in "-_" for c in remote_id):
                    raise WorkflowError("INVALID_RESPONSE", "远端任务 ID 无效")
                for _ in range(settings["poll_max_requests"]):
                    # A resumed job gets one read even after its deadline; never resubmit it.
                    receipt["phase"] = "poll"
                    receipt["poll_count"] = receipt.get("poll_count", 0) + 1
                    atomic_json(receipt_path, receipt)
                    base = settings["cloud_url" if strategy == "cloud" else "local_url"].rstrip("/")
                    response = await client.get(base + (
                        f"/extract-results/batch/{remote_id}" if strategy == "cloud" else f"/tasks/{remote_id}"),
                        headers=headers)
                    if strategy == "cloud":
                        items = self._cloud(response)["extract_result"]
                        item = next((x for x in items if x.get("data_id") == chunk["id"]), None)
                        if item is None and len(items) == 1:
                            item = items[0]
                        state = item.get("state") if item else "pending"
                    else:
                        self._http(response)
                        item = response.json()
                        state = item.get("status")
                    if state in ("failed", "cancelled"):
                        raise WorkflowError("PARSE_FAILED", "MinerU 任务失败；请查看服务任务记录")
                    if state in ("done", "completed", "succeeded"):
                        url = item["full_zip_url"] if strategy == "cloud" else base + f"/tasks/{remote_id}/result"
                        if strategy == "cloud":
                            self._external_url(url)
                        receipt.update(phase="download", remote_state="COMPLETED")
                        atomic_json(receipt_path, receipt)
                        self.store.finish_call(receipt["call_id"], "REMOTE_COMPLETED", remote_id=remote_id)
                        response = await self.download(client, url)
                        self._http(response)
                        layout, layout_path = unpack_result(response.content, target / "result")
                        if len(layout["pdf_info"]) != len(chunk["pages"]):
                            raise WorkflowError("PAGE_COVERAGE", "MinerU 返回页数与提交范围不同")
                        (target / "result.zip").write_bytes(response.content)
                        receipt.update(state="COMPLETED", layout_path=layout_path,
                                       result_sha256=file_hash(target / "result.zip"))
                        receipt.pop("last_error", None)
                        atomic_json(receipt_path, receipt)
                        self.store.finish_call(receipt["call_id"], "COMPLETED", remote_id=remote_id)
                        return layout, target / "result", layout_path
                    if time.time() - receipt["started_at"] > settings["timeout_seconds"]:
                        raise WorkflowError("PARSE_TIMEOUT", "解析超时；已保存远端任务 ID", review=True)
                    if receipt["poll_count"] >= settings["poll_max_requests"]:
                        raise WorkflowError("POLL_LIMIT", "达到累计轮询上限；可稍后核对同一个任务", review=True)
                    await asyncio.sleep(settings["poll_interval_seconds"])
                raise WorkflowError("POLL_LIMIT", "达到轮询次数上限；已保存远端任务 ID", review=True)
            except httpx.HTTPError as exc:
                if receipt:
                    receipt["last_error"] = {"type": type(exc).__name__,
                                             "phase": receipt.get("phase", receipt["state"]),
                                             "time": time.time()}
                    atomic_json(receipt_path, receipt)
                    if receipt.get("phase") == "download":
                        self.store.finish_call(receipt["call_id"], "DOWNLOAD_FAILED",
                                               remote_id=receipt.get("remote_id"))
                        raise WorkflowError("RESULT_DOWNLOAD_FAILED",
                            "远端解析已完成，但结果下载连接失败；恢复只取回已有任务结果", review=True) from None
                    self.store.finish_call(receipt["call_id"], "RESULT_UNKNOWN",
                                           remote_id=receipt.get("remote_id"))
                raise WorkflowError("RESULT_UNKNOWN", "网络结果未知；恢复时先核对已有任务", review=True) from None
            except WorkflowError as exc:
                if receipt:
                    receipt["last_error"] = {"code": exc.code,
                                             "phase": receipt.get("phase", receipt["state"]),
                                             "time": time.time()}
                    if exc.code == "PARSE_FAILED" or (exc.code == "AUTH_REQUIRED" and not receipt.get("remote_id")):
                        receipt.update(state="FAILED" if receipt.get("remote_id") else "REJECTED",
                                       error_code=exc.code)
                        self.store.finish_call(receipt["call_id"], "FAILED",
                                               remote_id=receipt.get("remote_id"))
                    atomic_json(receipt_path, receipt)
                raise

    @staticmethod
    def _http(response):
        if response.status_code in (401, 403):
            raise WorkflowError("AUTH_REQUIRED", "解析服务鉴权失败", 422)
        if not response.is_success:
            raise WorkflowError("SERVICE_ERROR", f"解析服务返回 HTTP {response.status_code}")

    def _cloud(self, response):
        self._http(response)
        payload = response.json()
        if payload.get("code") != 0:
            raise WorkflowError("SERVICE_ERROR", f"MinerU 请求失败（服务错误码 {payload.get('code')}）")
        return payload["data"]

    @staticmethod
    def _external_url(url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise WorkflowError("INVALID_RESPONSE", "MinerU 资源地址必须使用 HTTPS")
