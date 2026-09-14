"""Grok SuperGrok / X Premium+ OAuth using the public Grok CLI client."""

import asyncio
import base64
import hashlib
import html
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode, urlparse

from .store import WorkflowError

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
API_BASE = "https://api.x.ai/v1"
OAUTH_HOST = "127.0.0.1"
OAUTH_PORT = 56121
REDIRECT_URI = f"http://{OAUTH_HOST}:{OAUTH_PORT}/callback"
REFRESH_SKEW = 120
LOGIN_TIMEOUT = 300
FORM_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "BookAnalyst",
}

SUCCESS_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>BookAnalyst · Grok 登录</title>
<style>
body{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#f4f1ea;color:#1b1b1b}
.c{text-align:center;padding:2rem}p{color:#5c5854}
</style></head>
<body><div class="c"><h1>Grok 登录成功</h1>
<p>可以关闭此页，返回 BookAnalyst 后点击检查连接。</p></div>
<script>setTimeout(()=>window.close(),1500)</script></body></html>"""

ERROR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>BookAnalyst · Grok 登录失败</title>
<style>
body{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#f4f1ea;color:#1b1b1b}
.c{text-align:center;padding:2rem}h1{color:#8b1e1e}code{display:block;margin-top:1rem;color:#5c5854}
</style></head>
<body><div class="c"><h1>Grok 登录失败</h1><code>__MESSAGE__</code></div></body></html>"""


def error_page(message):
    return ERROR_HTML.replace("__MESSAGE__", html.escape(str(message)[:400]))


def generate_pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(challenge, state, nonce):
    query = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "nonce": nonce,
            "plan": "generic",
            "referrer": "bookanalyst",
        },
        quote_via=quote,
    )
    return f"{AUTHORIZE_URL}?{query}"


def credentials_from_tokens(payload, fallback_refresh=""):
    access = payload.get("access_token")
    if not isinstance(access, str) or not access.strip():
        raise WorkflowError("AUTH_REQUIRED", "Grok 登录未返回访问令牌", 422)
    expires_in = payload.get("expires_in")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        expires_in = 3600
    refresh = payload.get("refresh_token")
    if not isinstance(refresh, str) or not refresh.strip():
        refresh = fallback_refresh
    return {
        "access_token": access.strip(),
        "refresh_token": refresh.strip() if isinstance(refresh, str) else "",
        "expires_at": time.time() + float(expires_in) - REFRESH_SKEW,
    }


def _token_error(response):
    detail = " ".join((response.text or "").split())[:200]
    if response.status_code in (401, 403):
        message = "Grok 登录被拒绝或当前订阅无法使用 API。可改用自定义 API 密钥。"
        if detail:
            message += f" ({detail})"
        return WorkflowError("AUTH_REQUIRED", message, 422)
    message = f"Grok 令牌请求失败（HTTP {response.status_code}）"
    if detail:
        message += f"：{detail}"
    return WorkflowError("REQUEST_FAILED", message, 422)


def credentials_from_response(response, fallback_refresh=""):
    if not response.is_success:
        raise _token_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise WorkflowError("REQUEST_FAILED", "Grok 令牌响应不是 JSON", 422) from exc
    if not isinstance(payload, dict):
        raise WorkflowError("REQUEST_FAILED", "Grok 令牌响应无效", 422)
    return credentials_from_tokens(payload, fallback_refresh)


async def exchange_code(client, code, verifier):
    return await client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        headers=FORM_HEADERS,
    )


async def refresh_tokens(client, refresh_token):
    return await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        headers=FORM_HEADERS,
    )


def chat_models(payload):
    raw = payload.get("data", payload) if isinstance(payload, dict) else payload
    models = []
    if not isinstance(raw, list):
        return models
    for item in raw:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model")
        if not isinstance(model_id, str) or not model_id:
            continue
        lowered = model_id.lower()
        if "imagine" in lowered or lowered.startswith("grok-image"):
            continue
        modalities = item.get("inputModalities") or item.get("input_modalities")
        if not isinstance(modalities, list) or not modalities:
            modalities = ["text", "image"]
        models.append({"id": model_id, "inputModalities": [str(m) for m in modalities]})
    return models[:50]


def select_model(choices, model_id):
    if model_id:
        return next(
            (m for m in choices if model_id in (m.get("id"), m.get("model"))),
            None,
        )
    return (
        next((m for m in choices if str(m.get("id") or "").startswith("grok-4.6")), None)
        or next((m for m in choices if str(m.get("id") or "").startswith("grok-4")), None)
        or (choices[0] if choices else None)
    )


def http_connection(conn, access_token):
    return {
        "kind": "openai_compatible",
        "name": conn.get("name") or "Grok 官方订阅",
        "enabled": True,
        "base_url": API_BASE,
        "protocol": "chat_completions",
        "model_id": conn.get("model_id") or "",
        "auth_mode": "bearer",
        "image_support": "supported",
        "timeout_seconds": conn["timeout_seconds"],
        "_access_token": access_token,
    }


class LoopbackLogin:
    def __init__(self):
        self.verifier, self.challenge = generate_pkce()
        self.state = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(32)
        self.auth_url = authorize_url(self.challenge, self.state, self.nonce)
        self.server = None
        self._future = None

    async def start(self):
        self._future = asyncio.get_running_loop().create_future()
        try:
            self.server = await asyncio.start_server(
                self._handle, OAUTH_HOST, OAUTH_PORT
            )
        except OSError as exc:
            raise WorkflowError(
                "AUTH_REQUIRED",
                f"无法监听 {REDIRECT_URI}，请关闭占用 56121 端口的程序后重试",
                422,
            ) from exc

    async def wait_code(self):
        try:
            return await asyncio.wait_for(self._future, LOGIN_TIMEOUT)
        except TimeoutError as exc:
            raise WorkflowError(
                "AUTH_REQUIRED", "Grok 登录超时，请重新点击登录", 422
            ) from exc

    async def close(self):
        server = self.server
        self.server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        if self._future is not None and not self._future.done():
            self._future.cancel()

    def _settle(self, result=None, error=None):
        if self._future is None or self._future.done():
            return False
        if error is not None:
            self._future.set_exception(error)
        else:
            self._future.set_result(result)
        return True

    async def _respond(self, writer, status, body, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8")
        header = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store\r\n"
            "\r\n"
        )
        writer.write(header.encode("ascii") + payload)
        await writer.drain()

    async def _handle(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            line = raw.split(b"\r\n", 1)[0].decode("ascii", "replace")
            parts = line.split(" ")
            if len(parts) < 2:
                await self._respond(writer, 400, error_page("Bad request"))
                return
            if parts[0] not in ("GET", "HEAD"):
                await self._respond(writer, 405, error_page("Method not allowed"))
                return
            parsed = urlparse(parts[1])
            if parsed.path != "/callback":
                await self._respond(writer, 404, error_page("Not found"))
                return
            params = parse_qs(parsed.query)
            error = (params.get("error_description") or params.get("error") or [None])[0]
            code = (params.get("code") or [None])[0]
            got_state = (params.get("state") or [None])[0]
            if error:
                message = str(error)[:200]
                await self._respond(writer, 200, error_page(message))
                self._settle(error=WorkflowError("AUTH_REQUIRED", message, 422))
                return
            if not code:
                await self._respond(writer, 400, error_page("缺少授权码"))
                self._settle(error=WorkflowError("AUTH_REQUIRED", "Grok 登录未返回授权码", 422))
                return
            if got_state != self.state:
                await self._respond(writer, 400, error_page("登录状态不匹配"))
                self._settle(error=WorkflowError("AUTH_REQUIRED", "Grok 登录状态不匹配", 422))
                return
            if self._future is not None and self._future.done():
                await self._respond(writer, 410, error_page("Already handled"))
                return
            await self._respond(writer, 200, SUCCESS_HTML)
            self._settle(result=code)
        except Exception as exc:
            if self._future is not None and not self._future.done():
                self._settle(error=exc)
            try:
                await self._respond(writer, 500, error_page("Callback failed"))
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
