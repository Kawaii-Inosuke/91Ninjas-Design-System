"""Spec + brand → HTML → PNG (+ preview) with machine checks. The approach is the infographic skill's
(D-010: re-implemented): Jinja templates over brand CSS variables, headless Chromium, automatic checks for
canvas size, fonts, off-palette colours, overflow and small text, then the critic looks at the preview.

The page never reaches the network (every non-file, non-data request is aborted), and custom markup from
the agents is stripped of scripts and event handlers.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import jinja2
from markupsafe import Markup
from PIL import Image
from playwright.sync_api import Route, sync_playwright

from studio_agents import config
from studio_agents.brand import Brand
from studio_agents.packs import sanitize_css
from studio_agents.render import icons
from studio_agents.render.layered_svg import CHROMIUM_ARGS
from studio_agents.render.spec import Spec

TEMPLATES = Path(__file__).with_name("templates")
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True, trim_blocks=True, lstrip_blocks=True)


@dataclass
class RenderResult:
    version: int
    html: Path
    png: Path
    preview: Path
    width: int
    height: int
    auto_checks: list[str] = field(default_factory=list)
    missing_icons: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        return {k: str(v) if isinstance(v, Path) else v for k, v in d.items()}


def sanitize_markup(html: str) -> str:
    """Agent-written custom markup: no scripts, frames, embeds, event handlers, javascript: or remote URLs."""
    html = re.sub(r"<\s*(script|iframe|object|embed|style|noscript|template)\b[^>]*>.*?<\s*/\s*\1\s*>", "", html, flags=re.I | re.S)
    html = re.sub(r"<\s*/?\s*(script|iframe|object|embed|link|meta|base|style|noscript|template)\b[^>]*>", "", html, flags=re.I)
    html = re.sub(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html, flags=re.I)
    html = re.sub(r"(href|src)\s*=\s*(\"|')\s*(javascript:|https?:|//)[^\"']*\2", r'\1=""', html, flags=re.I)
    return html


def build_html(spec: Spec, brand: Brand) -> tuple[str, list[str]]:
    """The design's HTML and the icon names it asked for that don't exist."""
    style = brand.icon_style
    client_icons = brand.dir / "assets" / "icons"
    missing: list[str] = []

    def icon(name: str, size: int) -> Markup:
        out = icons.svg(name, round(size * float(style["scale"])), float(style["stroke"]), client_icons)
        if not out:
            missing.append(name)
        return Markup(out)

    # Passed per render, never via ENV.globals: Jinja caches imported macro modules, so a global would keep
    # the first brand's icon style and the first call's missing-icon list for the life of the process.
    custom = ""
    if spec.family == "custom":
        custom = ENV.from_string(sanitize_markup(spec.html or "")).render(icon=icon)
    html = ENV.get_template(f"{spec.family}.html.j2").render(
        icon=icon, spec=spec, family=spec.family, variant=spec.variant, width=brand.canvas_width, height=brand.canvas_height,
        css_vars=Markup(brand.css_vars()), font_faces=Markup(brand.font_faces()),
        base_css=Markup((TEMPLATES / "base.css").read_text()), brand_css=Markup(brand.brand_css()),
        spec_css=Markup(sanitize_css(spec.css or "")), custom_html=Markup(custom))
    return html, sorted(set(missing))


AUTO_CHECKS = """([width, maxH, fonts, palette, minPx]) => {
  const out = [], hex = c => { const m = c.match(/\\d+(\\.\\d+)?/g); if (!m || (m[3] !== undefined && +m[3] === 0)) return null;
    return '#' + m.slice(0, 3).map(v => (+v).toString(16).padStart(2, '0')).join('').toUpperCase(); };
  const canvas = document.querySelector('#canvas'), H = canvas.scrollHeight;
  if (canvas.scrollWidth > width) out.push(`canvas wider than ${width}px (${canvas.scrollWidth})`);
  if (H > maxH) out.push(`canvas taller than ${maxH}px (${H})`);
  for (const f of fonts) for (const w of [400, 700]) if (!document.fonts.check(`${w} 16px "${f}"`)) { out.push(`font not available: ${f}`); break; }
  const off = new Set(), small = new Set();
  for (const el of document.querySelectorAll('#canvas *')) {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (hasText) {
      const fam = s.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '');
      if (!fonts.includes(fam)) out.push(`wrong font "${fam}" on: ${el.textContent.trim().slice(0, 40)}`);
      if (parseFloat(s.fontSize) < minPx) small.add(`${s.fontSize} "${el.textContent.trim().slice(0, 30)}"`);
      if (el.scrollWidth > el.clientWidth + 1 && s.overflow !== 'visible') out.push(`text clipped: ${el.textContent.trim().slice(0, 40)}`);
    }
    if (r.right > width + 1 || r.left < -1) out.push(`outside the canvas: <${el.tagName.toLowerCase()} data-layer="${el.dataset ? el.dataset.layer || '' : ''}">`);
    const box = el.closest('.card, .cell, .col-head, .col-body, .takeaway');
    if (box && box !== el && hasText) { const b = box.getBoundingClientRect();
      if (r.right > b.right + 1 || r.bottom > b.bottom + 1) out.push(`text overflows its box: ${el.textContent.trim().slice(0, 40)}`); }
    const shape = el instanceof SVGGeometryElement;
    for (const p of shape ? ['fill', 'stroke'] : ['color', 'backgroundColor', 'borderTopColor']) {
      if (p === 'borderTopColor' && parseFloat(s.borderTopWidth) === 0) continue;
      if (p === 'color' && !hasText) continue;
      if (shape && (s[p] === 'none' || el.getAttribute(p) === 'none')) continue;
      const h = hex(s[p] || ''); if (h && !palette.includes(h)) off.add(`${h} (${p})`);
    }
  }
  for (const svg of document.querySelectorAll('#canvas .marker svg')) {
    const r = svg.getBoundingClientRect(); if (r.width < 4 || r.height < 4) out.push('icon not visible (zero size)'); }
  if (off.size) out.push('off-palette colours: ' + [...off].join(', '));
  if (small.size) out.push(`text under ${minPx}px: ` + [...small].slice(0, 5).join('; '));
  return [...new Set(out)];
}"""


def _offline(route: Route) -> None:
    url = route.request.url
    if url.startswith(("file:", "data:", "about:")):
        route.continue_()
    else:
        route.abort()


def render(spec: Spec, brand: Brand, out_dir: Path) -> RenderResult:
    """Render the next version (v1, v2, …) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("v*.html"))) + 1
    html_path, png, preview = out_dir / f"v{n}.html", out_dir / f"v{n}.png", out_dir / f"v{n}_preview.png"
    html, missing = build_html(spec, brand)
    html_path.write_text(html)
    (out_dir / f"v{n}.json").write_text(spec.model_dump_json(indent=1))
    width = brand.canvas_width
    palette = sorted(set(brand.palette().values()) | {"#FFFFFF"})
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        try:
            page = browser.new_page(viewport={"width": width, "height": 600})
            page.route("**/*", _offline)
            page.goto(html_path.resolve().as_uri())
            page.evaluate("document.fonts.ready.then(() => true)")
            checks = page.evaluate(AUTO_CHECKS, [width, config.MAX_CANVAS_HEIGHT, brand.fonts(), palette, config.MIN_TEXT_PX])
            page.locator("#canvas").screenshot(path=str(png))
        finally:
            browser.close()
    checks += [f"icon not found: {m} (use search_icons)" for m in missing]
    im = Image.open(png)
    w = config.PREVIEW_WIDTH
    im.resize((w, round(im.height * w / im.width)), Image.Resampling.LANCZOS).save(preview)
    return RenderResult(n, html_path, png, preview, im.width, im.height, checks, missing)


def main() -> None:  # dev helper: uv run python -m studio_agents.render.build spec.json <client> out/
    import sys
    from studio_agents import brand as brandmod
    spec = Spec.model_validate_json(Path(sys.argv[1]).read_text())
    r = render(spec, brandmod.load(sys.argv[2]), Path(sys.argv[3]))
    print(json.dumps(r.to_json(), indent=1))


if __name__ == "__main__":
    main()
