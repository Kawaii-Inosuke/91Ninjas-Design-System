"""Brand Onboarder (5d, D-031): a new client from a folder of past work.

    measure(folder)  → Facts      no AI: the colours actually used (pixel coverage and SVG counts), corner radii,
                                   canvas sizes, declared fonts, guideline text, and preview images
    draft(...)       → BrandDraft the onboarder agent (vision) fills fixed slots: colour roles, type styles, radii,
                                   spacing, layouts, a style guide and a rulebook
    to_design_md / to_rules_md     the draft as the pack's DESIGN.md and critic_rules.md
    OnboardManager                 jobs for the panel: measure → draft → the designer reviews (and may edit the
                                   files) → save as a client with packs.create_client

The slots are the ones the templates read (--color-primary, --font-title-*, --radius-sm…), so every drafted client
renders. Measuring and saving are separate from drafting so another AI (Claude through the connector) can draft.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import traceback
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from PIL import Image
from pydantic import BaseModel, Field, field_validator

from studio_agents import brand as brandmod
from studio_agents import config, models, packs
from studio_agents.render.spec import LAYOUTS, LAYOUT_GUIDE
from studio_agents.steps import fenced, prompt

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


# ── drafting (AI) ───────────────────────────────────────────────────────────────────────────────────

def _color(v: Any) -> str:
    h = _hex(str(v or ""))
    if not h:
        raise ValueError(f"{v!r} is not a colour: use #RRGGBB")
    return h


class NamedColor(BaseModel):
    name: str
    hex: str

    @field_validator("hex")
    @classmethod
    def _c(cls, v: str) -> str:
        return _color(v)


class Palette(BaseModel):
    """Colours as #RRGGBB (checked after the answer: not every model honours patterns in a schema)."""
    primary: str = Field(description="main brand colour: titles, filled pills, connectors")
    accent: str = Field(description="second brand colour: markers, outlines, icons")
    ink: str = Field(description="body text")
    surface: str = Field(description="page background")
    tint: str = Field(description="light card / panel fill")
    cell: str = Field(description="neutral table / card cell fill")
    line: str = Field(description="neutral outlines and dividers")
    on_primary: str = Field(description="text on primary fills")
    header_bar: str | None = Field(None, description="title band fill if the designs have one, #RRGGBB or #RRGGBBAA")
    others: list[NamedColor] = Field(default_factory=list, description="other brand colours")

    @field_validator("primary", "accent", "ink", "surface", "tint", "cell", "line", "on_primary")
    @classmethod
    def _c(cls, v: str) -> str:
        return _color(v)

    @field_validator("header_bar")
    @classmethod
    def _bar(cls, v: str | None) -> str | None:
        if not v:
            return None
        raw = v.strip().lstrip("#")
        if len(raw) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
            return f"#{raw.upper()}"
        return _color(v)


class TypeStyle(BaseModel):
    fontFamily: str
    fontSize: int = Field(ge=8, le=200, description="px at the canvas width")
    fontWeight: int = Field(ge=100, le=900)
    lineHeight: float = Field(ge=0.8, le=2.5)


class Typography(BaseModel):
    title: TypeStyle
    heading: TypeStyle
    body: TypeStyle
    label: TypeStyle
    marker: TypeStyle


class Rule(BaseModel):
    section: str = Field(description="Locked, Typography, Components, Layout…")
    text: str


class BrandDraft(BaseModel):
    name: str
    description: str = Field(max_length=300)
    palette: Palette
    typography: Typography
    radius_sm: int = Field(ge=0, le=200)
    radius_md: int = Field(ge=0, le=200)
    radius_lg: int = Field(ge=0, le=200)
    space_page: int = Field(ge=0, le=400)
    space_section: int = Field(ge=0, le=400)
    space_item: int = Field(ge=0, le=200)
    canvas_width: int = Field(ge=320, le=6000)
    content_max_width: int | None = Field(None, ge=200, le=6000)
    layouts: list[str] = Field(min_length=1, description="family/variant entries the designs use")
    icon_note: str = Field("", description="icon style: line or filled, colour, stroke")
    guide: str = Field(description="markdown style guide: look and feel, composition, components, do's and don'ts")
    rules: list[Rule] = Field(min_length=5, max_length=40)


def _layouts_menu() -> str:
    return "\n".join(f"- {fam}{' (' + '|'.join(v) + ')' if v else ''}: {LAYOUT_GUIDE[fam]}" for fam, v in LAYOUTS.items())


def draft(name: str, facts: Facts, notes: str = "") -> BrandDraft:
    text = "\n\n".join(p for p in [
        f"New client: {name}",
        "Measured from the past designs (exact; trust these numbers over impressions):\n" + facts.summary(),
        "Layout families our templates make (use family/variant names from here; other shapes the client uses may "
        "be listed by their own name and are made by `custom`):\n" + _layouts_menu(),
        fenced("guideline_documents", facts.guidelines) if facts.guidelines else "",
        fenced("designer_notes", notes) if notes.strip() else "",
        f"{len(facts.previews)} of the past designs are attached as images.",
    ] if p)
    out = models.ask("onboarder", prompt("onboarder"), text, BrandDraft, images=[Path(p) for p in facts.previews])
    return BrandDraft.model_validate(out.model_dump())


def to_design_md(d: BrandDraft, client: str) -> str:
    p = d.palette
    colors: dict[str, str] = {"primary": p.primary, "accent": p.accent, "tint": p.tint, "ink": p.ink, "surface": p.surface,
                              "cell": p.cell, "line": p.line, "on-primary": p.on_primary}
    if p.header_bar:
        colors["header-bar"] = p.header_bar
    for c in p.others:
        key = re.sub(r"[^a-z0-9-]+", "-", c.name.lower()).strip("-")
        if key and key not in colors:
            colors[key] = c.hex
    typo = {}
    for role in ("title", "heading", "body", "label", "marker"):
        st: TypeStyle = getattr(d.typography, role)
        typo[role] = {"fontFamily": st.fontFamily, "fontSize": f"{st.fontSize}px", "fontWeight": st.fontWeight, "lineHeight": st.lineHeight}
    infographic: dict[str, Any] = {"canvas": {"width": d.canvas_width, "height": "auto"}, "layouts": d.layouts}
    if d.content_max_width:
        infographic["content_max_width"] = d.content_max_width
    tokens = {
        "version": "alpha", "name": d.name, "client": client, "brand_version": "0.1.0",
        "updated": datetime.now().date().isoformat(),
        "description": d.description,
        "drafted_by": "91Ninjas Studio Brand Onboarder: check before relying on it",
        "colors": colors, "typography": typo,
        "rounded": {"sm": f"{d.radius_sm}px", "md": f"{d.radius_md}px", "lg": f"{d.radius_lg}px", "full": "999px"},
        "spacing": {"page": f"{d.space_page}px", "section": f"{d.space_section}px", "item": f"{d.space_item}px"},
        "infographic": infographic,
    }
    front = yaml.safe_dump(tokens, sort_keys=False, allow_unicode=True, width=120)
    icon = f"\n\n## Icons\n{d.icon_note.strip()}" if d.icon_note.strip() else ""
    return f"---\n{front}---\n\n# {d.name}: design language\n\n{d.guide.strip()}{icon}\n"


def to_rules_md(d: BrandDraft) -> str:
    out = [f"# {d.name} critic rules", "Drafted by the Brand Onboarder from past work; the designer checked them.", ""]
    n = 0
    for section in dict.fromkeys(r.section.strip() or "Rules" for r in d.rules):
        out.append(f"## {section}")
        for r in (r for r in d.rules if (r.section.strip() or "Rules") == section):
            n += 1
            out.append(f"R{n}. {r.text.strip()}")
        out.append("")
    return "\n".join(out)


# ── jobs for the panel ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Job:
    id: str
    name: str
    folder: str
    dir: Path
    notes: str = ""
    status: str = "measuring"           # measuring → drafting → ready | failed | saved
    error: str = ""
    facts: dict | None = None
    summary: dict | None = None         # what the panel shows: swatches, fonts, layouts, rules
    client: str | None = None

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "dir"} | {"dir": str(self.dir),
                "design_md": str(self.dir / "DESIGN.md"), "rules_md": str(self.dir / "critic_rules.md")}


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


class OnboardManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def start(self, name: str, folder: str, notes: str = "", run_async: bool = True) -> Job:
        name = name.strip()
        if not name:
            raise OnboardError("give the client a name")
        collect(Path(folder))   # fails early: not a folder, too few past designs
        jid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = Job(jid, name, str(Path(folder).expanduser()), config.data_dir() / "drafts" / jid, notes)
        job.dir.mkdir(parents=True, exist_ok=True)
        self.jobs[jid] = job
        if run_async:
            threading.Thread(target=self._run, args=(job,), name=f"onboard-{jid}", daemon=True).start()
        else:
            self._run(job)
        return job

    def get(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if not job:
            raise OnboardError(f"no draft {jid}")
        return job

    def _run(self, job: Job) -> None:
        try:
            _, facts = measure(Path(job.folder), job.dir)
            job.facts = asdict(facts)
            (job.dir / "facts.json").write_text(json.dumps(job.facts, indent=1))
            job.status = "drafting"
            d = draft(job.name, facts, job.notes)
            (job.dir / "DESIGN.md").write_text(to_design_md(d, client_id_for(job.name)))
            (job.dir / "critic_rules.md").write_text(to_rules_md(d))
            job.summary = summarize((job.dir / "DESIGN.md").read_text(), (job.dir / "critic_rules.md").read_text())
            job.status = "ready"
        except Exception as e:  # a draft ends with a reason, never hangs
            job.status, job.error = "failed", str(e)
            (job.dir / "error.txt").write_text(traceback.format_exc()[-4000:])

    def refresh(self, jid: str) -> Job:
        """Re-read the draft files (the designer may have edited them) for the summary."""
        job = self.get(jid)
        if job.status == "ready":
            try:
                job.summary = summarize((job.dir / "DESIGN.md").read_text(), (job.dir / "critic_rules.md").read_text())
            except (OSError, brandmod.BrandError) as e:   # an edit broke the file: say so, keep the job
                job.summary = {**(job.summary or {}), "problems": [str(e)]}
        return job

    def save(self, jid: str, client: str | None = None, overwrite: bool = False) -> str:
        """The (possibly edited) draft becomes a client: DESIGN.md, the rulebook, the past designs, fonts and documents."""
        job = self.refresh(jid)
        if job.status != "ready":
            raise OnboardError(f"the draft is {job.status}, not ready")
        cid = packs.check_client_id(client or client_id_for(job.name))
        inputs = collect(Path(job.folder))
        try:
            packs.create_client(cid, design_md=(job.dir / "DESIGN.md").read_text(), critic_rules=(job.dir / "critic_rules.md").read_text(),
                                past=inputs.past, fonts=inputs.fonts, sources=inputs.docs, overwrite=overwrite)
        except (packs.PackError, brandmod.BrandError) as e:
            raise OnboardError(str(e)) from None
        job.status, job.client = "saved", cid
        return cid

    def discard(self, jid: str) -> None:
        job = self.jobs.pop(jid, None)
        if job:
            shutil.rmtree(job.dir, ignore_errors=True)
