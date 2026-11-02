"""Our templates + renderer: every layout renders clean, layers get stable names, bad input is caught."""
import json
from pathlib import Path

import pytest

from studio_agents import brand
from studio_agents.render import icons
from studio_agents.render.build import build_html, render, sanitize_markup
from studio_agents.render.layered_svg import layered_svg
from studio_agents.render.spec import Spec

FIX = Path(__file__).parent / "fixtures"
SPECS = json.loads((FIX / "specs.json").read_text())


@pytest.fixture(scope="module")
def demo():
    return brand.load("demo", root=FIX / "brands")


@pytest.mark.parametrize("name", list(SPECS))
def test_every_layout_renders_without_problems(name, demo, tmp_path):
    r = render(Spec(**SPECS[name]), demo, tmp_path)
    assert r.auto_checks == [], r.auto_checks
    assert r.width == 1200 and 300 < r.height < 1500
    assert r.preview.exists() and r.version == 1
    assert render(Spec(**SPECS[name]), demo, tmp_path).version == 2      # versions count up


def test_layers_get_stable_names(demo, tmp_path):
    r = render(Spec(**SPECS["vertical_list"]), demo, tmp_path)
    svg = layered_svg(r.html, name="design-x").svg
    for layer in ['id="title"', 'id="item-1"', 'id="item-3"', 'id="marker-2"', 'id="heading-1"', 'id="body-3"', 'id="takeaway"']:
        assert layer in svg, layer


def test_spec_validation():
    with pytest.raises(ValueError, match="unknown"):
        Spec(title="x", layout="pie_chart", items=[])
    with pytest.raises(ValueError, match="no variant"):
        Spec(title="x", layout="vertical_list/hexagon", items=[{"heading": "a"}, {"heading": "b"}])
    with pytest.raises(ValueError, match="2-8 items"):
        Spec(title="x", layout="vertical_list", items=[{"heading": "a"}])
    with pytest.raises(ValueError, match="one cell per header"):
        Spec(title="x", layout="cell_table", table={"header": ["a", "b"], "rows": [["1"]]})
    with pytest.raises(ValueError, match="needs `html`"):
        Spec(title="x", layout="custom")
    assert Spec(title="x", layout="card_list", items=[{"body": "a"}, {"body": "b"}]).variant == "check"


def test_checks_catch_off_brand_output(demo, tmp_path):
    d = {**SPECS["card_list"], "css": ".clist .lead { color: #ff00ff; font-size: 12px; }"}
    d["items"] = [{**d["items"][0], "icon": "no-such-icon"}, *d["items"][1:]]
    r = render(Spec(**{**d, "layout": "vertical_list/icon"}), demo, tmp_path)
    assert any("icon not found: no-such-icon" in c for c in r.auto_checks)
    r = render(Spec(**d), demo, tmp_path)
    assert any("off-palette colours: #FF00FF" in c for c in r.auto_checks)
    assert any("text under 20px" in c for c in r.auto_checks)


def test_agent_markup_and_css_are_cleaned(demo):
    d = {"title": "x", "layout": "custom",
         "html": "<div onclick='x()'>ok<script>steal()</script><img src='https://evil.example/t.png'></div>",
         "css": "@import url(https://evil.example/a.css); .b{background:url(https://evil.example/b.png)}"}
    html, _ = build_html(Spec(**d), demo)
    assert "steal" not in html and "onclick" not in html and "evil.example" not in html
    assert sanitize_markup("<p>a</p><iframe src=x></iframe>") == "<p>a</p>"


def test_icon_search():
    assert icons.search("truck")[0] == "truck"
    assert "shield-check" in icons.search("shield check")
    assert icons.exists("circle-check") and not icons.exists("no-such-icon")
    assert icons.svg("truck", 40).startswith("<svg") and icons.svg("nope", 40) == ""
