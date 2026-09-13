"""Connection settings with write-only API keys and legacy environment references."""

import copy
import os
import re
from urllib.parse import urlparse
from dotenv import dotenv_values

from .store import WorkflowError
from .model_config import MODEL_STAGES, settings_models
from .models import Binding
from pydantic import ValidationError

DEFAULT_SETTINGS = {
    "stage_models": {},
    "llm_concurrency": 2,
    "image_repair_concurrency": 2,
    "default_connection": "openai_subscription",
    "connections": {
        "openai_subscription": {
            "name": "ChatGPT 官方订阅",
            "kind": "codex_chatgpt",
            "enabled": True,
            "model_id": "",
            "timeout_seconds": 600,
        },
        "custom_api": {
            "name": "自定义 API / vLLM",
            "kind": "openai_compatible",
            "enabled": False,
            "base_url": "",
            "protocol": "chat_completions",
            "model_id": "",
            "auth_mode": "bearer",
            "image_support": "unknown",
            "timeout_seconds": 600,
        },
    },
}


def secret(root, name):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", name):
        raise WorkflowError("INVALID_SECRET_REF", "凭据配置须为环境变量名", 422)
    return os.environ.get(name) or dotenv_values(root / ".env").get(name) or ""


def validate_url(url, allow_empty=False):
    if allow_empty and not url:
        return
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
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
        # Accept saved settings and forms from before provider limits were removed.
        conn.pop("max_in_flight", None)
        kind = conn.get("kind")
        if "name" in conn:
            display_name = conn["name"]
            if not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 100:
                raise WorkflowError("INVALID_CONFIG", "供应商名称须为 1–100 个字符", 422)
            conn["name"] = display_name.strip()
        allowed = set(
            DEFAULT_SETTINGS["connections"][
                "openai_subscription" if kind == "codex_chatgpt" else "custom_api"
            ]
        ) | ({"api_key_env"} if kind == "openai_compatible" else set())
        if kind not in ("codex_chatgpt", "openai_compatible") or set(conn) - allowed:
            raise WorkflowError("INVALID_CONFIG", "连接字段无效", 422)
        if (
            not isinstance(conn.get("timeout_seconds"), int)
            or not 1 <= conn["timeout_seconds"] <= 3600
        ):
            raise WorkflowError("INVALID_CONFIG", "连接超时必须为 1–3600 秒", 422)
        if kind == "openai_compatible":
            validate_url(conn.get("base_url", ""), allow_empty=not conn.get("enabled"))
            if conn.get("protocol") not in ("responses", "chat_completions"):
                raise WorkflowError("INVALID_CONFIG", "必须显式选择 API 协议", 422)
            if conn.get("auth_mode") not in ("bearer", "none"):
                raise WorkflowError("INVALID_CONFIG", "鉴权方式无效", 422)
            if conn.get("image_support") not in ("unknown", "supported", "unsupported"):
                raise WorkflowError("INVALID_CONFIG", "图像能力状态无效", 422)
            if "api_key_env" in conn and not re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,99}", conn["api_key_env"]
            ):
                raise WorkflowError("INVALID_CONFIG", "凭据必须填写环境变量名称", 422)
    if value["default_connection"] not in value["connections"]:
        raise WorkflowError("INVALID_CONFIG", "默认连接不存在", 422)
    if type(value["llm_concurrency"]) is not int or value["llm_concurrency"] < 1:
        raise WorkflowError("INVALID_CONFIG", "并行任务数必须为正整数", 422)
    if type(value["image_repair_concurrency"]) is not int or value["image_repair_concurrency"] < 1:
        raise WorkflowError("INVALID_CONFIG", "图片修复并发必须为正整数", 422)
    bindings = value["stage_models"]
    if not isinstance(bindings, dict) or set(bindings) != set(MODEL_STAGES):
        raise WorkflowError("INVALID_CONFIG", "请完整设置各阶段的模型配置", 422)
    for stage, binding in bindings.items():
        try:
            binding = Binding.model_validate(binding).model_dump()
        except ValidationError as exc:
            raise WorkflowError("INVALID_CONFIG", f"{MODEL_STAGES[stage]}的模型配置无效", 422) from exc
        conn = value["connections"].get(binding["connection_id"])
        if not conn or not conn.get("enabled"):
            raise WorkflowError("INVALID_CONFIG", f"{MODEL_STAGES[stage]}请选择已启用的连接", 422)
        bindings[stage] = binding
    return value


def prepare_settings(value, previous):
    """Validate everything before atomically saving settings and private credentials."""
    value = copy.deepcopy(value)
    value.setdefault("stage_models", settings_models(previous))
    value.setdefault("llm_concurrency", previous.get("llm_concurrency", 2))
    value.setdefault("image_repair_concurrency", previous.get("image_repair_concurrency", previous.get("llm_concurrency", 2)))
    if not value["stage_models"]:
        value["stage_models"] = settings_models(value)
    elif isinstance(value["stage_models"], dict) and "image_repair" not in value["stage_models"]:
        # A still-open old settings form must not overwrite the new selection.
        value["stage_models"]["image_repair"] = settings_models(previous)["image_repair"]
    credentials = {}
    for name, conn in value.get("connections", {}).items():
        if conn.get("kind") != "openai_compatible":
            continue
        key = conn.pop("api_key", "")
        clear = conn.pop("clear_api_key", False)
        conn.pop("api_key_configured", None)
        if not isinstance(key, str) or not isinstance(clear, bool):
            raise WorkflowError(
                "INVALID_CONFIG", "API 密钥必须为文本，清除选项必须为布尔值", 422
            )
        key = key.strip()
        if any(c.isspace() for c in key) or not key.isascii():
            raise WorkflowError(
                "INVALID_CONFIG", "API 密钥不能包含空格、换行或非 ASCII 字符", 422
            )
        if key and clear:
            raise WorkflowError("INVALID_CONFIG", "填写新密钥时请取消清除选项", 422)
        old = previous["connections"].get(name, {})
        if "api_key_env" not in conn and "api_key_env" in old:
            conn["api_key_env"] = old["api_key_env"]
        if key or clear:
            credentials[name] = key
    return validate_settings(value), credentials
