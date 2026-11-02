"""Icons: the bundled Tabler outline set (MIT, scripts/build_icons.py) plus a client's own assets/icons/*.svg."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1] / "assets" / "icons" / "tabler-outline.json"


@lru_cache(maxsize=1)
def _bundle() -> dict[str, dict]:
    return json.loads(BUNDLE.read_text())


def exists(name: str, client_icons: Path | None = None) -> bool:
    return name in _bundle() or bool(client_icons and (client_icons / f"{name}.svg").is_file())


def svg(name: str, size: int, stroke: float = 1.5, client_icons: Path | None = None) -> str:
    """Inline SVG in brand style: currentColor, one stroke weight, `size` px. Empty string when unknown.
    A client's own icon (assets/icons/<name>.svg, sanitised on import) wins over Tabler."""
    own = client_icons / f"{name}.svg" if client_icons else None
    if own and own.is_file():
        text = own.read_text()
        text = re.sub(r'\s(width|height|class|stroke-width)="[^"]*"', "", text)
        text = re.sub(r'\sfill="(?!none)[^"]*"', ' fill="currentColor"', text)
        text = re.sub(r'\sstroke="(?!none)[^"]*"', ' stroke="currentColor"', text)
        return text.replace("<svg", f'<svg width="{size}" height="{size}" stroke-width="{stroke}" aria-hidden="true"', 1)
    icon = _bundle().get(name)
    if not icon:
        return ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{icon["body"]}</svg>')


def search(query: str, limit: int = 12) -> list[str]:
    """Icon names for a word or phrase, best first (exact name, name contains, tag match)."""
    words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if w]
    if not words:
        return []
    scored = []
    for name, icon in _bundle().items():
        tags = " ".join(str(t) for t in icon["tags"]).lower()
        s = 0
        for w in words:
            if name == w:
                s += 10
            elif w in name.split("-"):
                s += 5
            elif w in name:
                s += 3
            elif w in tags:
                s += 1
        if s:
            scored.append((-s, len(name), name))
    return [n for _, _, n in sorted(scored)[:limit]]
