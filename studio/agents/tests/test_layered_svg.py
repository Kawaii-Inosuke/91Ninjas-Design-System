"""layered_svg(): structure rules (TRD "Rules for the SVG we place") and visual match with the browser render."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from studio_agents.render.layered_svg import layered_svg, screenshot_html, screenshot_svg
from imaging import mismatch

FIXTURE = Path(__file__).parent / "fixtures" / "sample_infographic.html"
NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(scope="module")
def result():
    return layered_svg(FIXTURE, name="design-sample")


@pytest.fixture(scope="module")
def doc(result):
    return ET.fromstring(result.svg)


def test_top_level_layers_in_paint_order(result):
    assert result.layers == ["background", "title", "subtitle", "steps", "arrow", "tips-card", "icon-shield", "footer"]
    assert result.width == 1200 and result.height == 900
    assert result.warnings == []


def test_single_outer_group(doc):
    groups = [c for c in doc if c.tag == f"{NS}g"]
    assert len(groups) == 1 and groups[0].get("id") == "design-sample"


def test_ids_unique_and_readable(doc):
    ids = [e.get("id") for e in doc.iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    assert {"step-1", "step-2", "step-3", "marker-1", "label-1", "tips-card-shape"} <= set(ids)
    assert not any(re.fullmatch(r"(div|span)(-\d+)?", i) for i in ids)


def test_text_is_live_with_lines_and_runs(doc):
    texts = {t.get("id"): t for t in doc.iter(f"{NS}text")}
    assert len(texts) == 16
    title = texts["title-text"]
    assert "".join(title.itertext()) == "Five steps to safer payments"
    sub = texts["subtitle"]
    lines = [c for c in sub if c.tag == f"{NS}tspan"]
    assert len(lines) == 2 and sub.get("text-anchor") == "middle"
    bold = [t for t in sub.iter(f"{NS}tspan") if t.get("font-weight") == "700"]
    assert bold and bold[0].text == "any transfer"
    assert "".join(texts["label-1"].itertext()) == "VERIFY"        # text-transform applied
    assert texts["label-1"].get("letter-spacing") == "2"


def test_one_font_family_per_element_and_no_filters(result, doc):
    for e in doc.iter():
        if e.get("font-family"):
            assert "," not in e.get("font-family")
    assert "<filter" not in result.svg and "filter=" not in result.svg


def test_shapes_cover_css_features(doc):
    by_id = {e.get("id"): e for e in doc.iter() if e.get("id")}
    assert by_id["arrow"].tag == f"{NS}polygon"                                   # clip-path polygon
    assert by_id["tips-card-shape"].get("fill") == "url(#tips-card-shape-grad)"   # linear-gradient
    assert by_id["marker-shape-1"].get("stroke") == "#4e93f5"                     # border → stroke
    assert by_id["step-before-1"].get("stroke-dasharray")                         # dashed ::before connector
    assert by_id["title-shape"].get("fill-opacity") == "0.1"                      # #RRGGBBAA tint
    assert by_id["icon"].tag == f"{NS}g"                                          # inline SVG icon
    assert all(p.get("stroke") == "#0164f0" for p in by_id["icon"])               # currentColor resolved


def test_svg_looks_like_the_html(result, tmp_path):
    ref = screenshot_html(FIXTURE, tmp_path / "ref.png")
    svg = screenshot_svg(result, tmp_path / "svg.png")
    assert mismatch(ref, svg, diff_out=tmp_path / "diff.png") < 0.3
