"""Model router.

Two tiers, picked per call site:
  fast  - bulk extraction from filings (results tables, appointment profiles,
          guidance statements per transcript). Cheap, high volume.
  smart - reasoning over already-extracted facts (promise vs delivery verdicts,
          bull/bear, grounded Q&A). Few calls, small prompts.

Any OpenAI-compatible provider works (Gemini, Groq, OpenRouter, Cerebras,
Ollama) plus Anthropic natively. Pick with env vars, e.g.
  FINN_FAST=groq:openai/gpt-oss-20b
  FINN_SMART=groq:openai/gpt-oss-120b
or just set one API key and the defaults below are used.

Every response is cached on disk by (model, prompt), so re-opening a filing or
a research page never pays twice. If no provider is configured, callers fall
back to deterministic output and the app still runs.
"""

import json
import os
import re
import threading
import time

import requests

import store

PROVIDERS = {
    # name: (base_url, key_env, default_fast, default_smart)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
               "gemini-2.5-flash-lite", "gemini-2.5-flash"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY",
             "openai/gpt-oss-20b", "openai/gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                   "google/gemini-2.5-flash-lite", "google/gemini-2.5-flash"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
                 "llama3.1-8b", "llama-3.3-70b"),
    "anthropic": ("https://api.anthropic.com/v1", "FINN_ANTHROPIC_API_KEY",
                  "claude-haiku-5-5", "claude-sonnet-5-5"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.2", "llama3.1"),
    # Keyless last resort so the demo works with zero setup. Only public filing
    # text is ever sent. Disable with FINN_KEYLESS=0.
    "pollinations": ("https://text.pollinations.ai/openai", None, "openai", "openai"),
}
_AUTO_ORDER = ["gemini", "groq", "openrouter", "cerebras", "anthropic"]
_KEYLESS_GAP = 3.0
_keyless_lock = threading.Lock()
_keyless_last = [0.0]
_keyless_blocked_until = [0.0]  # set when the free tier says "quota exceeded"

_usage_lock = threading.Lock()
USAGE = {"calls": 0, "cache_hits": 0, "in_tokens": 0, "out_tokens": 0, "by_tier": {"fast": 0, "smart": 0}}


class LLMUnavailable(RuntimeError):
    pass


def _resolve(tier: str):
    spec = os.environ.get("FINN_FAST" if tier == "fast" else "FINN_SMART", "").strip()
    if spec:
        prov, _, model = spec.partition(":")
        if prov not in PROVIDERS:
            raise LLMUnavailable(f"unknown provider {prov}")
        base, key_env, f, s = PROVIDERS[prov]
        return prov, base, (os.environ.get(key_env) if key_env else "ollama"), model or (f if tier == "fast" else s)
    for prov in _AUTO_ORDER:
        base, key_env, f, s = PROVIDERS[prov]
        if os.environ.get(key_env or ""):
            return prov, base, os.environ[key_env], f if tier == "fast" else s
    if os.environ.get("FINN_KEYLESS", "1") != "0" and time.time() > _keyless_blocked_until[0]:
        base, _, f, s = PROVIDERS["pollinations"]
        return "pollinations", base, "keyless", f if tier == "fast" else s
    raise LLMUnavailable("no LLM provider configured")


def available() -> dict:
    out = {}
    for tier in ("fast", "smart"):
        try:
            prov, _, key, model = _resolve(tier)
            out[tier] = f"{prov}:{model}" if key else None
        except LLMUnavailable:
            out[tier] = None
    return out


def enabled() -> bool:
    return all(available().values())


def _call(prov, base, key, model, system, prompt, max_tokens, json_mode):
    if prov == "anthropic":
        r = requests.post(
            f"{base}/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
        text = "".join(b.get("text", "") for b in d.get("content", []))
        u = d.get("usage", {})
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)
    body = {"model": model, "temperature": 0.1, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if json_mode and prov in ("groq", "openrouter", "cerebras", "ollama", "gemini"):
        body["response_format"] = {"type": "json_object"}
    url, headers = f"{base}/chat/completions", {"Authorization": f"Bearer {key}"}
    if prov == "pollinations":
        url, headers = base, {}
        body.pop("temperature")
        body["max_tokens"] = max_tokens + 1500  # reasoning model: leave room to think
        with _keyless_lock:  # anonymous tier is rate limited; serialise calls
            wait = _keyless_last[0] + _KEYLESS_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            r = requests.post(url, headers=headers, json=body, timeout=180)
            _keyless_last[0] = time.time()
    else:
        r = requests.post(url, headers=headers, json=body, timeout=120)
    if r.status_code == 400 and "response_format" in body:
        body.pop("response_format")
        r = requests.post(url, headers=headers, json=body, timeout=120)
    r.raise_for_status()
    d = r.json()
    u = d.get("usage") or {}
    return d["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def complete(prompt: str, *, tier: str = "fast", system: str = "", max_tokens: int = 1200,
             json_mode: bool = False) -> str:
    prov, base, key, model = _resolve(tier)
    if not key:
        raise LLMUnavailable(f"{prov} key missing")
    system = system or "You are a precise financial analyst. Use only the provided source text."
    ckey = json.dumps([prov, model, system, prompt, max_tokens, json_mode])
    hit = store.get("llm", ckey)
    if hit is not None:
        with _usage_lock:
            USAGE["cache_hits"] += 1
        return hit["text"]
    last = None
    for attempt in range(3):
        try:
            text, tin, tout = _call(prov, base, key, model, system, prompt, max_tokens, json_mode)
            break
        except requests.HTTPError as e:
            last = e
            code = e.response.status_code if e.response is not None else 0
            if prov == "pollinations" and code in (402, 403, 429):
                _keyless_blocked_until[0] = time.time() + 15 * 60
                raise LLMUnavailable("free keyless model is over quota; add GEMINI_API_KEY or GROQ_API_KEY") from e
            if code in (429, 500, 502, 503, 529):
                time.sleep(4 * (attempt + 1))
                continue
            raise
    else:
        raise last
    with _usage_lock:
        USAGE["calls"] += 1
        USAGE["in_tokens"] += tin or 0
        USAGE["out_tokens"] += tout or 0
        USAGE["by_tier"][tier] += 1
    store.put("llm", ckey, {"text": text, "model": f"{prov}:{model}"})
    return text


def complete_json(prompt: str, **kw):
    text = complete(prompt, json_mode=True, **kw)
    return parse_json(text)


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except ValueError:
                continue
    raise ValueError("model did not return JSON")
