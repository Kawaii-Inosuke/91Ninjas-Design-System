"""Designs made with Claude (D-033): what the connector's tools act on. No AI here: Claude does the thinking
(brief, outline, layout, critique); this module renders specs, places them in the editor, edits them there,
and saves approved ones in the client's history.

A design has versions (each a rendered spec) and placements: where a version sits in the editor, by document
uid, group id and artboard (D-032), so tabs the designer switches to are never touched, and "is it still
there?" can be answered after the designer's Ctrl+Z. Everything is kept in `designs/<id>/design.json`.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from studio_agents import brand as brandmod
from studio_agents import config
from studio_agents.bridge import Bridge, BridgeError
from studio_agents.history import History
from studio_agents.render.build import render
from studio_agents.render.layered_svg import layered_svg
from studio_agents.render.spec import Spec

ARTBOARD_GAP = 80.0          # points between artboards of versions placed side by side
EXPORTS = ("svg", "png", "pdf")


class DesignError(ValueError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "design"


def designs_dir() -> Path:
    return config.data_dir() / "designs"


@dataclass
class Design:
    id: str
    client: str
    topic: str
    angle: str = ""
    key_points: list[str] = field(default_factory=list)
    title: str = ""
    status: str = "draft"                      # draft → approved | abandoned
    history_id: str | None = None
    versions: list[dict] = field(default_factory=list)      # {version, layout, title, png, preview, html, svg?, auto_checks}
    placements: dict[str, dict] = field(default_factory=dict)  # str(version) → {document, group_id, artboard}
    files: dict[str, str] = field(default_factory=dict)
    created: str = ""

    @property
    def dir(self) -> Path:
        return designs_dir() / self.id

    def version(self, v: int | None) -> dict:
        if not self.versions:
            raise DesignError(f"design {self.id} has no versions yet: render one first")
        if v is None:
            return self.versions[-1]
        for x in self.versions:
            if x["version"] == v:
                return x
        raise DesignError(f"design {self.id} has no version {v} (it has {', '.join(str(x['version']) for x in self.versions)})")

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "design.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=1))
        tmp.replace(self.dir / "design.json")

    def public(self) -> dict:
        return asdict(self) | {"folder": str(self.dir)}


def _connect() -> Bridge | None:
    try:
        return Bridge.connect()
    except BridgeError:
        return None


class Studio:
    """The connector's back end: designs, rendering, the editor and the history, with no model calls."""

    def __init__(self, history: History | None = None, bridge_factory: Callable[[], Bridge | None] = _connect):
        self._history = history
        self.bridge_factory = bridge_factory

    @property
    def history(self) -> History:
        if self._history is None:
            self._history = History()
        return self._history

    # ── designs ─────────────────────────────────────────────────────────────────────────────────────
    def create(self, client: str, topic: str, angle: str = "", key_points: list[str] = ()) -> Design:
        """A new design for `client` (reserved in the history, so similar-topic checks see it)."""
        b = brandmod.load(client)
        if not topic.strip():
            raise DesignError("give the design a topic")
        did = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = Design(did, client, topic.strip(), angle.strip(), list(key_points), created=datetime.now(timezone.utc).isoformat())
        d.history_id = self.history.reserve(client, d.topic, d.angle, d.key_points, None, b.version)
        d.save()
        return d

    def get(self, did: str) -> Design:
        p = designs_dir() / did / "design.json"
        if not re.fullmatch(r"[0-9A-Za-z-]{6,64}", did) or not p.is_file():
            raise DesignError(f"no design {did!r}: list_designs shows the ones there are")
        return Design(**json.loads(p.read_text()))

    def list(self, client: str | None = None, limit: int = 30) -> list[Design]:
        root = designs_dir()
        out = []
        for p in sorted(root.glob("*/design.json"), reverse=True) if root.is_dir() else []:
            try:
                d = Design(**json.loads(p.read_text()))
            except (OSError, ValueError, TypeError):
                continue
            if client is None or d.client == client:
                out.append(d)
            if len(out) >= limit:
                break
        return out

    def render(self, did: str, spec: dict[str, Any]) -> tuple[Design, dict]:
        """Draw a spec in the client's brand → a new version (PNG preview, machine checks)."""
        d = self.get(did)
        if d.status != "draft":
            raise DesignError(f"design {did} is {d.status}; start a new design for new work")
        try:
            sp = Spec.model_validate(spec)
        except ValueError as e:
            raise DesignError(f"the spec is invalid: {e}") from None
        r = render(sp, brandmod.load(d.client), d.dir / "versions")
        entry = {"version": r.version, "layout": sp.layout, "title": sp.title, "spec": sp.model_dump(exclude_none=True),
                 "png": str(r.png), "preview": str(r.preview), "html": str(r.html), "width": r.width, "height": r.height,
                 "auto_checks": r.auto_checks, "missing_icons": r.missing_icons}
        d.versions.append(entry)
        d.title = sp.title
        d.save()
        return d, entry

    # ── the editor ──────────────────────────────────────────────────────────────────────────────────
    def _bridge(self) -> Bridge:
        b = self.bridge_factory()
        if b is None:
            raise DesignError("91Ninjas Studio isn't open (or its AI connection is off): open the app, then try again")
        return b

    def _layered(self, d: Design, v: dict) -> tuple[str, float, float, str]:
        name = f"design-{slug(v['title'])}-v{v['version']}"
        ls = layered_svg(Path(v["html"]), name=name)
        svg_path = d.dir / f"v{v['version']}-layered.svg"
        svg_path.write_text(ls.svg)
        v["svg"] = str(svg_path)
        return ls.svg, ls.width, ls.height, name

    def place(self, did: str, version: int | None = None, beside: bool = False, new_file: bool = False) -> dict:
        """Put a version on an artboard as editable layers (D-036):
        - default: in the file the designer has in front, on its empty artboard or a new artboard to the right of
          what's there; a new file only when none is open, or the one in front is CMYK with art in it;
        - `beside`: next to this design's other versions, in their file (to compare versions);
        - `new_file`: in a new file of its own (only when the designer asks for one)."""
        d = self.get(did)
        v = d.version(version)
        svg, w, h, name = self._layered(d, v)
        bridge = self._bridge()
        label = f"{v['title']} · v{v['version']}" if beside else v["title"]
        if beside:
            doc = next((p["document"] for p in d.placements.values() if p.get("document") is not None and bridge.in_document(p["document"]).is_open()), None)
            if doc is None:
                raise DesignError("none of this design's versions is open in the editor: place one without `beside` first")
        elif new_file:
            doc = None
        else:
            doc = self._current_document(bridge)
        if doc is None:
            b = bridge
            p = b.place_svg(svg, name=name, width=w, height=h, document_name=f"design-{slug(v['title'])}")
            artboard = 0
            b.name_artboard(0, label[:120])
        else:
            b = bridge.in_document(doc)
            artboard, x, y = self._next_artboard(b, w, h, label[:120])
            p = b.place_svg(svg, name=name, width=w, height=h, new_document=False, origin=(x, y))
        d.placements[str(v["version"])] = {"document": p.document, "group_id": p.group_id, "artboard": artboard, "layers": len(p.nodes)}
        d.save()
        return {"design": d.id, "version": v["version"], "document": p.document, "group_id": p.group_id,
                "artboard": artboard, "layers": len(p.nodes), "new_file": doc is None}

    @staticmethod
    def _empty(info: dict) -> bool:
        return not any(layer.get("children") for layer in info.get("layers", []))

    def _current_document(self, bridge: Bridge) -> int | None:
        """The file in front, if a design can go into it: RGB, or still empty (then it is switched to RGB, since
        brand colours are RGB hex, D-014). A CMYK file with art in it is left alone: the design gets a new file."""
        front = next((d["uid"] for d in bridge.documents() if d.get("active")), None)
        if front is None:
            return None
        b = bridge.in_document(front)
        info = b.inspect(depth=1)
        if str(info.get("colorMode", "")).lower().startswith("rgb"):
            return front
        if self._empty(info):
            b.call("file.documentColorMode", mode="rgb")
            return front
        return None

    def _next_artboard(self, b: Bridge, w: float, h: float, name: str) -> tuple[int, float, float]:
        """An artboard for the design: the file's only artboard if nothing is on it yet (resized to the design),
        else a new one to the right of the others → (index, x, y)."""
        info = b.inspect(depth=1)
        boards = info.get("artboards", [])
        if len(boards) == 1 and self._empty(info):
            a = boards[0]
            b.call("artboard.setProps", artboards=[{"index": 0, "x": a["x"], "y": a["y"], "width": w, "height": h, "name": name}])
            return 0, a["x"], a["y"]
        x = max((a["x"] + a["width"] for a in boards), default=0.0) + ARTBOARD_GAP
        y = min((a["y"] for a in boards), default=0.0)
        return b.add_artboard(x, y, w, h, name), x, y

    def current_file(self) -> dict:
        """The file in front and the designs placed in it (what "this", "it", "the last one" refer to)."""
        bridge = self._bridge()
        docs = bridge.documents()
        front = next((d for d in docs if d.get("active")), None)
        out: dict[str, Any] = {"file": front, "open_files": docs, "designs": []}
        if front is None:
            return out
        for d in self.list(limit=200):
            versions = [int(k) for k, p in d.placements.items() if p.get("document") == front["uid"]]
            if versions:
                out["designs"].append({"design": d.id, "title": d.title, "client": d.client, "status": d.status, "versions_here": versions})
        return out

    def _placed(self, d: Design, version: int | None) -> tuple[Bridge, dict, str]:
        key = str(version) if version is not None else next(reversed(d.placements), None)
        if key is None or key not in d.placements:
            raise DesignError(f"version {version} of design {d.id} isn't in the editor: place it first")
        p = d.placements[key]
        return self._bridge().in_document(p["document"]), p, key

    def remove(self, did: str, versions: list[int], label: str = "Remove versions") -> dict:
        """Take versions out of the editor (their art and artboards) in ONE undo step; their files stay."""
        d = self.get(did)
        groups, boards, gone = [], [], []
        doc = None
        for v in versions:
            p = d.placements.get(str(v))
            if not p:
                continue
            if doc is not None and p["document"] != doc:
                raise DesignError("those versions are in different documents: remove them one document at a time")
            doc = p["document"]
            groups.append(p["group_id"])
            gone.append(str(v))
        if doc is None:
            return {"removed": []}
        b = self._bridge().in_document(doc)
        if not b.is_open():
            raise DesignError("that document was closed")
        boards = self._artboards_of(b, groups)
        b.remove(groups, boards, label)
        for k in gone:
            d.placements.pop(k, None)
        d.save()
        return {"removed": [int(k) for k in gone]}

    @staticmethod
    def _artboards_of(b: Bridge, groups: list[int]) -> list[int]:
        """The artboards the groups sit on (by their centre), never every artboard of the document."""
        boards = b.inspect().get("artboards", [])
        out = set()
        for g in groups:
            try:
                bb = b.node(g).get("bounds") or {}
            except BridgeError:
                continue
            cx, cy = bb.get("x", 0) + bb.get("width", 0) / 2, bb.get("y", 0) + bb.get("height", 0) / 2
            for i, a in enumerate(boards):
                if a["x"] <= cx <= a["x"] + a["width"] and a["y"] <= cy <= a["y"] + a["height"]:
                    out.add(i)
        return sorted(out)[: max(0, len(boards) - 1)]   # a document keeps one artboard

    def look(self, did: str | None = None, preview: bool = True) -> tuple[dict, bytes | None]:
        """What is in the editor now: the open documents and, for a design, whether each placed version is still
        there (the designer may have undone it, deleted it or closed its tab) and its layers. With `preview`, a
        PNG of the design's artboard as the editor draws it, the designer's hand edits included."""
        bridge = self._bridge()
        state: dict[str, Any] = {"documents": bridge.documents()}
        png = None
        if did:
            d = self.get(did)
            open_docs = {x["uid"] for x in state["documents"]}
            versions = []
            for k, p in d.placements.items():
                entry = {"version": int(k), "document": p["document"], "document_open": p["document"] in open_docs}
                if entry["document_open"]:
                    b = bridge.in_document(p["document"])
                    try:
                        node = b.node(p["group_id"])
                        entry["on_artboard"] = True
                        entry["layers"] = _layers(node)
                    except BridgeError:
                        entry["on_artboard"] = False   # undone or deleted in the editor
                    if entry.get("on_artboard") and preview and png is None:
                        idx = (self._artboards_of(b, [p["group_id"]]) or [0])[0]
                        png = b.render_preview(artboard=idx, scale=0.5)
                versions.append(entry)
            state["design"] = {"id": d.id, "status": d.status, "versions_made": [v["version"] for v in d.versions],
                               "placed": versions}
        return state, png

    def edit(self, did: str, commands: list[dict], label: str, version: int | None = None) -> dict:
        """Editor commands on a placed version as ONE undo step (all or nothing)."""
        if not commands or len(commands) > 60:
            raise DesignError("give 1–60 commands")
        d = self.get(did)
        b, _, key = self._placed(d, version)
        calls = []
        for c in commands:
            if not isinstance(c, dict) or not isinstance(c.get("command"), str):
                raise DesignError("each command is {\"command\": \"...\", \"params\": {...}}")
            calls.append({"command": c["command"], "params": c.get("params") or {}})
        try:
            results = b.batch(calls, label[:60] or "Claude edit")
        except BridgeError as e:
            raise DesignError(f"nothing was changed: {e.error}" + (f" (at command {e.failed_call})" if e.failed_call is not None else "")) from None
        return {"applied": len(calls), "version": int(key), "results": results}

    # ── finishing ───────────────────────────────────────────────────────────────────────────────────
    def approve(self, did: str, version: int | None = None) -> dict:
        """The designer approved: export the version as it is in the editor (their edits included) to SVG, PNG and
        PDF, or the rendered files when it isn't in the editor, and record it in the client's history."""
        d = self.get(did)
        if d.status != "draft":
            raise DesignError(f"design {did} is already {d.status}")
        v = d.version(version if version is not None else (int(next(reversed(d.placements))) if d.placements else None))
        final = d.dir / "final"
        final.mkdir(parents=True, exist_ok=True)
        base = slug(v["title"])
        files: dict[str, str] = {}
        p = d.placements.get(str(v["version"]))
        if p:
            b = self._bridge().in_document(p["document"])
            if b.is_open():
                for fmt in EXPORTS:
                    try:
                        (final / f"{base}.{fmt}").write_bytes(b.export(fmt))
                        files[fmt] = str(final / f"{base}.{fmt}")
                    except BridgeError:
                        continue
        if "png" not in files:
            (final / f"{base}.png").write_bytes(Path(v["png"]).read_bytes())
            files["png"] = str(final / f"{base}.png")
        if "svg" not in files:
            if not v.get("svg"):
                self._layered(d, v)
            (final / f"{base}.svg").write_text(Path(v["svg"]).read_text())
            files["svg"] = str(final / f"{base}.svg")
        rec = self.history.approve(d.history_id, Path(files["png"]), files, layout=v["layout"]) if d.history_id else {}
        d.status, d.files = "approved", files
        d.save()
        return {"files": files, "looks_like": rec.get("visual_duplicates", [])}

    def abandon(self, did: str) -> None:
        d = self.get(did)
        if d.status == "draft":
            if d.history_id:
                self.history.release(d.history_id)
            d.status = "abandoned"
            d.save()


def _layers(node: dict, depth: int = 0, out: list | None = None) -> list[dict]:
    """A design's layers, flat: name, id, kind, text, bounds (what Claude needs to edit it)."""
    out = [] if out is None else out
    if len(out) >= 300:
        return out
    e: dict[str, Any] = {"name": node.get("name"), "id": node.get("id"), "kind": node.get("kind"), "depth": depth}
    if node.get("kind") == "Type":
        e["text"] = (node.get("text") or "")[:120]
    if b := node.get("bounds"):
        e["bounds"] = {k: round(v) for k, v in b.items()}
    if node.get("fill") and node.get("fill") != "None":
        e["fill"] = node["fill"]
    out.append(e)
    for c in node.get("children", []):
        _layers(c, depth + 1, out)
    return out


def png_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
