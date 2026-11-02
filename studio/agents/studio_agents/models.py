"""Model layer (D-003, D-027): `models.yaml` maps each agent role to a provider and model; keys come from `.env`.

    uv run python -m studio_agents.models --list     # the model ids your keys can use, and which keys work

Keys: `studio/agents/.env` or `studio/.env`. Several per provider are allowed and are rotated through:
    GEMINI_API_KEY, GEMINI_API_KEY_2 … GEMINI_API_KEY_9, or GEMINI_API_KEYS=key1,key2   (same for GROQ_, ANTHROPIC_)
When a call fails because of the key or the provider (invalid key, quota, rate limit, outage), the next key is
tried; a rate-limited key rests for a minute; when every key of the provider fails, the role's fallbacks in
`models.yaml` are tried. A bad *answer* (invalid structured output) is not a key problem and is not retried here.

Agents never hard-code a model. `ask()` is the one way steps get a structured answer, so tests can swap in a
fake (`set_backend`); `with_rotation()` runs any model call (the Canvas Editor's agent) through the same keys.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "models.yaml"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
ENV_NAMES = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
COOLDOWN_S = 60.0
CALL_TIMEOUT_S = 90          # a call that hangs (an overloaded provider) fails, so the next key or model takes over
BLIP_RETRY_S = 3.0           # "high demand" / unavailable: one quick retry on the same key before moving on
T = TypeVar("T", bound=BaseModel)
log = logging.getLogger("studio_agents.models")

# studio/agents/.env first, then studio/.env (values already set in the environment win).
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")


class ModelError(RuntimeError):
    pass


@dataclass
class Role:
    name: str
    provider: str
    model: str
    temperature: float = 0.3


@dataclass
class Candidate:
    """One way to make a call: a provider, a model and one of its keys."""
    provider: str
    model: str
    temperature: float
    key_index: int
    key: str

    def label(self) -> str:
        return f"{self.provider}/{self.model} (key {self.key_index + 1})"


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text()) or {}


def roles() -> dict[str, Role]:
    return {k: Role(k, v["provider"], v["model"], float(v.get("temperature", 0.3))) for k, v in _config().get("roles", {}).items()}


def keys(provider: str) -> list[str]:
    """Every key for a provider, in order, without duplicates."""
    base = ENV_NAMES.get(provider)
    if not base:
        raise ModelError(f"unknown provider {provider!r}")
    found = [os.environ.get(base, "")] + [os.environ.get(f"{base}_{i}", "") for i in range(2, 10)]
    found += os.environ.get(f"{base}S", "").split(",")
    out: list[str] = []
    for k in (k.strip() for k in found):
        if k and k not in out:
            out.append(k)
    return out


# ── rotation ────────────────────────────────────────────────────────────────────────────────────────

_cooling: dict[tuple[str, str, str], float] = {}  # (provider, model, key) → time it may be used again (quotas are per model)
_preferred: dict[str, int] = {}                    # provider → index of the key that last worked
rotation_log: list[dict] = []                      # what failed and what took over (shown in run events)


def candidates(role: str, vision: bool = False) -> list[Candidate]:
    """The role's own provider and model with each of its keys (last good key first), then its fallbacks."""
    r = roles().get(role)
    if not r:
        raise ModelError(f"no role {role!r} in {CONFIG}")
    chain = [{"provider": r.provider, "model": r.model, "temperature": r.temperature}]
    for fb in (_config().get("fallbacks") or {}).get(r.provider, []):
        if vision and fb.get("vision") is False:
            continue
        chain.append({"provider": fb["provider"], "model": fb["model"], "temperature": float(fb.get("temperature", r.temperature))})
    out: list[Candidate] = []
    now = time.monotonic()
    for link in chain:
        ks = keys(link["provider"])
        start = _preferred.get(link["provider"], 0) % max(len(ks), 1)
        for i in [*range(start, len(ks)), *range(0, start)]:
            if _cooling.get((link["provider"], link["model"], ks[i]), 0) > now:
                continue
            c = Candidate(link["provider"], link["model"], link["temperature"], i, ks[i])
            if not any(o.provider == c.provider and o.model == c.model and o.key == c.key for o in out):
                out.append(c)
    return out


def retryable(e: BaseException) -> tuple[bool, bool]:
    """(try another key or model?, rest this key?) for an error from a model call."""
    text = f"{type(e).__name__} {e}".lower()
    rate = any(s in text for s in ("429", "resource_exhausted", "quota", "rate limit", "ratelimit", "throttl", "too many requests"))
    key = any(s in text for s in ("401", "403", "api key", "api_key", "permission_denied", "unauthenticated", "authentication",
                                  "invalid key", "forbidden"))
    outage = any(s in text for s in ("500", "502", "503", "504", "unavailable", "overloaded", "internal error", "timed out",
                                     "timeout", "connection", "not found", "404", "model_not_found", "does not exist"))
    return (rate or key or outage, rate)


def build_candidate(c: Candidate):
    """A Strands model for one candidate. Client-side retries are off and calls time out: rotation decides what's next."""
    if c.provider == "gemini":
        from google.genai import types
        from strands.models.gemini import GeminiModel
        http = types.HttpOptions(timeout=CALL_TIMEOUT_S * 1000, retry_options=types.HttpRetryOptions(attempts=1))
        return GeminiModel(client_args={"api_key": c.key, "http_options": http}, model_id=c.model, params={"temperature": c.temperature})
    if c.provider == "groq":
        from strands.models.openai import OpenAIModel
        return OpenAIModel(client_args={"api_key": c.key, "base_url": GROQ_BASE_URL, "timeout": CALL_TIMEOUT_S, "max_retries": 0},
                           model_id=c.model, params={"temperature": c.temperature})
    if c.provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(client_args={"api_key": c.key}, model_id=c.model, max_tokens=8000, params={"temperature": c.temperature})
    raise ModelError(f"unknown provider {c.provider!r}")


def with_rotation(role: str, call: Callable[[Any, Candidate], Any], vision: bool = False) -> Any:
    """Run `call(model, candidate)` with the role's candidates in turn until one works."""
    tried = candidates(role, vision)
    if not tried:
        names = ", ".join(sorted({ENV_NAMES.get(roles()[role].provider, "?")})) if role in roles() else "?"
        raise ModelError(f"no usable API key for the {role} agent: set {names} in studio/agents/.env or studio/.env "
                         "(never paste keys into chat), or wait a minute if all keys hit their rate limit")
    errors: list[str] = []
    for i, c in enumerate(tried):
        try:
            result = _call_once_more_on_blip(call, c)
        except Exception as e:
            again, rest = retryable(e)
            if not again:
                raise
            if rest:
                _cooling[(c.provider, c.model, c.key)] = time.monotonic() + COOLDOWN_S
            errors.append(f"{c.label()}: {str(e)[:160]}")
            log.warning("model call failed on %s (%s); trying the next key or model", c.label(), str(e)[:200])
            if i + 1 < len(tried):
                rotation_log.append({"role": role, "failed": c.label(), "next": tried[i + 1].label(), "error": str(e)[:200]})
            continue
        _preferred[c.provider] = c.key_index
        return result
    raise ModelError(f"every key and fallback failed for the {role} agent: " + " | ".join(errors[-4:]))


def _blip(e: BaseException) -> bool:
    text = f"{e}".lower()
    return any(s in text for s in ("503", "unavailable", "high demand", "overloaded")) and "quota" not in text


def _call_once_more_on_blip(call: Callable[[Any, Candidate], Any], c: Candidate) -> Any:
    try:
        return call(build_candidate(c), c)
    except Exception as e:
        if not _blip(e):
            raise
        log.info("%s is busy; one more try in %.0fs", c.label(), BLIP_RETRY_S)
        time.sleep(BLIP_RETRY_S)
        return call(build_candidate(c), c)


def no_retry_strategy():
    """Strands' own retry waits up to minutes on throttling; rotation handles it instead (one attempt per call)."""
    from strands import ModelRetryStrategy
    return ModelRetryStrategy(max_attempts=1)


def build(role: str):
    """A Strands model for an agent role, with its first usable key (no rotation; prefer `with_rotation`)."""
    cs = candidates(role)
    if not cs:
        raise ModelError(f"no usable API key for the {role} agent: add one to studio/agents/.env or studio/.env")
    return build_candidate(cs[0])


def image_block(png: Path) -> dict:
    return {"image": {"format": "png", "source": {"bytes": png.read_bytes()}}}


# ── structured asks ─────────────────────────────────────────────────────────────────────────────────

Backend = Callable[[str, str, str, type, list[Path], list[Any]], Any]


def _strands_backend(role: str, system: str, prompt: str, output: type[T], images: list[Path], tools: list[Any]) -> T:
    from strands import Agent

    content: list[dict] = [{"text": prompt}] + [image_block(p) for p in images]

    def call(model: Any, c: Candidate) -> T:
        agent = Agent(model=model, system_prompt=system, tools=list(tools), callback_handler=None, retry_strategy=no_retry_strategy())
        result = agent(content, structured_output_model=output)
        out = result.structured_output
        if out is None:
            raise ModelError(f"{role}: the model gave no structured answer")
        _record_usage(role, c, result)
        return out

    return with_rotation(role, call, vision=bool(images))


_backend: Backend = _strands_backend
usage_log: list[dict] = []


def _record_usage(role: str, c: Candidate, result: Any) -> None:
    try:
        u = result.metrics.accumulated_usage
        usage_log.append({"role": role, "model": f"{c.provider}/{c.model}", "input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)})
    except AttributeError:
        pass


def set_backend(fn: Backend | None) -> None:
    """Tests: replace the model call (None restores the real one)."""
    global _backend
    _backend = fn or _strands_backend


def ask(role: str, system: str, prompt: str, output: type[T], images: list[Path] = (), tools: list[Any] = ()) -> T:
    """One structured answer from the role's model (validated against `output`)."""
    return _backend(role, system, prompt, output, list(images), list(tools))


# ── discovery ───────────────────────────────────────────────────────────────────────────────────────

def list_models() -> dict[str, list[str]]:
    """Models each provider offers, per key (checks every key; prints no key)."""
    out: dict[str, list[str]] = {}
    for i, key in enumerate(keys("gemini")):
        try:
            from google import genai
            names = sorted(m.name.removeprefix("models/") for m in genai.Client(api_key=key).models.list()
                           if "generateContent" in (m.supported_actions or []))
            out[f"gemini key {i + 1}: ok"] = names
        except Exception as e:
            out[f"gemini key {i + 1}: FAILED {str(e)[:120]}"] = []
    for i, key in enumerate(keys("groq")):
        try:
            from openai import OpenAI
            out[f"groq key {i + 1}: ok"] = sorted(m.id for m in OpenAI(api_key=key, base_url=GROQ_BASE_URL).models.list().data)
        except Exception as e:
            out[f"groq key {i + 1}: FAILED {str(e)[:120]}"] = []
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="check every key and list the models it can use")
    a = p.parse_args()
    if a.list:
        found = list_models()
        if not found:
            print("no keys set: add GEMINI_API_KEY / GROQ_API_KEY to studio/agents/.env or studio/.env")
        for what, names in found.items():
            print(f"{what} ({len(names)} models)\n  " + "\n  ".join(names))
    for r in roles().values():
        print(f"role {r.name:10} → {r.provider}/{r.model}")
