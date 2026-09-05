"""Planning estimates, not provider usage: OpenAI encoding with margin, bytes for unknown models."""
from functools import lru_cache
from math import ceil
import tiktoken

@lru_cache(maxsize=1)
def encoding():
    return tiktoken.get_encoding("o200k_base")

def estimate(text, model_id=""):
    if model_id.startswith(("gpt-", "o1", "o3", "o4")):
        return ceil(len(encoding().encode(text, disallowed_special=())) * 1.15)
    return len(text.encode("utf-8"))

def budget_tokens(text, config, roles):
    return max(estimate(text, config[r]["model_id"]) for r in roles)
