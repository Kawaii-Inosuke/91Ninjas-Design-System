"""The bridge against the running editor (start it with `STUDIO_BRIDGE=1 ./target/debug/vectorcraft --new-instance`).
Skipped when no editor with the bridge is running. Each run opens a new document in the editor."""
import http.client
from pathlib import Path

import pytest

from studio_agents.bridge import Bridge, BridgeError
from studio_agents.render.layered_svg import layered_svg

FIXTURE = Path(__file__).parent / "fixtures" / "sample_infographic.html"


@pytest.fixture(scope="module")
def bridge():
    try:
        return Bridge.connect()
    except BridgeError as e:
        pytest.skip(f"no running editor with the bridge: {e.error}")


@pytest.fixture(scope="module")
def placed(bridge):
    res = layered_svg(FIXTURE, name="design-sample")
    return bridge.place_svg(res.svg, name="design-sample", width=res.width, height=res.height)


def test_design_lands_exactly_on_an_rgb_artboard(bridge, placed):
    doc = bridge.inspect(depth=0)
    assert doc["colorMode"] == "Rgb"
    assert doc["artboards"][0]["width"] == 1200 and doc["artboards"][0]["height"] == 900
    assert bridge.node(placed.nodes["background"])["bounds"] == {"x": 0.0, "y": 0.0, "width": 1200.0, "height": 900.0}
    assert doc["history"] == ["Place design-sample"]          # one undo step, no corrective move needed
    assert {"title", "subtitle", "steps", "tips-card", "footer", "title-text", "step-1"} <= set(placed.nodes)


def test_a_turn_is_one_undo_step(bridge, placed):
    tid = placed.nodes["title-text"]
    before = bridge.node(tid)
    with bridge.turn("Bigger title, new wording") as t:
        t.call("text.setText", id=tid, text="Six steps to safer payments")
        t.call("object.scale", ids=[tid], sx=120)
    after = bridge.node(tid)
    assert after["text"] == "Six steps to safer payments"
    assert after["bounds"]["width"] > before["bounds"]["width"]
    bridge.undo()
    undone = bridge.node(tid)
    assert undone["text"] == before["text"] and undone["bounds"] == before["bounds"]


def test_a_failing_turn_leaves_nothing_behind(bridge, placed):
    sid = placed.nodes["subtitle"]
    before = bridge.node(sid)["text"]
    with pytest.raises(BridgeError) as e:
        with bridge.turn("Broken turn") as t:
            t.call("text.setText", id=sid, text="should not stay")
            t.call("object.move", ids=[999_999_999], dx=5, dy=5)
    assert e.value.status == 422 and e.value.failed_call == 1
    assert bridge.node(sid)["text"] == before


def test_preview_and_export_without_files(bridge, placed):
    assert bridge.render_preview(scale=0.25)[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"<svg" in bridge.export("svg")[:400]


def test_refuses_files_scripts_and_strangers(bridge):
    for cmd, kw in [("document.save", {}), ("file.open", {}), ("file.place", {"path": "/etc/passwd"})]:
        with pytest.raises(BridgeError) as e:
            bridge.call(cmd, **kw)
        assert e.value.status == 403

    def status(headers):
        c = http.client.HTTPConnection("127.0.0.1", bridge.port)
        c.request("GET", "/v1/health", headers=headers)
        return c.getresponse().status

    host = {"Host": f"127.0.0.1:{bridge.port}"}
    assert status(host) == 401
    assert status({**host, "Authorization": f"Bearer {bridge.token}", "Origin": "http://evil.example"}) == 403
    assert status({"Host": "evil.example", "Authorization": f"Bearer {bridge.token}"}) == 403
