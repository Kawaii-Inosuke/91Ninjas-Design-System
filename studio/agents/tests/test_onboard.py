"""New clients from past work (D-031, D-033): measuring, and Claude's DESIGN.md installed through the connector."""
import asyncio
from pathlib import Path

import pytest

from studio_agents import brand as brandmod
from studio_agents import onboard


def svg(w, h, blocks):
    rects = "".join(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="{rx}" fill="{c}"/>' for x, y, bw, bh, rx, c in blocks)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{rects}</svg>'


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "acme past work"
    d.mkdir()
    for i in range(3):
        (d / f"design-{i}.svg").write_text(svg(1080, 900 + i * 100, [
            (0, 0, 1080, 1200, 0, "#ffffff"), (0, 0, 1080, 120, 0, "#0A7A5A"),
            (80, 200, 920, 160, 18, "#E3F4EE"), (80, 400, 920, 160, 18, "#e3f4ee"), (100, 220, 60, 60, 30, "#F5A623")]))
    (d / "notes").mkdir()
    (d / "notes" / "brand.md").write_text("Acme uses Poppins for everything. Never use red.")
    (d / "logo.ai").write_text("not used")
    return d


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("STUDIO_DATA_DIR", str(d))
    return d


DESIGN_MD = (Path(onboard.__file__).with_name("claude") / "new_client.md").read_text().split("```\n")[1].split("```")[0]


def test_measuring_finds_the_files_and_the_real_colours(folder, tmp_path):
    inputs, facts = onboard.measure(folder, tmp_path / "work")
    assert len(inputs.past) == 3 and [p.name for p in inputs.docs] == ["brand.md"] and "logo.ai" in inputs.skipped
    hexes = [c["hex"] for c in facts.colors]
    assert hexes[0] == "#FFFFFF", "the page covers the most"
    assert {"#0A7A5A", "#E3F4EE", "#F5A623"} <= set(hexes)
    tint = next(c for c in facts.colors if c["hex"] == "#E3F4EE")
    assert tint["svg_uses"] == 6 and tint["share"] > 0.05, "case-insensitive, and pixels land on the declared colour"
    assert facts.canvas == [[1080, 900], [1080, 1000], [1080, 1100]]
    assert facts.radii[0][0] == 18 and "Poppins" in facts.guidelines
    assert len(facts.previews) == 3 and all(Path(p).exists() for p in facts.previews)
    assert "1080px wide (3); heights 900–1100px" in facts.summary()


def test_too_few_designs_or_no_folder_is_refused(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    with pytest.raises(onboard.OnboardError, match="at least 3"):
        onboard.collect(tmp_path)
    with pytest.raises(onboard.OnboardError, match="not a folder"):
        onboard.collect(tmp_path / "missing")


def test_the_template_claude_fills_is_a_valid_pack(folder, data):
    """The DESIGN.md example Claude is shown is itself valid, and save_new_client installs it with the folder's files."""
    tokens, _ = brandmod.parse_design_md(DESIGN_MD)
    assert brandmod.validate_tokens(tokens) == []
    from studio_agents import mcp_server
    out = mcp_server.save_new_client("Acme Health", str(folder), DESIGN_MD, "# rules\n## Locked\nR1. White page.")
    assert '"client": "acme-health"' in out, out
    b = brandmod.load("acme-health")
    assert len(b.past_work()) == 3 and (b.dir / "source" / "brand.md").exists()
    assert "error" in mcp_server.save_new_client("Acme Health", str(folder), DESIGN_MD, "# rules"), "exists: not replaced unasked"
    assert "error" in mcp_server.save_new_client("Bad", str(folder), "no front matter", "# rules")
    s = onboard.summarize(DESIGN_MD, "R1. x\nR2. y")
    assert s["rules"] == 2 and s["fonts"] == ["Poppins"] and s["problems"] == []


def test_measure_past_work_gives_claude_the_facts_the_images_and_the_template(folder, data):
    from studio_agents import mcp_server
    out = mcp_server.measure_past_work(str(folder))
    assert "#E3F4EE" in out[0] and "DESIGN.md: YAML front matter" in out[0] and "Never use red" in out[0]
    assert len(out) == 4, "the text and three previews"
    assert mcp_server.measure_past_work(str(folder / "nope"))[0].startswith("error:")
