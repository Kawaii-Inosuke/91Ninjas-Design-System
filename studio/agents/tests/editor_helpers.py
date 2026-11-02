"""Place a layered SVG in the editor headlessly (vectorcraft-cli run) and read back what it built.

Headless counterpart of `Bridge.place_svg` (the live app is tested in test_bridge_live.py): a new RGB
document the size of the design, place, then move the design so its `background` sits at (0, 0) if it
doesn't already (since the Phase 2 engine fix it does).
"""
from __future__ import annotations

import json
import subprocess
from base64 import b64encode
from pathlib import Path

EDITOR_CLI = Path(__file__).resolve().parents[2] / "editor" / "target" / "debug" / "vectorcraft-cli"


def _run(steps: list[dict], workdir: Path, exports: list[str] = ()) -> list[dict]:
    (workdir / "steps.json").write_text(json.dumps(steps))
    cmd = [str(EDITOR_CLI), "run", "--commands", "steps.json"]
    for e in exports:
        cmd += ["--export", e]
    out = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=120, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def find(node: dict, name: str) -> dict | None:
    if node.get("name") == name:
        return node
    for c in node.get("children", []):
        if hit := find(c, name):
            return hit
    return None


def place(svg: str, width: float, height: float, workdir: Path, *, name: str = "design",
          export_png: str | None = None) -> dict:
    """Returns the editor's document.inspect result after placing (and positioning) the design."""
    new = {"command": "file.new", "params": {"width": width, "height": height, "units": "Pixels", "colorMode": "rgb"}}
    put = {"command": "file.place", "params": {"name": name, "dataBase64": b64encode(svg.encode()).decode()}}
    inspect = {"command": "document.inspect", "params": {}}
    first = _run([new, put, inspect], workdir)
    placed_id = first[1]["result"]["ids"][0]
    bg = find(first[2]["result"]["layers"][0], "background")["bounds"]
    move = {"command": "object.move", "params": {"ids": [placed_id], "dx": -bg["x"], "dy": -bg["y"]}}
    steps = [new, put] + ([move] if abs(bg["x"]) > 0.01 or abs(bg["y"]) > 0.01 else []) + [inspect]
    result = _run(steps, workdir, [export_png] if export_png else [])
    return next(r["result"] for r in result if r.get("command") == "document.inspect")
