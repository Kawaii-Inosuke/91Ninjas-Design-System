"""Rendered HTML design → layered, editable SVG for the 91 Studio editor.

The page is opened in headless Chromium, `extract.js` measures every box, text line and icon under the root
element, and this module writes them as SVG that VectorCraft imports as named groups with live text.
Rules the output follows (PHASE-0 findings, TRD "Rules for the SVG we place"): named groups, live <text>
with one <tspan> per line, one font family per element, no filters.

    uv run python -m studio_agents.render.layered_svg design.html -o design.svg [--root '#canvas']
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from base64 import b64encode
from collections import Counter
from dataclasses import dataclass, field
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape, quoteattr

from playwright.sync_api import Page, sync_playwright

EXTRACT_JS = (Path(__file__).with_name("extract.js")).read_text()
# Greyscale text anti-aliasing, as the editor draws it, so screenshots compare like with like.
CHROMIUM_ARGS = ["--disable-lcd-text", "--font-render-hinting=none"]


@dataclass
class LayeredSVG:
    svg: str
    width: float
    height: float
    layers: list[str] = field(default_factory=list)      # top-level layer names, paint order
    warnings: list[str] = field(default_factory=list)
    font_faces: list[str] = field(default_factory=list)  # @font-face rules of the page (for previews)


def layered_svg(html: str | Path, *, root: str = "#canvas", name: str = "design",
                viewport: tuple[int, int] = (2400, 2400)) -> LayeredSVG:
    """Convert a design (an HTML file path, or HTML markup) to a layered SVG."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            _load(page, html)
            data = page.evaluate(EXTRACT_JS, root)
        finally:
            browser.close()
    return build_svg(data, name=name, base=Path(html).parent if isinstance(html, Path) else None)


def _load(page: Page, html: str | Path) -> None:
    if isinstance(html, Path):
        page.goto(html.resolve().as_uri())
    else:
        page.set_content(html)
    page.evaluate("document.fonts.ready.then(() => true)")


# ── tree → SVG ────────────────────────────────────────────────────────────────────────────────────

def build_svg(data: dict, *, name: str = "design", base: Path | None = None) -> LayeredSVG:
    warnings = list(data["warnings"])
    root_name = data["tree"]["name"]
    tree = _simplify(data["tree"])
    if tree["type"] != "group":
        tree = {"type": "group", "name": name, "explicit": True, "children": [tree]}
    tree["name"] = name
    for c in tree["children"]:
        if c.get("name") == f"{root_name}-shape":
            c["name"] = "background"
    _assign_ids(tree)
    defs: list[str] = []
    body = _emit(tree, defs, warnings, base)
    w, h = _n(data["width"]), _n(data["height"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
           + (f"<defs>\n{''.join(defs)}</defs>\n" if defs else "") + body + "</svg>\n")
    layers = [c["id"] for c in tree.get("children", []) if "id" in c]
    return LayeredSVG(svg, data["width"], data["height"], layers, warnings, data.get("fontFaces", []))


def _simplify(node: dict) -> dict | None:
    """Drop empty groups; unwrap anonymous single-child groups; name leaves after their element."""
    if node["type"] != "group":
        return node
    kids = [k for k in (_simplify(c) for c in node["children"]) if k]
    if not kids:
        return None
    if len(kids) == 1 and kids[0]["type"] != "group":
        leaf = kids[0]
        leaf["name"] = node["name"]
        if node.get("opacity", 1) < 1:
            leaf["opacity"] = node["opacity"]
        return leaf
    if len(kids) == 1 and not node["explicit"] and node.get("opacity", 1) == 1:
        return kids[0]
    suffix = {"box": "shape", "polygon": "shape", "sides": "border", "text": "text", "svg": "icon", "image": "image"}
    for k in kids:
        if k["type"] != "group" and "name" not in k:
            k["name"] = f'{node["name"]}-{suffix[k["type"]]}'
    node["children"] = kids
    return node


def _assign_ids(tree: dict) -> None:
    """Unique XML ids from names; repeated names are numbered in paint order (step-1, step-2 …)."""
    nodes: list[dict] = []

    def collect(n: dict) -> None:
        nodes.append(n)
        for c in n.get("children", []):
            collect(c)

    collect(tree)
    clean = [re.sub(r"[^A-Za-z0-9_-]+", "-", n["name"]).strip("-") or n["type"] for n in nodes]
    clean = [c if c[0].isalpha() else f"l-{c}" for c in clean]
    counts, seen, used = Counter(clean), Counter(), set()
    for n, c in zip(nodes, clean):
        if counts[c] > 1:
            seen[c] += 1
            c = f"{c}-{seen[c]}"
        while c in used:
            c += "-x"
        used.add(c)
        n["id"] = c


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _paint(c: dict | None, prop: str = "fill") -> str:
    if not c:
        return f' {prop}="none"'
    out = f' {prop}="{c["hex"]}"'
    if c["alpha"] < 1:
        out += f' {prop}-opacity="{_n(c["alpha"])}"'
    return out


def _gradient(g: dict, gid: str, defs: list[str]) -> str:
    stops = "".join(f'<stop offset="{_n(s["offset"])}" stop-color="{s["hex"]}"'
                    + (f' stop-opacity="{_n(s["alpha"])}"' if s["alpha"] < 1 else "") + "/>" for s in g["stops"])
    defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_n(g["x1"])}" y1="{_n(g["y1"])}" '
                f'x2="{_n(g["x2"])}" y2="{_n(g["y2"])}">{stops}</linearGradient>\n')
    return f' fill="url(#{gid})"'


def _dash(style: str, width: float) -> str:
    if style == "dashed":
        return f' stroke-dasharray="{_n(width * 3)} {_n(width * 2)}"'
    if style == "dotted":
        return f' stroke-dasharray="0 {_n(width * 2)}" stroke-linecap="round"'
    return ""


def _rounded_path(x: float, y: float, w: float, h: float, r: list[list[float]]) -> str:
    """Rect with per-corner radii [tl, tr, br, bl] (each [rx, ry]) as path data, CSS-style scaled to fit."""
    f = min(1.0, *(w / (a[0] + b[0]) for a, b in ((r[0], r[1]), (r[3], r[2])) if a[0] + b[0] > 0),
            *(h / (a[1] + b[1]) for a, b in ((r[0], r[3]), (r[1], r[2])) if a[1] + b[1] > 0))
    (a, b), (c, d), (e, g), (i, j) = [[v * f for v in k] for k in r]
    return (f"M{_n(x + a)},{_n(y)} H{_n(x + w - c)} A{_n(c)},{_n(d)} 0 0 1 {_n(x + w)},{_n(y + d)} "
            f"V{_n(y + h - g)} A{_n(e)},{_n(g)} 0 0 1 {_n(x + w - e)},{_n(y + h)} "
            f"H{_n(x + i)} A{_n(i)},{_n(j)} 0 0 1 {_n(x)},{_n(y + h - j)} "
            f"V{_n(y + b)} A{_n(a)},{_n(b)} 0 0 1 {_n(x + a)},{_n(y)} Z")


def _emit(n: dict, defs: list[str], warnings: list[str], base: Path | None, depth: int = 0) -> str:
    pad = "  " * depth
    nid = f' id="{n["id"]}"'
    op = f' opacity="{_n(n["opacity"])}"' if n.get("opacity", 1) < 1 else ""
    t = n["type"]

    if t == "group":
        inner = "".join(_emit(c, defs, warnings, base, depth + 1) for c in n["children"])
        return f"{pad}<g{nid}{op}>\n{inner}{pad}</g>\n"

    if t == "box":
        fill = _gradient(n["gradient"], f'{n["id"]}-grad', defs) if n.get("gradient") else _paint(n["fill"])
        x, y, w, h, r = n["x"], n["y"], n["w"], n["h"], n["radii"]
        stroke = ""
        if n.get("stroke"):
            s = n["stroke"]
            sw = s["width"]
            x, y, w, h = x + sw / 2, y + sw / 2, w - sw, h - sw
            r = [[max(0.0, a - sw / 2), max(0.0, b - sw / 2)] for a, b in r]
            stroke = _paint(s["color"], "stroke") + f' stroke-width="{_n(sw)}"' + _dash(s["style"], sw)
        geo = f'x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"'
        if all(c == r[0] for c in r):
            rx, ry = r[0]
            rr = "" if rx == 0 and ry == 0 else f' rx="{_n(min(rx, w / 2))}" ry="{_n(min(ry, h / 2))}"'
            return f"{pad}<rect{nid} {geo}{rr}{fill}{stroke}{op}/>\n"
        return f'{pad}<path{nid} d="{_rounded_path(x, y, w, h, r)}"{fill}{stroke}{op}/>\n'

    if t == "polygon":
        fill = _gradient(n["gradient"], f'{n["id"]}-grad', defs) if n.get("gradient") else _paint(n["fill"])
        pts = " ".join(f"{_n(px)},{_n(py)}" for px, py in n["points"])
        return f'{pad}<polygon{nid} points="{pts}"{fill}{op}/>\n'

    if t == "sides":
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        lines = []
        for i, s in enumerate(n["sides"]):
            if not s:
                continue
            sw = s["width"]
            x1, y1, x2, y2 = [(x, y + sw / 2, x + w, y + sw / 2), (x + w - sw / 2, y, x + w - sw / 2, y + h),
                              (x, y + h - sw / 2, x + w, y + h - sw / 2), (x + sw / 2, y, x + sw / 2, y + h)][i]
            lines.append((["top", "right", "bottom", "left"][i],
                          f'x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}"'
                          f'{_paint(s["color"], "stroke")} stroke-width="{_n(sw)}"{_dash(s["style"], sw)}'))
        if len(lines) == 1:
            return f"{pad}<line{nid} {lines[0][1]}{op}/>\n"
        inner = "".join(f'{pad}  <line id="{n["id"]}-{side}" {geo}/>\n' for side, geo in lines)
        return f"{pad}<g{nid}{op}>\n{inner}{pad}</g>\n"

    if t == "text":
        return _emit_text(n, pad, nid, op)

    if t == "svg":
        if n["viewBox"]:
            vx, vy, vw, vh = n["viewBox"]
            s = min(n["w"] / vw, n["h"] / vh)
            tx, ty = n["x"] + (n["w"] - vw * s) / 2 - vx * s, n["y"] + (n["h"] - vh * s) / 2 - vy * s
            tf = f"translate({_n(tx)} {_n(ty)}) scale({s:.5g})"
        else:
            tf = f'translate({_n(n["x"])} {_n(n["y"])})'
        markup = re.sub(r"\s+", " ", n["markup"]).strip()
        return f'{pad}<g{nid} transform="{tf}"{op}>{markup}</g>\n'

    if t == "image":
        href = _data_uri(n["src"], base)
        if not href:
            warnings.append(f'image skipped (not a local file or data URI): {n["src"][:60]}')
            return ""
        par = {"cover": "xMidYMid slice", "contain": "xMidYMid meet", "none": "xMidYMid meet"}.get(n.get("fit"), "none")
        return (f'{pad}<image{nid} x="{_n(n["x"])}" y="{_n(n["y"])}" width="{_n(n["w"])}" height="{_n(n["h"])}" '
                f'preserveAspectRatio="{par}" href={quoteattr(href)}{op}/>\n')

    raise ValueError(f"unknown node type {t}")


def _style_attrs(st: dict) -> dict[str, str]:
    a = {"font-family": st["family"], "font-size": _n(st["size"]), "font-weight": str(st["weight"])}
    if st["italic"]:
        a["font-style"] = "italic"
    if st["letterSpacing"]:
        a["letter-spacing"] = _n(st["letterSpacing"])
    c = st["color"] or {"hex": "#000000", "alpha": 0}
    a["fill"] = c["hex"]
    if c["alpha"] < 1:
        a["fill-opacity"] = _n(c["alpha"])
    return a


def _emit_text(n: dict, pad: str, nid: str, op: str) -> str:
    """One <text> per text block; each line is a positioned <tspan>; style changes within a line are nested
    runs without x, so they flow after the previous run exactly as the browser laid them out."""
    base = _style_attrs(n["lines"][0]["runs"][0]["style"])
    attrs = "".join(f" {k}={quoteattr(v)}" for k, v in base.items())
    anchor = f' text-anchor="{n["anchor"]}"' if n["anchor"] != "start" else ""
    parts = []
    for line in n["lines"]:
        runs = []
        for r in line["runs"]:
            diff = {k: v for k, v in _style_attrs(r["style"]).items() if base.get(k) != v}
            for k in ("font-style", "letter-spacing", "fill-opacity"):
                if k in base and k not in _style_attrs(r["style"]):
                    diff[k] = {"font-style": "normal", "letter-spacing": "0", "fill-opacity": "1"}[k]
            text = escape(r["text"])
            runs.append(f'<tspan{"".join(f" {k}={quoteattr(v)}" for k, v in diff.items())}>{text}</tspan>'
                        if diff else text)
        parts.append(f'<tspan x="{_n(line["x"])}" y="{_n(line["y"])}">{"".join(runs)}</tspan>')
    return f'{pad}<text{nid}{attrs}{anchor} xml:space="preserve"{op}>{"".join(parts)}</text>\n'


def _data_uri(src: str, base: Path | None) -> str | None:
    if src.startswith("data:"):
        return src
    u = urlparse(src)
    if u.scheme == "file":
        path = Path(unquote(u.path))
    elif not u.scheme and base:
        path = base / src
    else:
        return None
    if not path.is_file():
        return None
    mime = guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{b64encode(path.read_bytes()).decode()}"


# ── previews (tests and quick looks) ──────────────────────────────────────────────────────────────

def screenshot_html(html: str | Path, out: Path, *, root: str = "#canvas") -> Path:
    """PNG of the design as the browser renders it (the reference for comparisons)."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        try:
            page = browser.new_page(viewport={"width": 2400, "height": 2400})
            _load(page, html)
            page.locator(root).screenshot(path=str(out))
        finally:
            browser.close()
    return out


def screenshot_svg(result: LayeredSVG, out: Path) -> Path:
    """PNG of the layered SVG rendered by Chromium, with the page's @font-face rules so fonts match."""
    page_html = (f"<!doctype html><html><head><style>{''.join(result.font_faces)} "
                 f"*{{margin:0;padding:0}}</style></head><body>{result.svg}</body></html>")
    # Loaded from a file, not set_content: an about:blank page may not load file:// fonts.
    wrapper = out.with_suffix(".preview.html")
    wrapper.write_text(page_html)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=CHROMIUM_ARGS)
            try:
                page = browser.new_page(viewport={"width": 2400, "height": 2400})
                _load(page, wrapper)
                page.locator("svg").first.screenshot(path=str(out))
            finally:
                browser.close()
    finally:
        wrapper.unlink(missing_ok=True)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Convert a rendered HTML design to a layered SVG.")
    p.add_argument("html", type=Path)
    p.add_argument("-o", "--out", type=Path, required=True)
    p.add_argument("--root", default="#canvas", help="CSS selector of the design's canvas element")
    p.add_argument("--name", default="design")
    a = p.parse_args(argv)
    res = layered_svg(a.html, root=a.root, name=a.name)
    a.out.write_text(res.svg)
    print(json.dumps({"out": str(a.out), "width": res.width, "height": res.height,
                      "layers": res.layers, "warnings": res.warnings}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
