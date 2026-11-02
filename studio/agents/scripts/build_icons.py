"""Bundle the Tabler outline icons (MIT) into one JSON file the renderer and agents use.

    curl -sSfL https://registry.npmjs.org/@tabler/icons/-/icons-3.50.0.tgz -o /tmp/tabler/icons.tgz
    (cd /tmp/tabler && tar -xzf icons.tgz)
    uv run python scripts/build_icons.py /tmp/tabler/package

Writes studio_agents/assets/icons/tabler-outline.json ({name: {"body": inner SVG, "tags": [...], "category"}})
and LICENSE-tabler.txt next to it. Pinned source: @tabler/icons 3.50.0,
sha256 4c2eb678ed71cdcbf57a3e899c4e91f3445d680fbc1d57d27c7083a321c47303.
"""
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "studio_agents" / "assets" / "icons"


def main(pkg: Path) -> None:
    meta = json.loads((pkg / "icons.json").read_text())
    icons = {}
    for svg in sorted((pkg / "icons" / "outline").glob("*.svg")):
        text = svg.read_text()
        inner = re.search(r"<svg[^>]*>(.*)</svg>", text, re.S)
        if not inner:
            continue
        body = re.sub(r'<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*/>', "", inner.group(1))  # bounding box helper
        body = re.sub(r"\s+", " ", body).strip()
        m = meta.get(svg.stem, {})
        icons[svg.stem] = {"body": body, "tags": m.get("tags", []), "category": m.get("category", "")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tabler-outline.json").write_text(json.dumps(icons, separators=(",", ":")))
    (OUT / "LICENSE-tabler.txt").write_text((pkg / "LICENSE").read_text())
    print(f"{len(icons)} icons → {OUT / 'tabler-outline.json'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
