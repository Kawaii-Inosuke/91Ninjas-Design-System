"""A design run: the designer's steps, driven in code, with AI agents doing the thinking at each step (D-017).

    brief → similar-design check (→ [generate again?]) → layout → [outline approval] → render ⇄ critic (≤ 3 rounds) → layered SVG
    → place in the editor (when it is open) → [final approval] → export → record in history

Approvals are pauses: the run waits (no time limit) until the designer decides through `RunManager.decide`.
Every step emits events (`Run.events`); the sidecar streams them to the panel. Chat edits on a placed design
go to the Canvas Editor agent (`RunManager.message`).
"""
from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from studio_agents import brand as brandmod
from studio_agents import config, models, steps
from studio_agents.bridge import Bridge, BridgeError
from studio_agents.history import History
from studio_agents.render.build import RenderResult, render
from studio_agents.render.layered_svg import layered_svg
from studio_agents.render.spec import Spec

GATES = {"similar_exists": ("generate", "abandon"), "outline": ("approve", "change", "abandon"),
         "final": ("approve", "change", "abandon")}
TERMINAL = {"done", "abandoned", "failed", "not_usable"}


class RunError(RuntimeError):
    pass


@dataclass
class Run:
    id: str
    client: str
    content: str
    images: list[str]
    instruction: str
    dir: Path
    status: str = "running"
    stage: str = "starting"
    brief: dict | None = None
    spec: dict | None = None
    history_id: str | None = None
    versions: list[dict] = field(default_factory=list)
    best: int | None = None
    placed: dict | None = None
    final_files: dict = field(default_factory=dict)
    gate: dict | None = None
    events: list[dict] = field(default_factory=list)
    _decision: dict | None = None
    _wake: threading.Condition = field(default_factory=threading.Condition)

    def public(self) -> dict:
        keys = ("id", "client", "status", "stage", "brief", "spec", "history_id", "versions", "best", "placed", "final_files", "gate")
        return {k: getattr(self, k) for k in keys}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "design"


class RunManager:
    def __init__(self, history: History | None = None, bridge_factory: Callable[[], Bridge | None] | None = None):
        self.history = history or History()
        self.bridge_factory = bridge_factory or _connect_bridge
        self.runs: dict[str, Run] = {}
        self.editors: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ── public API ──────────────────────────────────────────────────────────────────────────────────
    def start(self, client: str, content: str = "", images: list[Path] = (), instruction: str = "",
              run_async: bool = True) -> Run:
        brandmod.load(client)  # fails early when the client isn't installed
        if not content.strip() and not images:
            raise RunError("give some content: text or an image")
        rid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        run = Run(rid, client, content, [str(p) for p in images], instruction, config.runs_dir() / rid)
        run.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.runs[rid] = run
        if run_async:
            threading.Thread(target=self._guarded, args=(run,), name=f"run-{rid}", daemon=True).start()
        else:
            self._guarded(run)
        return run

    def get(self, rid: str) -> Run:
        run = self.runs.get(rid)
        if not run:
            raise RunError(f"no run {rid}")
        return run

    def decide(self, rid: str, gate: str, decision: str, notes: str = "") -> None:
        run = self.get(rid)
        with run._wake:
            if not run.gate or run.gate["kind"] != gate:
                raise RunError(f"run {rid} is not waiting for {gate!r}")
            if decision not in GATES[gate]:
                raise RunError(f"{gate}: decide one of {', '.join(GATES[gate])}")
            run._decision = {"decision": decision, "notes": notes}
            run.gate, run.status = None, "running"   # cleared now: callers waiting for the next gate don't see this one
            run._wake.notify_all()

    def message(self, rid: str, text: str) -> str:
        """A chat request on the placed design → the Canvas Editor agent edits it (one undo step)."""
        run = self.get(rid)
        if not run.placed:
            raise RunError("the design isn't in the editor yet")
        editor = self.editors.get(rid)
        if editor is None:
            from studio_agents.editor_agent import CanvasEditor
            bridge = self.bridge_factory()
            if not bridge:
                raise RunError("the editor isn't connected")
            editor = self.editors[rid] = CanvasEditor(bridge, run.placed["group_id"], brandmod.load(run.client))
        self._emit(run, "message", {"from": "designer", "text": text})
        reply = editor.ask(text)
        self._emit(run, "message", {"from": "editor", "text": reply})
        return reply

    def wait(self, rid: str, timeout: float = 30) -> Run:
        """Tests: block until the run pauses at a gate or ends."""
        run = self.get(rid)
        with run._wake:
            run._wake.wait_for(lambda: run.gate is not None or run.status in TERMINAL, timeout)
        return run

    # ── internals ───────────────────────────────────────────────────────────────────────────────────
    def _emit(self, run: Run, kind: str, data: Any = None) -> None:
        with run._wake:
            run.events.append({"seq": len(run.events), "type": kind, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "data": data})
            run._wake.notify_all()
        (run.dir / "run.json").write_text(json.dumps(run.public() | {"events": run.events}, indent=1, default=str))

    def _stage(self, run: Run, stage: str, note: str = "") -> None:
        # A key or model that failed and what took over (D-027), told once.
        while models.rotation_log:
            self._emit(run, "rotation", models.rotation_log.pop(0))
        run.stage = stage
        self._emit(run, "stage", {"stage": stage, "note": note})

    def _gate(self, run: Run, kind: str, payload: dict) -> dict:
        """Pause until the designer decides."""
        with run._wake:
            run.gate, run._decision = {"kind": kind, "options": list(GATES[kind]), "payload": payload}, None
            run.status = "waiting"
        self._emit(run, "gate", run.gate)
        with run._wake:
            run._wake.wait_for(lambda: run._decision is not None)
            decision, run.gate, run.status = run._decision, None, "running"
        self._emit(run, "decision", {"gate": kind, **decision})
        return decision

    def _finish(self, run: Run, status: str, data: Any = None) -> None:
        run.status = status
        self._emit(run, "done", {"status": status, **(data or {})})

    def _guarded(self, run: Run) -> None:
        try:
            self._run(run)
        except Exception as e:  # a run must end with a reason, never hang
            if run.history_id and run.status != "done":
                self.history.release(run.history_id)
            self._emit(run, "error", {"error": str(e), "trace": traceback.format_exc()[-2000:]})
            self._finish(run, "failed", {"error": str(e)})

    def _run(self, run: Run) -> None:
        brand = brandmod.load(run.client)
        avoid = self.history.recent_layouts(run.client, brand.layout_window)

        self._stage(run, "brief", "reading the content")
        brief = steps.write_brief(brand, run.content, [Path(p) for p in run.images], run.instruction, avoid)
        run.brief = brief.model_dump()
        self._emit(run, "brief", run.brief)
        if not brief.usable:
            return self._finish(run, "not_usable", {"reason": brief.reason_if_not})

        self._stage(run, "check", "looking for similar designs")
        check = self.history.check(run.client, brief.topic, brief.angle, brand.layout_window)
        self._emit(run, "check", check.to_json())
        # D-020: never blocked. Show what exists and where, and let the designer decide.
        if check.verdict == "similar" and self._gate(run, "similar_exists", check.to_json())["decision"] == "abandon":
            return self._finish(run, "abandoned")
        run.history_id = self.history.reserve(run.client, brief.topic, brief.angle, brief.key_points,
                                              brief.suggested_layout or None, brand.version)
        avoid = check.avoid_layouts

        self._stage(run, "outline", "designing the layout")
        spec = steps.design_spec(brand, brief, avoid)
        while True:
            run.spec = spec.model_dump(exclude_none=True)
            d = self._gate(run, "outline", {"spec": run.spec})
            if d["decision"] == "approve":
                break
            if d["decision"] == "abandon":
                self.history.release(run.history_id)
                return self._finish(run, "abandoned")
            self._stage(run, "outline", "applying your notes")
            spec = steps.design_spec(brand, brief, avoid, notes=d["notes"], previous=spec)

        while True:
            best = self._render_and_critique(run, brand, brief, spec, avoid)
            self._place(run, best)
            d = self._gate(run, "final", {"version": run.best, "png": str(best.png), "preview": str(best.preview),
                                          "unresolved": run.versions[-1].get("fails", []), "placed": run.placed})
            if d["decision"] == "abandon":
                self.history.release(run.history_id)
                return self._finish(run, "abandoned")
            if d["decision"] == "approve":
                return self._approve(run, best)
            self._stage(run, "revise", "applying your notes")
            spec = steps.design_spec(brand, brief, avoid, notes=d["notes"], previous=Spec(**run.spec))

    def _render_and_critique(self, run: Run, brand: brandmod.Brand, brief: steps.Brief, spec: Spec, avoid: list[str]) -> RenderResult:
        results: list[tuple[int, RenderResult, Spec]] = []
        for round_ in range(config.MAX_REVISIONS + 1):
            self._stage(run, "render", f"drawing version {len(run.versions) + 1}")
            r = render(spec, brand, run.dir / "versions")
            self._stage(run, "critic", "checking it against the brand rules")
            report = steps.critique(brand, spec, r)
            entry = {"version": r.version, "png": str(r.png), "preview": str(r.preview), "auto_checks": r.auto_checks,
                     "passes": report.passes, "fails": [f.model_dump() for f in report.fails]}
            run.versions.append(entry)
            self._emit(run, "version", entry)
            results.append((len(report.fails), r, spec))
            if report.passes or round_ == config.MAX_REVISIONS:
                break
            self._stage(run, "fix", f"fixing {len(report.fails)} finding(s)")
            spec = steps.design_spec(brand, brief, avoid, previous=spec, findings=report.fails)
        _, best, best_spec = min(results, key=lambda t: t[0])
        run.best, run.spec = best.version, best_spec.model_dump(exclude_none=True)
        return best

    def _place(self, run: Run, best: RenderResult) -> None:
        self._stage(run, "place", "putting it on an artboard")
        name = f"design-{slug(run.spec.get('title', 'design'))}"
        ls = layered_svg(best.html, name=name)
        svg_path = run.dir / f"v{best.version}-layered.svg"
        svg_path.write_text(ls.svg)
        bridge = self.bridge_factory()
        if not bridge:
            run.placed = None
            self._emit(run, "placed", {"editor": False, "svg": str(svg_path), "note": "editor not open: files are ready"})
            return
        try:
            p = bridge.place_svg(ls.svg, name=name, width=ls.width, height=ls.height)
        except BridgeError as e:
            run.placed = None
            self._emit(run, "placed", {"editor": False, "svg": str(svg_path), "note": f"could not place: {e.error}"})
            return
        run.placed = {"group_id": p.group_id, "layers": len(p.nodes), "svg": str(svg_path)}
        self.editors.pop(run.id, None)
        self._emit(run, "placed", {"editor": True, **run.placed})

    def _approve(self, run: Run, best: RenderResult) -> None:
        self._stage(run, "export", "exporting the approved design")
        final = run.dir / "final"
        final.mkdir(exist_ok=True)
        base = slug(run.spec.get("title", "design"))
        bridge = self.bridge_factory() if run.placed else None
        files: dict[str, str] = {}
        if bridge:  # the designer's edits in the editor are part of the approved design
            for fmt in ("svg", "png", "pdf"):
                try:
                    (final / f"{base}.{fmt}").write_bytes(bridge.export(fmt))
                    files[fmt] = str(final / f"{base}.{fmt}")
                except BridgeError as e:
                    self._emit(run, "warning", {"export": fmt, "error": e.error})
        if "png" not in files:
            (final / f"{base}.png").write_bytes(best.png.read_bytes())
            files["png"] = str(final / f"{base}.png")
        if "svg" not in files:
            (final / f"{base}.svg").write_text(Path(run.placed["svg"] if run.placed else run.dir / f"v{best.version}-layered.svg").read_text())
            files["svg"] = str(final / f"{base}.svg")
        run.final_files = files
        rec = self.history.approve(run.history_id, Path(files["png"]), files, layout=run.spec.get("layout"))
        self._finish(run, "done", {"files": files, "looks_like": rec.get("visual_duplicates", [])})


def _connect_bridge() -> Bridge | None:
    try:
        return Bridge.connect()
    except BridgeError:
        return None
