"""Python client for the 91 Studio bridge (the editor's token-protected localhost API, D-002/D-008).

The editor writes `bridge.json` (port, token) when started with `STUDIO_BRIDGE=1`; this client reads it.

    b = Bridge.connect()
    placed = b.place_svg(svg, name="design-fcc-cac", width=1920, height=1080)
    with b.turn("Bigger title") as t:            # one AI turn = one undo step
        t.call("object.scale", ids=[placed.nodes["title"]], sx=120)
    b.undo()                                     # reverts the whole turn

Every edit goes through `/v1/batch`, so it is one undo step and is rolled back as a whole if any part
fails. The bridge refuses commands that touch files, scripts or plug-ins (SECURITY T4).
"""
from __future__ import annotations

import base64
import getpass
import http.client
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


class BridgeError(RuntimeError):
    def __init__(self, status: int, error: str, failed_call: int | None = None):
        super().__init__(f"bridge {status}: {error}" + (f" (call {failed_call})" if failed_call is not None else ""))
        self.status, self.error, self.failed_call = status, error, failed_call


def bridge_dir() -> Path:
    """Same rule as the editor (`vectorcraft_studio_bridge::bridge_dir`)."""
    if d := os.environ.get("STUDIO_BRIDGE_DIR"):
        return Path(d)
    if d := os.environ.get("XDG_RUNTIME_DIR"):
        return Path(d) / "91studio"
    return Path(tempfile.gettempdir()) / f"91studio-{os.environ.get('USER') or getpass.getuser()}"


@dataclass
class Placed:
    group_id: int
    nodes: dict[str, int] = field(default_factory=dict)     # layer name → node id, for the whole design
    warnings: list[str] = field(default_factory=list)


class Turn:
    """Calls collected for one AI turn; sent as one batch (one undo step) when the `with` block ends."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, command: str, **params: Any) -> str:
        """Queue a command; returns a reference ("$N") later calls can use, e.g. `ids=[ref + ".id"]`."""
        self.calls.append({"command": command, "params": params})
        return f"${len(self.calls) - 1}"


class Bridge:
    def __init__(self, port: int, token: str, timeout: float = 90.0):
        self.port, self.token, self.timeout = port, token, timeout

    @classmethod
    def connect(cls, info_file: Path | None = None) -> "Bridge":
        path = info_file or bridge_dir() / "bridge.json"
        try:
            info = json.loads(path.read_text())
        except FileNotFoundError:
            raise BridgeError(0, f"no bridge file at {path}: start the editor with STUDIO_BRIDGE=1") from None
        b = cls(int(info["port"]), str(info["token"]))
        b.health()
        return b

    # ── transport ───────────────────────────────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        payload = json.dumps(body).encode() if body is not None else b""
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                   "Content-Length": str(len(payload))}
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read() or b"null")
        except (ConnectionError, OSError) as e:
            raise BridgeError(0, f"cannot reach the editor on port {self.port}: {e}") from None
        finally:
            conn.close()
        if resp.status != 200 or not data.get("ok"):
            raise BridgeError(resp.status, str(data.get("error")), data.get("failedCall"))
        return data.get("result")

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def call(self, command: str, **params: Any) -> Any:
        """One engine command. Edits made this way are their own undo step; prefer `turn()` for edits."""
        return self._request("POST", "/v1/call", {"command": command, "params": params})

    def method(self, method: str, **params: Any) -> Any:
        """One control method (`ui.render`, `app.export`, `engine.commands`)."""
        return self._request("POST", "/v1/call", {"method": method, "params": params})

    def batch(self, calls: list[dict[str, Any]], label: str = "AI edit") -> list[Any]:
        """Run calls as one undo step; any failure undoes all of them and raises BridgeError."""
        return self._request("POST", "/v1/batch", {"label": label, "calls": calls})["results"]

    @contextmanager
    def turn(self, label: str) -> Iterator[Turn]:
        t = Turn()
        yield t
        if t.calls:
            t.results = self.batch(t.calls, label)

    # ── document helpers ────────────────────────────────────────────────────────────────────────────
    def inspect(self, **params: Any) -> dict:
        return self.call("document.inspect", **params)

    def find(self, **params: Any) -> list[dict]:
        return self.call("document.find", **params).get("matches", [])

    def node(self, node_id: int) -> dict:
        """One object with its bounds, paint, text and children."""
        return self.call("document.node", id=node_id, summary=True)

    def undo(self) -> Any:
        return self.call("edit.undo")

    def redo(self) -> Any:
        return self.call("edit.redo")

    def new_document(self, width: float, height: float, name: str | None = None) -> int:
        """A new RGB document the size of the design (D-014: brand colours are RGB hex)."""
        params: dict[str, Any] = {"width": width, "height": height, "units": "Pixels", "colorMode": "rgb"}
        if name:
            params["name"] = name
        return self.call("file.new", **params)["index"]

    def add_artboard(self, x: float, y: float, width: float, height: float, name: str) -> int:
        """A new artboard in the active document (5c variants: one per variant, side by side) → its index."""
        return self.call("artboard.new", x=x, y=y, width=width, height=height, name=name)["index"]

    def name_artboard(self, index: int, name: str) -> None:
        self.call("artboard.setProps", index=index, name=name)

    def remove(self, ids: list[int], artboards: list[int], label: str) -> None:
        """Delete objects and artboards as ONE undo step (Ctrl+Z brings them back)."""
        calls: list[dict[str, Any]] = []
        if ids:
            calls.append({"command": "layer.delete", "params": {"ids": ids}})
        if artboards:
            calls.append({"command": "artboard.delete", "params": {"indices": sorted(artboards)}})
        if calls:
            self.batch(calls, label)

    def place_svg(self, svg: str, *, name: str, width: float, height: float, new_document: bool = True,
                  origin: tuple[float, float] = (0.0, 0.0), document_name: str | None = None) -> Placed:
        """Place a layered SVG (Phase 1 output) so it sits exactly on the artboard, as one undo step.

        Procedure D-014: a new RGB document of the design's size, `file.place` (it centres the art on
        the artboard; the editor measures centred type correctly since the Phase 2 fix), then a
        corrective move only if `background` isn't at the artboard's origin."""
        if new_document:
            self.new_document(width, height, name=document_name or name)
        data = base64.b64encode(svg.encode()).decode()
        placed = self.batch([{"command": "file.place", "params": {"name": name, "dataBase64": data}}], f"Place {name}")[0]
        gid = placed["ids"][0]
        tree = self.node(gid)
        nodes = _names(tree)
        bg = _node_named(tree, "background")
        ox, oy = origin   # the artboard's top-left corner (another artboard than the first for a variant)
        if bg and (abs(bg["bounds"]["x"] - ox) > 0.01 or abs(bg["bounds"]["y"] - oy) > 0.01):
            self.batch([{"command": "object.move", "params": {"ids": [gid], "dx": ox - bg["bounds"]["x"], "dy": oy - bg["bounds"]["y"]}}],
                       f"Align {name}")
        return Placed(gid, nodes, placed.get("warnings", []))

    def render_preview(self, artboard: int = 0, scale: float = 0.5) -> bytes:
        """PNG of an artboard as the editor draws it (for the critic agent)."""
        r = self.method("ui.render", artboard=artboard, scale=scale, data=True)
        return base64.b64decode(r["pngBase64"])

    def export(self, fmt: str = "svg") -> bytes:
        """The active document exported in `fmt` (svg, png, pdf), returned as bytes; no file is written."""
        r = self.method("app.export", format=fmt)
        return base64.b64decode(r["dataBase64"])


def _names(node: dict, out: dict[str, int] | None = None) -> dict[str, int]:
    out = {} if out is None else out
    if node.get("name") and node["name"] not in out:
        out[node["name"]] = node["id"]
    for c in node.get("children", []):
        _names(c, out)
    return out


def _node_named(node: dict, name: str) -> dict | None:
    if node.get("name") == name:
        return node
    for c in node.get("children", []):
        if hit := _node_named(c, name):
            return hit
    return None
