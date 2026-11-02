"""The layered SVG imported by the real editor (headless vectorcraft-cli): layers, live text, colours, look."""
from pathlib import Path

import pytest

from studio_agents.render.layered_svg import layered_svg, screenshot_html
from editor_helpers import EDITOR_CLI, find, place
from imaging import mismatch

pytestmark = pytest.mark.skipif(not EDITOR_CLI.exists(), reason="editor not built (see docs/RUNBOOK.md)")
FIXTURE = Path(__file__).parent / "fixtures" / "sample_infographic.html"


@pytest.fixture(scope="module")
def placed(tmp_path_factory):
    work = tmp_path_factory.mktemp("editor")
    res = layered_svg(FIXTURE, name="design-sample")
    doc = place(res.svg, res.width, res.height, work, name="design-sample", export_png="editor.png")
    return doc, work


def _all(node, kind=None):
    out = [node] if kind is None or node["kind"] == kind else []
    for c in node.get("children", []):
        out += _all(c, kind)
    return out


def test_rgb_artboard_of_design_size(placed):
    doc, _ = placed
    assert doc["colorMode"] == "Rgb"
    ab = doc["artboards"][0]
    assert (ab["width"], ab["height"]) == (1200, 900)
    assert find(doc["layers"][0], "background")["bounds"] == {"x": 0.0, "y": 0.0, "width": 1200.0, "height": 900.0}


def test_named_groups_survive(placed):
    doc, _ = placed
    design = find(doc["layers"][0], "design-sample")
    assert design["kind"] == "Group"
    names = [c["name"] for c in design["children"]]
    assert set(names) == {"background", "title", "subtitle", "steps", "arrow", "tips-card", "icon-shield", "footer"}
    assert [c["name"] for c in find(design, "steps")["children"]][::-1] == ["step-1", "step-2", "step-3"]


def test_text_is_editable_type(placed):
    doc, _ = placed
    types = {t["name"]: t["text"] for t in _all(doc["layers"][0], "Type")}
    assert len(types) == 16
    assert types["title-text"] == "Five steps to safer payments"
    assert types["subtitle"].split("\n")[1] == "matter how urgent it looks."
    assert types["label-1"] == "VERIFY"


def test_brand_colours_exact(placed):
    doc, _ = placed
    root = doc["layers"][0]
    assert find(root, "footer-shape")["fill"] == "#0164f0"
    assert find(root, "marker-shape-1")["fill"] == "#ffffff"


def test_editor_render_matches_html(placed):
    _, work = placed
    ref = screenshot_html(FIXTURE, work / "ref.png")
    assert mismatch(ref, work / "editor.png", diff_out=work / "diff.png") < 1.0
