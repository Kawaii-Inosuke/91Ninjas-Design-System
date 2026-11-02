"""Designs with Claude (D-033) and documents by uid (D-032): render, place in its own document, versions side by
side, remove the others in one step, look after the designer's Ctrl+Z, edit, approve — against a fake editor."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest

from studio_agents.bridge import BridgeError, Placed
from studio_agents.designs import DesignError, Studio
from studio_agents.history import History

FIX = Path(__file__).parent / "fixtures"
SPECS = json.loads((FIX / "specs.json").read_text())


class Editor:
    """A fake editor: documents by uid, each with artboards and placed groups."""

    def __init__(self):
        self.docs: dict[int, dict] = {}
        self.front: int | None = None
        self.next_uid, self.next_gid = 1, 100
        self.log: list[tuple] = []

    def bridge(self):
        return FakeBridge(self, None)


class FakeBridge:
    def __init__(self, ed: Editor, document):
        self.ed, self.document = ed, document

    def in_document(self, uid):
        return FakeBridge(self.ed, uid)

    def _doc(self):
        uid = self.document if self.document is not None else self.ed.front
        if uid not in self.ed.docs:
            raise BridgeError(422, "that document is no longer open (it was closed)")
        return self.ed.docs[uid]

    def documents(self):
        return [{"uid": u, "title": d["title"], "active": u == self.ed.front} for u, d in self.ed.docs.items()]

    def is_open(self):
        return self.document in self.ed.docs

    def new_document(self, w, h, name=None):
        uid = self.ed.next_uid
        self.ed.next_uid += 1
        self.ed.docs[uid] = {"title": name, "artboards": [{"name": "Artboard 1", "x": 0, "y": 0, "width": w, "height": h}], "groups": {}}
        self.ed.front, self.document = uid, uid
        return uid

    def place_svg(self, svg, *, name, width, height, new_document=True, origin=(0.0, 0.0), document_name=None):
        if new_document:
            self.new_document(width, height, document_name or name)
        gid = self.ed.next_gid
        self.ed.next_gid += 1
        self._doc()["groups"][gid] = {"x": origin[0], "y": origin[1], "width": width, "height": height}
        self.ed.log.append(("place", self.document, gid, new_document))
        return Placed(gid, {"title": gid + 1000}, [], self.document)

    def name_artboard(self, i, name):
        self._doc()["artboards"][i]["name"] = name

    def add_artboard(self, x, y, w, h, name):
        boards = self._doc()["artboards"]
        boards.append({"name": name, "x": x, "y": y, "width": w, "height": h})
        return len(boards) - 1

    def inspect(self):
        return {"artboards": self._doc()["artboards"]}

    def node(self, gid):
        g = self._doc()["groups"].get(gid)
        if g is None:
            raise BridgeError(422, f"no object {gid}")
        return {"id": gid, "name": "design", "kind": "Group", "bounds": g, "children": [{"id": gid + 1, "name": "title-text", "kind": "Type", "text": "Hello"}]}

    def remove(self, ids, artboards, label):
        d = self._doc()
        for g in ids:
            d["groups"].pop(g, None)
        d["artboards"] = [a for i, a in enumerate(d["artboards"]) if i not in artboards]
        self.ed.log.append(("remove", self.document, tuple(ids), tuple(artboards), label))

    def batch(self, calls, label):
        self._doc()
        self.ed.log.append(("batch", self.document, label, len(calls)))
        return [None] * len(calls)

    def render_preview(self, artboard=0, scale=0.5):
        return b"\x89PNG fake"

    def export(self, fmt):
        self._doc()
        self.ed.log.append(("export", self.document, fmt))
        if fmt == "png":
            import io
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (40, 30), (self.document * 40 % 255, 90, 200)).save(buf, "PNG")
            return buf.getvalue()
        return f"{fmt} of {self.document}".encode()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(Path.home() / ".cache" / "91studio" / "models"))
    shutil.copytree(FIX / "brands" / "demo", data / "clients" / "demo")
    ed = Editor()
    return ed, Studio(History(data / "history.db"), bridge_factory=ed.bridge)


def test_each_design_gets_its_own_document_and_the_designers_tab_never_matters(setup):
    ed, studio = setup
    a = studio.create("demo", "Safer payments", "steps")
    studio.render(a.id, SPECS["vertical_list"])
    pa = studio.place(a.id)
    b = studio.create("demo", "Marketplace vs D2C", "comparison")
    studio.render(b.id, SPECS["comparison_cards"])
    pb = studio.place(b.id)
    assert pa["document"] != pb["document"], "a new design never goes into another design's document"
    # The designer clicks back to design A's tab; edits and approval of B still go to B's document.
    ed.front = pa["document"]
    studio.edit(b.id, [{"command": "text.setText", "params": {"id": 1, "text": "x"}}], "New title")
    assert ed.log[-1] == ("batch", pb["document"], "New title", 1)
    r = studio.approve(b.id)
    assert Path(r["files"]["svg"]).read_bytes() == f"svg of {pb['document']}".encode()
    assert {e[1] for e in ed.log if e[0] == "export"} == {pb["document"]}, "exported from B's document only"
    assert studio.get(b.id).status == "approved" and studio.history.get(studio.get(b.id).history_id)["status"] == "approved"
    with pytest.raises(DesignError, match="approved"):
        studio.render(b.id, SPECS["vertical_list"])


def test_versions_side_by_side_then_keep_one_in_one_step(setup):
    ed, studio = setup
    d = studio.create("demo", "Order shipping", "steps")
    for fam in ("vertical_list", "horizontal_timeline", "comparison_cards"):
        studio.render(d.id, SPECS[fam])
    p1 = studio.place(d.id, 1)
    p2 = studio.place(d.id, 2, beside=True)
    p3 = studio.place(d.id, 3, beside=True)
    assert p1["document"] == p2["document"] == p3["document"] and (p2["artboard"], p3["artboard"]) == (1, 2)
    boards = ed.docs[p1["document"]]["artboards"]
    assert [b["name"] for b in boards] == ["Version 1", "Version 2", "Version 3"]
    assert boards[0]["x"] + boards[0]["width"] < boards[1]["x"] < boards[2]["x"], "to the right, apart"
    studio.remove(d.id, [1, 3], "Keep version 2")
    assert ed.log[-1] == ("remove", p1["document"], (p1["group_id"], p3["group_id"]), (0, 2), "Keep version 2")
    assert list(studio.get(d.id).placements) == ["2"]


def test_look_sees_the_designers_undo_and_place_brings_it_back(setup):
    ed, studio = setup
    d = studio.create("demo", "Returns policy", "steps")
    studio.render(d.id, SPECS["vertical_list"])
    p = studio.place(d.id)
    state, png = studio.look(d.id)
    assert state["design"]["placed"][0]["on_artboard"] and png
    assert state["design"]["placed"][0]["layers"][1]["text"] == "Hello"
    ed.docs[p["document"]]["groups"].clear()            # the designer pressed Ctrl+Z
    state, png = studio.look(d.id)
    assert state["design"]["placed"][0]["on_artboard"] is False and png is None
    again = studio.place(d.id, 1)                         # "add it back"
    assert again["document"] != p["document"] and studio.look(d.id)[0]["design"]["placed"][0]["on_artboard"]
    del ed.docs[again["document"]]                        # tab closed
    assert studio.look(d.id)[0]["design"]["placed"][0]["document_open"] is False
    with pytest.raises(DesignError, match="place one without"):
        studio.place(d.id, 1, beside=True)


def test_bad_input_is_refused_before_anything_happens(setup):
    ed, studio = setup
    with pytest.raises(Exception):
        studio.create("nobody", "x")
    d = studio.create("demo", "X", "")
    with pytest.raises(DesignError, match="render one first"):
        studio.place(d.id)
    with pytest.raises(DesignError, match="invalid"):
        studio.render(d.id, {"title": "x", "layout": "nope"})
    with pytest.raises(DesignError, match="no design"):
        studio.get("../../etc")
    studio.render(d.id, SPECS["vertical_list"])
    with pytest.raises(DesignError, match="isn't in the editor"):
        studio.edit(d.id, [{"command": "x"}], "x")
    studio.abandon(d.id)
    assert studio.history.get(d.history_id)["status"] == "abandoned"
    off = Studio(studio.history, bridge_factory=lambda: None)
    with pytest.raises(DesignError, match="isn't open"):
        off.place(d.id)


def test_the_connectors_tools_return_text_and_images(setup, monkeypatch):
    ed, studio = setup
    from studio_agents import mcp_server
    monkeypatch.setattr(mcp_server, "studio", studio)
    tools = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert {"get_brand", "check_similar", "start_design", "render_design", "place_design", "remove_versions", "look_at_editor",
            "edit_design", "approve_design", "list_designs", "measure_past_work", "save_new_client"} <= tools
    assert "create something new" not in mcp_server.INSTRUCTIONS and "NEW design" in mcp_server.INSTRUCTIONS
    brand = mcp_server.get_brand("demo")
    assert "## Writing the spec" in brand and "vertical_list" in brand and "Rules every design is checked against" in brand
    did = json.loads(mcp_server.start_design("demo", "Safer payments", "steps", ["call back"]))["design"]
    r = asyncio.run(mcp_server.server.call_tool("render_design", {"design": did, "spec": SPECS["vertical_list"]}))
    kinds = [c.type for c in r.content]
    assert kinds == ["text", "image"] and '"version": 1' in r.content[0].text
    assert json.loads(mcp_server.place_design(did))["document"] == 1
    assert mcp_server.edit_design(did, [{"command": "text.setText", "params": {"id": 1, "text": "Hi"}}], "Hi").startswith("{")
    assert mcp_server.edit_design("nope", [], "x").startswith("error:")
    assert did in mcp_server.list_designs("demo")
    assert "files" in json.loads(mcp_server.approve_design(did))
