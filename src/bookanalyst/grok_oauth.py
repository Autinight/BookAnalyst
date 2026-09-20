"""Grok SuperGrok / X Premium+ OAuth using the public Grok CLI client.

Login follows OpenHanako / Grok Build: RFC 8628 device code against auth.x.ai,
then API calls through cli-chat-proxy.grok.com with grok-cli headers.
"""

import asyncio
import time
from urllib.parse import urlparse

from .store import WorkflowError

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
DISCOVERY_URL = "https://auth.x.ai/.well-known/openid-configuration"
DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
API_BASE = "https://cli-chat-proxy.grok.com/v1"
REFRESH_SKEW = 120
LOGIN_TIMEOUT = 300
FORM_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "BookAnalyst",
}
CLI_HEADERS = {
    "x-xai-token-auth": "xai-grok-cli",
    "x-grok-client-version": "0.2.95",
    "x-grok-client-identifier": "bookanalyst",
}
FALLBACK_MODELS = [
    {"id": "grok-4.6", "inputModalities": ["text", "image"]},
    {"id": "grok-4.5-latest", "inputModalities": ["text", "image"]},
    {"id": "grok-4.5", "inputModalities": ["text", "image"]},
    {"id": "grok-4.3", "inputModalities": ["text", "image"]},
    {"id": "grok-build-latest", "inputModalities": ["text", "image"]},
]


def _auth_url(value, name):
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("REQUEST_FAILED", f"Grok 认证配置缺少 {name}", 422)
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "auth.x.ai"
        or parsed.port
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise WorkflowError("REQUEST_FAILED", f"Grok 认证地址不可信：{name}", 422)
    return parsed.geturl()


def _verify_url(value, name):
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("REQUEST_FAILED", f"Grok 登录未返回 {name}", 422)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not (host == "x.ai" or host.endswith(".x.ai"))
        or parsed.port
        or parsed.username
        or parsed.password
    ):
        raise WorkflowError("REQUEST_FAILED", f"Grok 登录地址不可信：{name}", 422)
    return parsed.geturl()


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


def _token_error(response, payload=None):
    payload = payload if isinstance(payload, dict) else {}
    detail = str(payload.get("error_description") or payload.get("error") or "").strip()[:200]
    if response.status_code in (401, 403) or payload.get("error") in (
        "access_denied",
        "authorization_denied",
    ):
        message = "Grok 登录被拒绝或当前订阅无法使用 API。"
        if detail:
            message += f" ({detail})"
        return WorkflowError("AUTH_REQUIRED", message, 422)
    if payload.get("error") == "expired_token":
        return WorkflowError("AUTH_REQUIRED", "Grok 登录超时，请重新点击登录", 422)
    message = f"Grok 令牌请求失败（HTTP {response.status_code}）"
    if detail:
        message += f"：{detail}"
    return WorkflowError("REQUEST_FAILED", message, 422)


def credentials_from_response(response, fallback_refresh=""):
    try:
        payload = response.json()
    except ValueError as exc:
        raise WorkflowError("REQUEST_FAILED", "Grok 令牌响应不是 JSON", 422) from exc
    if not isinstance(payload, dict):
        raise WorkflowError("REQUEST_FAILED", "Grok 令牌响应无效", 422)
    if not response.is_success or payload.get("error"):
        raise _token_error(response, payload)
    return credentials_from_tokens(payload, fallback_refresh)


async def discover(client):
    try:
        listed = await client.get(DISCOVERY_URL, headers={"Accept": "application/json"})
        payload = listed.json() if listed.is_success else {}
        if isinstance(payload, dict):
            return (
                _auth_url(payload.get("device_authorization_endpoint"), "device_authorization_endpoint"),
                _auth_url(payload.get("token_endpoint"), "token_endpoint"),
            )
    except (WorkflowError, ValueError, TypeError):
        pass
    return DEVICE_CODE_URL, TOKEN_URL


async def request_device(client, device_endpoint):
    return await client.post(
        device_endpoint,
        data={"client_id": CLIENT_ID, "scope": SCOPE},
        headers=FORM_HEADERS,
    )


def parse_device(response):
    try:
        payload = response.json()
    except ValueError as exc:
        raise WorkflowError("REQUEST_FAILED", "Grok 设备授权响应不是 JSON", 422) from exc
    if not response.is_success:
        raise _token_error(response, payload if isinstance(payload, dict) else {})
    if not isinstance(payload, dict):
        raise WorkflowError("REQUEST_FAILED", "Grok 设备授权响应无效", 422)
    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    if not isinstance(device_code, str) or not device_code:
        raise WorkflowError("REQUEST_FAILED", "Grok 未返回设备授权码", 422)
    if not isinstance(user_code, str) or not user_code.strip():
        raise WorkflowError("REQUEST_FAILED", "Grok 未返回用户授权码", 422)
    uri = payload.get("verification_uri") or payload.get("verification_url")
    complete = payload.get("verification_uri_complete")
    interval = payload.get("interval")
    expires_in = payload.get("expires_in")
    if not isinstance(interval, (int, float)) or interval <= 0:
        interval = 5
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        expires_in = LOGIN_TIMEOUT
    return {
        "device_code": device_code,
        "user_code": user_code.strip(),
        "verification_uri": _verify_url(uri, "verification_uri"),
        "verification_uri_complete": (
            _verify_url(complete, "verification_uri_complete") if complete else ""
        ),
        "interval": float(interval),
        "expires_in": float(expires_in),
    }


async def poll_device(client, token_endpoint, device_code):
    return await client.post(
        token_endpoint,
        data={
            "grant_type": DEVICE_CODE_GRANT,
            "device_code": device_code,
            "client_id": CLIENT_ID,
        },
        headers=FORM_HEADERS,
    )


async def refresh_tokens(client, refresh_token, token_endpoint=TOKEN_URL):
    return await client.post(
        token_endpoint,
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
    return models[:50] or list(FALLBACK_MODELS)


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


def http_connection(conn, access_token, model_id=""):
    headers = dict(CLI_HEADERS)
    chosen = model_id or conn.get("model_id") or ""
    if chosen:
        headers["x-grok-model-override"] = chosen.split("/")[-1]
    return {
        "kind": "openai_compatible",
        "name": conn.get("name") or "Grok 官方订阅",
        "enabled": True,
        "base_url": API_BASE,
        "protocol": "responses",
        "model_id": chosen or conn.get("model_id") or "",
        "auth_mode": "bearer",
        "image_support": "supported",
        "timeout_seconds": conn["timeout_seconds"],
        "_access_token": access_token,
        "_extra_headers": headers,
        "_stream": True,
    }


class DeviceLogin:
    def __init__(self, device, token_endpoint):
        self.device = device
        self.token_endpoint = token_endpoint
        self._cancelled = asyncio.Event()

    @property
    def auth_url(self):
        return self.device["verification_uri_complete"] or self.device["verification_uri"]

    @property
    def user_code(self):
        return self.device["user_code"]

    async def close(self):
        self._cancelled.set()

    async def wait_tokens(self, client):
        interval = max(0.05, self.device["interval"])
        deadline = time.time() + min(LOGIN_TIMEOUT, self.device["expires_in"])
        while time.time() < deadline:
            try:
                await asyncio.wait_for(self._cancelled.wait(), timeout=interval)
                raise asyncio.CancelledError
            except TimeoutError:
                pass
            response = await poll_device(client, self.token_endpoint, self.device["device_code"])
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            error = payload.get("error")
            if response.is_success and not error:
                return credentials_from_tokens(payload)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise _token_error(response, payload)
        raise WorkflowError("AUTH_REQUIRED", "Grok 登录超时，请重新点击登录", 422)
