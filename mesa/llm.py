"""
Router de modelos con cadena de respaldo.

Gratis: Groq -> Cerebras -> OpenRouter -> Gemini.  Premium: Anthropic (Claude).
Si un proveedor falla (cuota, 429, caída, JSON roto) se pasa al siguiente.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import requests
from urllib.parse import urlparse

from .config import secret

TIMEOUT = 90


@dataclass
class Provider:
    key_name: str
    name: str
    kind: str  # "openai" | "gemini" | "anthropic"
    base_url: str
    models: dict  # {"fast": id, "strong": id}
    free: bool = True
    max_input_chars: int = 40000  # recorte defensivo según límites del plan gratis
    extra_headers: dict = field(default_factory=dict)

    def __post_init__(self):
        self.base_url = secret(self.key_name.replace("_API_KEY", "_BASE_URL"), self.base_url)

    @property
    def key(self) -> str:
        return secret(self.key_name)

    @property
    def enabled(self) -> bool:
        return bool(self.key)


def providers() -> list[Provider]:
    return [
        Provider("GROQ_API_KEY", "Groq", "openai", "https://api.groq.com/openai/v1",
                 {"fast": secret("GROQ_FAST_MODEL", "openai/gpt-oss-20b"),
                  "strong": secret("GROQ_STRONG_MODEL", "openai/gpt-oss-120b")},
                 max_input_chars=22000),
        Provider("CEREBRAS_API_KEY", "Cerebras", "openai", "https://api.cerebras.ai/v1",
                 {"fast": secret("CEREBRAS_MODEL", "gpt-oss-120b"),
                  "strong": secret("CEREBRAS_MODEL", "gpt-oss-120b")},
                 max_input_chars=16000),  # el plan gratis tiene contexto de ~8k tokens
        Provider("OPENROUTER_API_KEY", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
                 {"fast": secret("OPENROUTER_MODEL", "openrouter/free"),
                  "strong": secret("OPENROUTER_MODEL", "openrouter/free")},
                 extra_headers={"HTTP-Referer": "https://streamlit.app", "X-Title": "Mesa Notas Vivas"}),
        Provider("GEMINI_API_KEY", "Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta",
                 {"fast": secret("GEMINI_FAST_MODEL", "gemini-3.5-flash-lite"),
                  "strong": secret("GEMINI_STRONG_MODEL", "gemini-3.8-flash")}),
        Provider("ANTHROPIC_API_KEY", "Claude", "anthropic", "https://api.anthropic.com",
                 {"fast": secret("PREMIUM_MODEL", "claude-sonnet-5"),
                  "strong": secret("PREMIUM_MODEL", "claude-sonnet-5")},
                 free=False, max_input_chars=150000),
    ]


def provider_status() -> dict[str, bool]:
    return {p.name: p.enabled for p in providers()}


class LLMError(RuntimeError):
    pass


def _err(r: requests.Response) -> str:
    try:
        j = r.json()
        return str(j.get("error", {}).get("message") or j.get("message") or j)[:240]
    except Exception:
        return r.text[:240]


def _openai(p: Provider, model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {p.key}", "Content-Type": "application/json", **p.extra_headers}
    for attempt in range(2):
        r = requests.post(f"{p.base_url}/chat/completions", json=body, headers=headers, timeout=TIMEOUT)
        if r.status_code == 429 and attempt == 0:
            wait = float(r.headers.get("retry-after", "0") or 0)
            if 0 < wait <= 12:  # espera corta: reintenta en el mismo proveedor
                time.sleep(wait)
                continue
        if r.status_code == 400 and json_mode and "json" in r.text.lower() and attempt == 0:
            body.pop("response_format", None)  # el modelo se desvió del JSON: reintento sin modo JSON
            continue
        break
    if not r.ok:
        raise LLMError(f"{p.name} {r.status_code}: {_err(r)}")
    msg = (r.json().get("choices") or [{}])[0].get("message", {})
    text = (msg.get("content") or "").strip()
    if not text:
        raise LLMError(f"{p.name}: respuesta vacía")
    return text


def _gemini(p: Provider, model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    cfg = {"temperature": 0.2, "maxOutputTokens": max_tokens}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    r = requests.post(f"{p.base_url}/models/{model}:generateContent",
                      headers={"x-goog-api-key": p.key, "Content-Type": "application/json"},
                      json={"systemInstruction": {"parts": [{"text": system}]},
                            "contents": [{"role": "user", "parts": [{"text": user}]}],
                            "generationConfig": cfg}, timeout=TIMEOUT)
    if not r.ok:
        raise LLMError(f"Gemini {r.status_code}: {_err(r)}")
    d = r.json()
    parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(x.get("text", "") for x in parts if not x.get("thought")).strip()
    if not text:
        raise LLMError("Gemini: respuesta vacía")
    return text


def _anthropic(p: Provider, model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    if json_mode:
        system += "\n\nRespondé únicamente con el objeto JSON, sin texto antes ni después ni bloques de código."
    r = requests.post(f"{p.base_url}/v1/messages",
                      headers={"x-api-key": p.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": model, "max_tokens": max_tokens, "system": system,
                            "messages": [{"role": "user", "content": user}]}, timeout=TIMEOUT)
    if not r.ok:
        raise LLMError(f"Claude {r.status_code}: {_err(r)}")
    text = "\n".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise LLMError("Claude: respuesta vacía")
    return text


CALL = {"openai": _openai, "gemini": _gemini, "anthropic": _anthropic}


def parse_json(text: str) -> dict:
    t = re.sub(r"<think>[\s\S]*?</think>", "", text)
    t = re.sub(r"```(?:json)?", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


@dataclass
class Result:
    data: dict | str
    provider: str
    model: str
    errors: list[str]


def chain(mode: str) -> list[Provider]:
    """mode: 'gratis' | 'premium' | 'gratis+premium' (gratis y Claude como último recurso)."""
    ps = [p for p in providers() if p.enabled]
    free = [p for p in ps if p.free]
    paid = [p for p in ps if not p.free]
    if mode == "premium":
        return paid + free
    if mode == "gratis+premium":
        return free + paid
    return free


def run(system: str, user: str, *, mode: str = "gratis", tier: str = "strong",
        json_mode: bool = True, max_tokens: int = 3000) -> Result:
    ps = chain(mode)
    if not ps:
        raise LLMError("No hay proveedores configurados para este modo. Cargá al menos GROQ_API_KEY en Secrets.")
    errors: list[str] = []
    for p in ps:
        model = p.models[tier]
        u = user if len(user) <= p.max_input_chars else user[: p.max_input_chars] + "\n\n[texto recortado]"
        try:
            text = CALL[p.kind](p, model, system, u, json_mode, max_tokens)
            data = parse_json(text) if json_mode else text
            return Result(data, p.name, model, errors)
        except requests.Timeout:
            errors.append(f"{p.name}: tiempo agotado")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p.name}: {e}"[:300])
    raise LLMError("Fallaron todos los modelos → " + " | ".join(errors))


def groq_compound(prompt: str) -> tuple[str, list[dict]]:
    """Búsqueda web integrada de Groq (groq/compound). Devuelve texto + fuentes usadas."""
    key = secret("GROQ_API_KEY")
    if not key:
        raise LLMError("Sin GROQ_API_KEY")
    base = secret("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    r = requests.post(f"{base}/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": secret("GROQ_SEARCH_MODEL", "groq/compound-mini"),
                            "messages": [{"role": "user", "content": prompt}]}, timeout=TIMEOUT)
    if not r.ok:
        raise LLMError(f"Groq compound {r.status_code}: {_err(r)}")
    msg = r.json()["choices"][0]["message"]
    sources = []
    for tool in msg.get("executed_tools") or []:
        for res in (tool.get("search_results") or {}).get("results", []) or []:
            sources.append({"title": res.get("title", ""), "url": res.get("url", ""),
                            "body": (res.get("content") or "")[:1500], "date": res.get("published_date", ""),
                            "source": (urlparse(res.get("url", "")).hostname or "").replace("www.", "")})
    return msg.get("content") or "", sources
