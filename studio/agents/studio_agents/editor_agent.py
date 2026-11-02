"""Canvas Editor agent: applies the designer's chat requests to the placed design through the bridge.
Each request becomes one batch, so one undo step in the editor (D-015)."""
from __future__ import annotations

import json
from typing import Any

from strands import Agent, tool

from studio_agents import models
from studio_agents.brand import Brand
from studio_agents.bridge import Bridge, BridgeError
from studio_agents.steps import prompt

MAX_COMMANDS = 40


def _layers(node: dict, out: list[dict], depth: int = 0) -> None:
    entry = {"name": node.get("name"), "id": node.get("id"), "kind": node.get("kind"), "depth": depth}
    if node.get("kind") == "Type":
        entry["text"] = (node.get("text") or "")[:120]
    if b := node.get("bounds"):
        entry["bounds"] = {k: round(v) for k, v in b.items()}
    if node.get("fill") and node.get("fill") != "None":
        entry["fill"] = node["fill"]
    out.append(entry)
    for c in node.get("children", []):
        _layers(c, out, depth + 1)


class CanvasEditor:
    """One per design run; keeps the conversation so follow-ups ("a bit more") work."""

    def __init__(self, bridge: Bridge, group_id: int, brand: Brand):
        self.bridge, self.group_id, self.brand = bridge, group_id, brand
        self.applied: list[dict] = []

        @tool
        def inspect_design() -> str:
            """The design's layers: name, id, kind, text, bounds, fill. Call before editing."""
            out: list[dict] = []
            _layers(self.bridge.node(self.group_id), out)
            return json.dumps(out[:400])

        @tool
        def apply_edits(commands: list[dict[str, Any]], label: str) -> str:
            """Apply editor commands as ONE undo step. Each command: {"command": "...", "params": {...}}.

            Args:
                commands: the commands for this request, in order
                label: a short name for the undo step, e.g. "Bigger title"
            """
            if not commands or len(commands) > MAX_COMMANDS:
                return f"error: give 1-{MAX_COMMANDS} commands"
            try:
                calls = [{"command": c["command"], "params": c.get("params", {})} for c in commands]
                self.bridge.batch(calls, label[:60] or "AI edit")
            except (KeyError, TypeError):
                return "error: each command needs a `command` name and optional `params` object"
            except BridgeError as e:
                return f"error (nothing was changed): {e.error}" + (f" at command {e.failed_call}" if e.failed_call is not None else "")
            self.applied.append({"label": label, "commands": calls})
            return f"applied {len(calls)} command(s) as one undo step"

        palette = ", ".join(f"{k} {v}" for k, v in brand.palette().items())
        system = prompt("editor") + f"\n\nBrand colours you may use: {palette}. Fonts: {', '.join(brand.fonts())}."
        self.agent = Agent(model=models.build("editor"), system_prompt=system, tools=[inspect_design, apply_edits],
                           callback_handler=None, retry_strategy=models.no_retry_strategy())

    def ask(self, request: str) -> str:
        """The request through the editor role's keys and fallbacks (D-027): a failed attempt leaves no half turn behind."""
        def call(model, _candidate) -> str:
            start = len(self.agent.messages)
            self.agent.model = model
            try:
                return str(self.agent(request)).strip()
            except Exception:
                del self.agent.messages[start:]
                raise
        return models.with_rotation("editor", call)
