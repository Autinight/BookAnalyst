"""Stage bindings and explicit, next-request refresh of saved model settings."""
import asyncio
import copy
import time

from .models import Binding
from .store import digest


MODEL_STAGES = {
    "setup": "全书设置与计数规则",
    "convert": "分批视觉转换",
    "seams": "页面衔接",
    "headings": "标题层级与附录",
    "references": "标签与引用",
    "reference_repair": "引用局部修复",
    "finish": "最终编译与修复",
    "template_apply": "模板外观迁移",
    "image_repair": "图片修复",
}
PURPOSE_STAGE = {"compile_repair": "finish", "counter_repair": "setup"}


def legacy_models(config):
    binding = config.get("model") or Binding().model_dump()
    return {
        stage: dict(binding, reasoning_effort=(binding.get("reasoning_effort", "medium")
                    if stage in ("convert", "seams", "image_repair") else config.get("structure_effort", "xhigh")))
        for stage in MODEL_STAGES
    }


def settings_models(settings, legacy=None):
    if settings.get("stage_models"):
        models = copy.deepcopy(settings["stage_models"])
        # Old installations get an editable initial copy, not a live alias.
        if "image_repair" not in models and "convert" in models:
            models["image_repair"] = copy.deepcopy(models["convert"])
        for binding in models.values():
            if not binding["model_id"]:
                binding["model_id"] = settings["connections"].get(binding["connection_id"], {}).get("model_id", "")
        return models
    cid = settings["default_connection"]
    return legacy_models(legacy or {"model": {
        "connection_id": cid, "model_id": settings["connections"][cid].get("model_id", ""),
        "reasoning_effort": "medium",
    }})


def stage_binding(config, purpose, *, repair=False):
    stage = PURPOSE_STAGE.get(purpose, purpose)
    if config.get("stage_models"):
        # An existing run keeps its frozen, pre-upgrade model until refreshed.
        if stage == "image_repair" and stage not in config["stage_models"]:
            stage = "convert"
        return copy.deepcopy(config["stage_models"][stage])
    # Old runs keep their original semantics until the user explicitly refreshes.
    if repair and stage in ("convert", "seams"):
        stage = "setup"
    return legacy_models(config)[stage]


def refresh_pending(store, rid):
    run = store.get("run", rid)
    if not run.get("model_refresh_pending"):
        return run
    settings = store.get("settings", "main")
    bindings = settings_models(settings)
    default_limit = settings.get("llm_concurrency", run["config"]["llm_concurrency"])
    if run.get("kind") == "image_repair":
        default_limit = settings.get("image_repair_concurrency", default_limit)

    def apply(current):
        if current.get("model_refresh_pending"):
            current["config"].update(stage_models=bindings,
                                     llm_concurrency=current.pop("pending_llm_concurrency", default_limit))
            current["model_refresh_pending"] = False
            current["model_settings_updated_at"] = time.time()
    return store.change(rid, apply)


async def request_run(providers, rid, purpose, *, repair=False, require_image=False):
    """Freeze one request's binding; later updates cannot mutate an active call."""
    run = await asyncio.to_thread(refresh_pending, providers.store, rid)
    binding = stage_binding(run["config"], purpose, repair=repair)
    if run["config"].get("stage_models"):
        requested = copy.deepcopy(binding)
        # Resolve each distinct binding/capability once, not once per book page.
        settings = await asyncio.to_thread(providers.store.get, "settings", "main")
        key = digest({"binding": binding, "connection": settings["connections"].get(binding["connection_id"]),
                      "images": require_image})
        if not hasattr(providers, "stage_binding_cache"):
            providers.stage_binding_cache, providers.stage_binding_locks = {}, {}
        async with providers.stage_binding_locks.setdefault(key, asyncio.Lock()):
            if key not in providers.stage_binding_cache:
                resolved, _ = await providers.resolve(binding, require_image=require_image)
                providers.stage_binding_cache[key] = resolved
            binding = copy.deepcopy(providers.stage_binding_cache[key])
        if not requested["model_id"] and binding["model_id"]:
            stage = PURPOSE_STAGE.get(purpose, purpose)
            def freeze_default(current):
                if (current["revision"] == run["revision"] and not current.get("model_refresh_pending")
                        and current["config"].get("stage_models", {}).get(stage) == requested):
                    current["config"]["stage_models"][stage] = copy.deepcopy(binding)
            await asyncio.to_thread(providers.store.change, rid, freeze_default)
    run["config"]["model"] = binding
    return run
