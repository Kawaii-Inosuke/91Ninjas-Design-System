"""New clients from past work (5d, D-031; Claude drafts since D-033).

    collect(folder) → Inputs     the usable files: past designs, fonts, guideline documents
    measure(folder) → Facts      no AI: the colours actually used (pixel coverage snapped to the colours the SVGs
                                 declare), corner radii, canvas sizes, declared fonts, guideline text, previews

Claude reads the facts and previews through the connector (`measure_past_work`), writes DESIGN.md and the rules,
and `save_new_client` installs them with packs.create_client.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from studio_agents import brand as brandmod
from studio_agents import config, packs

MAX_INPUT_FILES = 80
MAX_PAST = 30
PREVIEWS = 6                 # images the agent looks at
PREVIEW_WIDTH = 1280
ANALYSIS_WIDTH = 320         # colour coverage is measured on a small copy
DOC_TEXT_MAX = 12000         # guideline text passed to the agent
DOC_EXT = {".md", ".txt"}
RASTER_EXT = {".png", ".jpg", ".jpeg", ".webp"}
SVG_COLOR = re.compile(r"(?:fill|stroke|stop-color|color)\s*[=:]\s*[\"']?\s*(#[0-9a-fA-F]{3,8})\b")
SVG_RX = re.compile(r"\brx\s*=\s*[\"']?(\d+(?:\.\d+)?)")
SVG_FONT = re.compile(r"font-family\s*[=:]\s*[\"']?([^;\"'>]+)")
SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)
SVG_W = re.compile(r"\bwidth\s*=\s*[\"']?(\d+(?:\.\d+)?)")
SVG_H = re.compile(r"\bheight\s*=\s*[\"']?(\d+(?:\.\d+)?)")
SVG_VIEWBOX = re.compile(r"viewBox\s*=\s*[\"']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+(\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)")


class OnboardError(ValueError):
    pass


# ── measuring (no AI) ───────────────────────────────────────────────────────────────────────────────

@dataclass
class Inputs:
    past: list[Path] = field(default_factory=list)
    fonts: list[Path] = field(default_factory=list)
    docs: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class Facts:
    files: int
    canvas: list[list[int]]                 # [width, height] of each past design
    colors: list[dict]                      # {"hex", "share" (pixel coverage, 0–1), "svg_uses"}
    radii: list[list[float]]                # [radius, count] from SVG rx, most used first
    fonts_declared: list[str]               # font-family names in SVGs and font file names
    font_files: list[str]
    guidelines: str                         # text of the .md/.txt documents (trimmed)
    previews: list[str]                     # PNG paths the agent looks at
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        cols = ", ".join(f"{c['hex']} ({c['share'] * 100:.1f}% of pixels, {c['svg_uses']} SVG uses)" for c in self.colors)
        widths = Counter(w for w, _ in self.canvas).most_common(3)
        heights = [h for _, h in self.canvas]
        size = (", ".join(f"{w}px wide ({n})" for w, n in widths) + f"; heights {min(heights)}–{max(heights)}px") if self.canvas else "unknown"
        return "\n".join([
            f"Past designs: {self.files}. Canvas: {size}",
            f"Colours measured, most coverage first: {cols or 'none'}",
            "Corner radii in SVGs (px, uses): " + (", ".join(f"{r:g} ({n})" for r, n in self.radii[:6]) or "none found"),
            "Fonts named in files: " + (", ".join(self.fonts_declared) or "none (text is outlined: judge from the images)"),
            "Font files given: " + (", ".join(self.font_files) or "none"),
            *(f"Note: {n}" for n in self.notes),
        ])


def collect(folder: Path) -> Inputs:
    """The usable files in a folder (and its subfolders): past designs, fonts, guideline documents."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise OnboardError(f"{folder} is not a folder")
    out = Inputs()
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(folder).parts))
    for p in files[: MAX_INPUT_FILES * 3]:
        ext = p.suffix.lower()
        if ext in packs.FONT_EXT:
            out.fonts.append(p)
        elif ext in DOC_EXT:
            out.docs.append(p)
        elif ext in packs.IMAGE_EXT and ext != ".pdf":
            out.past.append(p)
        else:
            out.skipped.append(p.name)
    if len(out.past) > MAX_PAST:
        out.skipped += [p.name for p in out.past[MAX_PAST:]]
        out.past = out.past[:MAX_PAST]
    if len(out.past) < config.PACK_PAST_MIN:
        raise OnboardError(f"the folder needs at least {config.PACK_PAST_MIN} past designs (SVG, PNG, JPEG or WebP); "
                           f"found {len(out.past)}")
    return out


def _hex(c: str) -> str | None:
    c = c.strip().lstrip("#")
    if len(c) in (3, 4):
        c = "".join(ch * 2 for ch in c[:3])
    elif len(c) == 8:
        c = c[:6]
    return f"#{c.upper()}" if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c) else None


def _render_svgs(svgs: list[Path], out_dir: Path) -> dict[Path, Path]:
    """Each SVG as a PNG (offline Chromium, as the renderer uses)."""
    if not svgs:
        return {}
    from playwright.sync_api import sync_playwright
    done: dict[Path, Path] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            for i, src in enumerate(svgs):
                dst = out_dir / f"svg-{i:02d}.png"
                try:
                    page.goto(src.as_uri())
                    page.locator("svg").first.screenshot(path=str(dst), timeout=20000)
                    done[src] = dst
                except Exception:  # an SVG that doesn't render is measured from its text only
                    continue
        finally:
            browser.close()
    return done


def _coverage(png: Path) -> list[tuple[tuple[int, int, int], float]]:
    with Image.open(png) as im:
        im = im.convert("RGB")
        if im.width > ANALYSIS_WIDTH:
            im = im.resize((ANALYSIS_WIDTH, max(1, round(im.height * ANALYSIS_WIDTH / im.width))))
        q = im.quantize(colors=16)
        pal = q.getpalette() or []
        total = q.width * q.height
        out = []
        for count, idx in q.getcolors() or []:
            rgb = tuple(pal[idx * 3: idx * 3 + 3])
            if len(rgb) == 3:
                out.append((rgb, count / total))
        return out


def _merge(colors: list[tuple[tuple[int, int, int], float]], images: int) -> list[list[Any]]:
    """Close colours (anti-aliasing, compression) count as one; shares are averaged over the images."""
    groups: list[list[Any]] = []   # [rgb, share]
    for rgb, share in sorted(colors, key=lambda c: -c[1]):
        for g in groups:
            if sum((a - b) ** 2 for a, b in zip(g[0], rgb)) < 18 ** 2:
                g[1] += share
                break
        else:
            groups.append([rgb, share])
    return [[f"#{r:02X}{g:02X}{b:02X}", s / max(images, 1)] for (r, g, b), s in groups]


def _svg_size(text: str) -> list[int] | None:
    tag = SVG_TAG.search(text)
    if not tag:
        return None
    w, h = SVG_W.search(tag.group(0)), SVG_H.search(tag.group(0))
    if w and h:
        return [round(float(w.group(1))), round(float(h.group(1)))]
    if vb := SVG_VIEWBOX.search(tag.group(0)):
        return [round(float(vb.group(1))), round(float(vb.group(2)))]
    return None


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _colors(measured: list[list[Any]], declared: Counter[str]) -> list[dict]:
    """Pixel coverage attached to the exact colours the SVGs declare when one is close (anti-aliasing and
    quantising shift colours a little); the rest stay as measured if they cover enough to matter."""
    out: dict[str, dict] = {h: {"hex": h, "share": 0.0, "svg_uses": n} for h, n in declared.items()}
    # Pure white and black are candidates too: backgrounds often aren't declared (the page is white).
    candidates = list(declared) + [h for h in ("#FFFFFF", "#000000") if h not in declared]
    for h, share in measured:
        rgb = _rgb(h)
        near = min(candidates, key=lambda d: sum((a - b) ** 2 for a, b in zip(_rgb(d), rgb)))
        if sum((a - b) ** 2 for a, b in zip(_rgb(near), rgb)) <= 16 ** 2:
            out.setdefault(near, {"hex": near, "share": 0.0, "svg_uses": 0})["share"] += share
        elif share >= 0.003:
            out.setdefault(h, {"hex": h, "share": 0.0, "svg_uses": 0})["share"] += share
    ranked = sorted(out.values(), key=lambda c: (-c["share"], -c["svg_uses"]))
    for c in ranked:
        c["share"] = round(c["share"], 4)
    return ranked


def measure(folder: Path, work: Path) -> tuple[Inputs, Facts]:
    inputs = collect(folder)
    (work / "previews").mkdir(parents=True, exist_ok=True)
    svgs = [p for p in inputs.past if p.suffix.lower() == ".svg"]
    rendered = _render_svgs(svgs, work / "previews")
    svg_colors: Counter[str] = Counter()
    radii: Counter[float] = Counter()
    fonts: Counter[str] = Counter()
    canvas: list[list[int]] = []
    pngs: list[Path] = []
    notes: list[str] = []
    for p in inputs.past:
        if p.suffix.lower() == ".svg":
            text = p.read_text(errors="ignore")[:5_000_000]
            svg_colors.update(h for c in SVG_COLOR.findall(text) if (h := _hex(c)))
            radii.update(r for r in (float(x) for x in SVG_RX.findall(text)) if r > 0)   # square corners say nothing
            fonts.update(f.split(",")[0].strip().strip("'\"") for f in SVG_FONT.findall(text))
            if size := _svg_size(text):
                canvas.append(size)
            if p in rendered:
                pngs.append(rendered[p])
            else:
                notes.append(f"{p.name} could not be rendered; measured from its markup only")
        else:
            try:
                with Image.open(p) as im:
                    canvas.append([im.width, im.height])
                pngs.append(p)
            except OSError:
                notes.append(f"{p.name} is not a readable image")
    coverage: list[tuple[tuple[int, int, int], float]] = []
    for png in pngs:
        coverage += _coverage(png)
    colors = _colors(_merge(coverage, len(pngs)), svg_colors)
    previews = []
    for i, png in enumerate(pngs[:PREVIEWS]):
        dst = work / "previews" / f"preview-{i + 1}.png"
        with Image.open(png) as im:
            im = im.convert("RGB")
            if im.width > PREVIEW_WIDTH:
                im = im.resize((PREVIEW_WIDTH, max(1, round(im.height * PREVIEW_WIDTH / im.width))))
            im.save(dst)
        previews.append(str(dst))
    guidelines = ""
    for d in inputs.docs:
        guidelines += f"\n\n## {d.name}\n" + d.read_text(errors="ignore")
    if len(guidelines) > DOC_TEXT_MAX:
        notes.append("the guideline documents were long; only the start was read")
    font_files = [p.name for p in inputs.fonts]
    declared = [f for f, _ in fonts.most_common(6) if f] + sorted({p.stem.rpartition("-")[0] or p.stem for p in inputs.fonts})
    facts = Facts(len(inputs.past), canvas, colors[:24], [[r, n] for r, n in radii.most_common(8)],
                  list(dict.fromkeys(declared)), font_files, guidelines[:DOC_TEXT_MAX].strip(), previews, notes)
    return inputs, facts


# ── helpers ──────────────────────────────────────────────────────────────────────────────

def client_id_for(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return base if len(base) >= 2 else f"client-{uuid.uuid4().hex[:4]}"


def summarize(design_md: str, rules_md: str) -> dict:
    tokens, _ = brandmod.parse_design_md(design_md)
    typo = tokens.get("typography") or {}
    return {
        "name": tokens.get("name"),
        "description": tokens.get("description"),
        "colors": tokens.get("colors") or {},
        "fonts": sorted({str(t.get("fontFamily")) for t in typo.values() if isinstance(t, dict)}),
        "layouts": (tokens.get("infographic") or {}).get("layouts") or [],
        "rules": sum(1 for line in rules_md.splitlines() if re.match(r"^[A-Z]?\d+\.", line.strip())),
        "problems": brandmod.validate_tokens(tokens),
    }
