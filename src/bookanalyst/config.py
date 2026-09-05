"""Independent connection settings. Only environment-variable names are persisted."""
import os
import re
from urllib.parse import urlparse
from dotenv import dotenv_values

from .store import WorkflowError

DEFAULT_SETTINGS = {
    "default_connection": "openai_subscription",
    "connections": {
        "openai_subscription": {"kind": "codex_chatgpt", "enabled": True, "model_id": "",
                                "max_in_flight": 2, "timeout_seconds": 180},
        "custom_api": {"kind": "openai_compatible", "enabled": False, "base_url": "",
                       "protocol": "chat_completions", "model_id": "", "auth_mode": "bearer",
                       "api_key_env": "LLM_CUSTOM_API_KEY", "image_support": "unknown",
                       "max_in_flight": 2, "timeout_seconds": 180},
    },
    "mineru": {"cloud_url": "https://mineru.net/api/v4", "api_key_env": "MINERU_API_TOKEN",
               "local_url": "http://127.0.0.1:8000", "backend": "pipeline",
               "model_version": "vlm", "language": "en", "max_pages": 200,
               "max_bytes": 200 * 1024 * 1024, "timeout_seconds": 600,
               "poll_interval_seconds": 5, "poll_max_requests": 120},
}


def secret(root, name):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", name):
        raise WorkflowError("INVALID_SECRET_REF", "凭据配置须为环境变量名", 422)
    return os.environ.get(name) or dotenv_values(root / ".env").get(name) or ""


def validate_url(url, allow_empty=False):
    if allow_empty and not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise WorkflowError("INVALID_URL", "请填写不含凭据的 HTTP(S) 服务地址", 422)
    if parsed.query or parsed.fragment:
        raise WorkflowError("INVALID_URL", "服务根地址不能带查询参数或片段", 422)


def validate_settings(value):
    if set(value) != set(DEFAULT_SETTINGS):
        raise WorkflowError("INVALID_CONFIG", "配置字段不完整或包含未知字段", 422)
    if not isinstance(value["connections"], dict) or not value["connections"]:
        raise WorkflowError("INVALID_CONFIG", "至少需要一个连接", 422)
    for name, conn in value["connections"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise WorkflowError("INVALID_CONFIG", "连接名称格式无效", 422)
        kind = conn.get("kind")
        allowed = set(DEFAULT_SETTINGS["connections"]["openai_subscription" if kind == "codex_chatgpt" else "custom_api"])
        if kind not in ("codex_chatgpt", "openai_compatible") or set(conn) - allowed:
            raise WorkflowError("INVALID_CONFIG", "连接字段无效；密钥只通过环境变量引用", 422)
        if not isinstance(conn.get("max_in_flight"), int) or conn["max_in_flight"] < 1:
            raise WorkflowError("INVALID_CONFIG", "连接并发必须为正整数", 422)
        if not isinstance(conn.get("timeout_seconds"), int) or not 1 <= conn["timeout_seconds"] <= 3600:
            raise WorkflowError("INVALID_CONFIG", "连接超时必须为 1–3600 秒", 422)
        if kind == "openai_compatible":
            validate_url(conn.get("base_url", ""), allow_empty=not conn.get("enabled"))
            if conn.get("protocol") not in ("responses", "chat_completions"):
                raise WorkflowError("INVALID_CONFIG", "必须显式选择 API 协议", 422)
            if conn.get("auth_mode") not in ("bearer", "none"):
                raise WorkflowError("INVALID_CONFIG", "鉴权方式无效", 422)
            if conn.get("image_support") not in ("unknown", "supported", "unsupported"):
                raise WorkflowError("INVALID_CONFIG", "图像能力状态无效", 422)
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", conn.get("api_key_env", "")):
                raise WorkflowError("INVALID_CONFIG", "凭据必须填写环境变量名称", 422)
    if value["default_connection"] not in value["connections"]:
        raise WorkflowError("INVALID_CONFIG", "默认连接不存在", 422)
    mineru = value["mineru"]
    if set(mineru) != set(DEFAULT_SETTINGS["mineru"]):
        raise WorkflowError("INVALID_CONFIG", "MinerU 配置字段无效", 422)
    for key in ("cloud_url", "local_url"):
        validate_url(mineru[key])
    for key in ("max_pages", "max_bytes", "timeout_seconds", "poll_interval_seconds", "poll_max_requests"):
        if not isinstance(mineru[key], int) or mineru[key] < 1:
            raise WorkflowError("INVALID_CONFIG", "MinerU 限额和超时必须为正整数", 422)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", mineru["api_key_env"]):
        raise WorkflowError("INVALID_CONFIG", "MinerU 凭据必须填写环境变量名称", 422)
    return value
