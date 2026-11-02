"""Model layer (D-003): `models.yaml` maps each agent role to a provider and model; keys come from `.env`.

    uv run python -m studio_agents.models --list     # the model ids your keys can use

Agents never hard-code a model. `ask()` is the one way steps call a model for a structured answer, so tests
can swap in a fake (`set_backend`).
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "models.yaml"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
T = TypeVar("T", bound=BaseModel)

load_dotenv(ROOT / ".env")


class ModelError(RuntimeError):
    pass


@dataclass
class Role:
    name: str
    provider: str
    model: str
    temperature: float = 0.3


def roles() -> dict[str, Role]:
    data = yaml.safe_load(CONFIG.read_text()) or {}
    return {k: Role(k, v["provider"], v["model"], float(v.get("temperature", 0.3))) for k, v in data.get("roles", {}).items()}


def _key(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise ModelError(f"{name} is not set: add it to {ROOT / '.env'} (never paste keys into chat)")
    return v


def build(role: str):
    """A Strands model for an agent role."""
    r = roles().get(role)
    if not r:
        raise ModelError(f"no role {role!r} in {CONFIG}")
    if r.provider == "gemini":
        from strands.models.gemini import GeminiModel
        return GeminiModel(client_args={"api_key": _key("GEMINI_API_KEY")}, model_id=r.model,
                           params={"temperature": r.temperature})
    if r.provider == "groq":
        from strands.models.openai import OpenAIModel
        return OpenAIModel(client_args={"api_key": _key("GROQ_API_KEY"), "base_url": GROQ_BASE_URL}, model_id=r.model,
                           params={"temperature": r.temperature})
    if r.provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(client_args={"api_key": _key("ANTHROPIC_API_KEY")}, model_id=r.model, max_tokens=8000,
                              params={"temperature": r.temperature})
    raise ModelError(f"unknown provider {r.provider!r} for role {role!r}")


def image_block(png: Path) -> dict:
    return {"image": {"format": "png", "source": {"bytes": png.read_bytes()}}}


# ── structured asks ─────────────────────────────────────────────────────────────────────────────────

Backend = Callable[[str, str, str, type, list[Path], list[Any]], Any]


def _strands_backend(role: str, system: str, prompt: str, output: type[T], images: list[Path], tools: list[Any]) -> T:
    from strands import Agent
    agent = Agent(model=build(role), system_prompt=system, tools=list(tools), callback_handler=None)
    content: list[dict] = [{"text": prompt}] + [image_block(p) for p in images]
    result = agent(content, structured_output_model=output)
    out = result.structured_output
    if out is None:
        raise ModelError(f"{role}: the model gave no structured answer")
    _record_usage(role, result)
    return out


_backend: Backend = _strands_backend
usage_log: list[dict] = []


def _record_usage(role: str, result: Any) -> None:
    try:
        u = result.metrics.accumulated_usage
        usage_log.append({"role": role, "input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)})
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
    out: dict[str, list[str]] = {}
    if os.environ.get("GEMINI_API_KEY"):
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        out["gemini"] = sorted(m.name.removeprefix("models/") for m in client.models.list()
                               if "generateContent" in (m.supported_actions or []))
    if os.environ.get("GROQ_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL)
        out["groq"] = sorted(m.id for m in client.models.list().data)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="list the models your keys can use")
    a = p.parse_args()
    if a.list:
        found = list_models()
        if not found:
            print(f"no keys set: add GEMINI_API_KEY / GROQ_API_KEY to {ROOT / '.env'}")
        for prov, names in found.items():
            print(f"{prov} ({len(names)}):\n  " + "\n  ".join(names))
    for r in roles().values():
        print(f"role {r.name:10} → {r.provider}/{r.model}")
