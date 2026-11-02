"""A client's brand pack: `DESIGN.md` front matter (tokens) + prose, `critic_rules.md`, fonts, icons, past work.

DESIGN.md follows the format the infographic skill uses (google-labs-code/design.md): YAML front matter with
`colors`, `typography` (title/heading/body/…: fontFamily, fontSize, fontWeight, lineHeight), `rounded`,
`spacing`, and an `infographic` block (canvas, layouts, layout_window, icon). See `docs/TRD.md` › Client packs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from studio_agents import config

BASE_RULES = Path(__file__).with_name("rules") / "base_critic_rules.md"
FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
HEX = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class BrandError(ValueError):
    pass


@dataclass
class Brand:
    client: str
    dir: Path
    tokens: dict[str, Any]
    guide: str                                   # DESIGN.md prose after the front matter
    rules: str                                   # the client's critic_rules.md
    problems: list[str] = field(default_factory=list)   # non-fatal issues found while loading

    # ── tokens ──────────────────────────────────────────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return str(self.tokens.get("name") or self.client)

    @property
    def version(self) -> str:
        return str(self.tokens.get("brand_version", "0"))

    @property
    def infographic(self) -> dict[str, Any]:
        return self.tokens.get("infographic") or {}

    @property
    def canvas_width(self) -> int:
        return int((self.infographic.get("canvas") or {}).get("width") or config.DEFAULT_CANVAS_WIDTH)

    @property
    def canvas_height(self) -> int | None:
        h = (self.infographic.get("canvas") or {}).get("height")
        return int(h) if isinstance(h, (int, float)) else None

    @property
    def layouts(self) -> list[str]:
        return list(self.infographic.get("layouts") or [])

    @property
    def layout_window(self) -> int:
        return int(self.infographic.get("layout_window") or config.LAYOUT_WINDOW)

    @property
    def icon_style(self) -> dict[str, Any]:
        return {"scale": 0.6, "stroke": 1.5, **(self.infographic.get("icon") or {})}

    def palette(self) -> dict[str, str]:
        """Allowed colours by token name, as #RRGGBB (alpha dropped)."""
        return {k: str(v)[:7].upper() for k, v in (self.tokens.get("colors") or {}).items() if HEX.match(str(v))}

    def fonts(self) -> list[str]:
        return sorted({str(t["fontFamily"]) for t in (self.tokens.get("typography") or {}).values() if t.get("fontFamily")})

    def css_vars(self) -> str:
        """Tokens as :root custom properties (--color-*, --radius-*, --space-*, --font-<style>-*)."""
        out = [f"--color-{k}: {v};" for k, v in (self.tokens.get("colors") or {}).items()]
        out += [f"--radius-{k}: {v};" for k, v in (self.tokens.get("rounded") or {}).items()]
        out += [f"--space-{k}: {v};" for k, v in (self.tokens.get("spacing") or {}).items()]
        for style, t in (self.tokens.get("typography") or {}).items():
            out += [f"--font-{style}-family: '{t.get('fontFamily', 'sans-serif')}';",
                    f"--font-{style}-size: {t.get('fontSize', '24px')};",
                    f"--font-{style}-weight: {t.get('fontWeight', 400)};",
                    f"--font-{style}-line: {t.get('lineHeight', 1.3)};"]
        return ":root {\n  " + "\n  ".join(out) + "\n}"

    # ── files ───────────────────────────────────────────────────────────────────────────────────────
    def font_files(self) -> list[Path]:
        d = self.dir / "assets" / "fonts"
        return sorted(p for p in d.glob("*") if p.suffix.lower() in {".woff2", ".woff", ".ttf", ".otf"}) if d.is_dir() else []

    def font_faces(self) -> str:
        """@font-face rules for files named <Family>-<weight>.<ext> (the skill's convention)."""
        faces = []
        for f in self.font_files():
            family, _, weight = f.stem.rpartition("-")
            if not family or not weight.isdigit():
                continue
            fmt = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}[f.suffix.lower()]
            faces.append(f"@font-face {{ font-family: '{family}'; font-weight: {weight}; "
                         f"src: url('{f.as_uri()}') format('{fmt}'); }}")
        return "\n".join(faces)

    def brand_css(self) -> str:
        p = self.dir / "assets" / "brand.css"
        return p.read_text() if p.is_file() else ""

    def past_work(self) -> list[Path]:
        d = self.dir / "past"
        exts = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf"}
        return sorted(p for p in d.iterdir() if p.suffix.lower() in exts) if d.is_dir() else []

    def critic_rules(self) -> str:
        """Client rules first; they win over the base rules where they conflict."""
        return f"{self.rules.strip()}\n\n{BASE_RULES.read_text().strip()}\n"


def parse_design_md(text: str) -> tuple[dict[str, Any], str]:
    m = FRONT_MATTER.match(text)
    if not m:
        raise BrandError("DESIGN.md must start with YAML front matter between --- lines")
    try:
        tokens = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise BrandError(f"DESIGN.md front matter is not valid YAML: {e}") from None
    if not isinstance(tokens, dict):
        raise BrandError("DESIGN.md front matter must be a mapping")
    return tokens, m.group(2)


def validate_tokens(tokens: dict[str, Any]) -> list[str]:
    """Problems that make a pack unusable (empty list = fine)."""
    problems = []
    colors = tokens.get("colors")
    if not isinstance(colors, dict) or not colors:
        problems.append("colors: at least one colour is required")
    else:
        problems += [f"colors.{k}: {v!r} is not #RRGGBB" for k, v in colors.items() if not HEX.match(str(v))]
        for need in ("surface", "ink"):
            if need not in colors:
                problems.append(f"colors.{need} is required (page background / body text)")
    typo = tokens.get("typography")
    if not isinstance(typo, dict) or not typo:
        problems.append("typography: at least a title, heading and body style are required")
    else:
        for style in ("title", "heading", "body"):
            t = typo.get(style)
            if not isinstance(t, dict) or not t.get("fontFamily"):
                problems.append(f"typography.{style}.fontFamily is required")
    return problems


def load(client: str, root: Path | None = None) -> Brand:
    d = (root or config.clients_dir()) / client
    design = d / "DESIGN.md"
    if not design.is_file():
        raise BrandError(f"client {client!r} is not installed (no {design})")
    tokens, guide = parse_design_md(design.read_text())
    problems = validate_tokens(tokens)
    if problems:
        raise BrandError(f"{client}: " + "; ".join(problems))
    rules = (d / "critic_rules.md").read_text() if (d / "critic_rules.md").is_file() else ""
    b = Brand(client, d, tokens, guide, rules)
    if not rules.strip():
        b.problems.append("no critic_rules.md: only the base rules apply")
    if len(b.past_work()) < config.PACK_PAST_MIN:
        b.problems.append(f"fewer than {config.PACK_PAST_MIN} past infographics: the agents have little style reference")
    missing = [f for f in b.fonts() if not any(p.stem.rpartition("-")[0] == f for p in b.font_files())]
    if missing:
        b.problems.append(f"no font files for {', '.join(missing)}: the system font must be installed")
    return b


def list_clients(root: Path | None = None) -> list[str]:
    d = root or config.clients_dir()
    return sorted(p.name for p in d.iterdir() if (p / "DESIGN.md").is_file()) if d.is_dir() else []
