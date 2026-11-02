"""Brand Onboarder (5d): measuring past work, the draft as a pack, the job flow and saving (scripted model)."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio_agents import brand as brandmod
from studio_agents import models, onboard
from studio_agents.render.build import render
from studio_agents.render.spec import Spec
from studio_agents.server import create_app

FIX = Path(__file__).parent / "fixtures"
SPEC = json.loads((FIX / "specs.json").read_text())["vertical_list"]


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
    yield d
    models.set_backend(None)


def a_draft(**over) -> onboard.BrandDraft:
    style = lambda size, weight: {"fontFamily": "Poppins", "fontSize": size, "fontWeight": weight, "lineHeight": 1.3}
    d = {
        "name": "Acme Health", "description": "Green and calm.",
        "palette": {"primary": "#0a7a5a", "accent": "#F5A623", "ink": "#222", "surface": "#FFFFFF", "tint": "#E3F4EE",
                    "cell": "#F2F2F2", "line": "#999999", "on_primary": "#FFFFFF", "header_bar": "#0A7A5A1A",
                    "others": [{"name": "Warm Sand", "hex": "#F4E9D8"}]},
        "typography": {"title": style(44, 600), "heading": style(30, 600), "body": style(26, 400), "label": style(26, 600),
                       "marker": style(40, 700)},
        "radius_sm": 12, "radius_md": 18, "radius_lg": 28, "space_page": 72, "space_section": 60, "space_item": 24,
        "canvas_width": 1080, "layouts": ["vertical_list/icon", "card_list/check", "pyramid"],
        "icon_note": "Line icons in accent.", "guide": "Calm, airy cards on white.",
        "rules": [{"section": "Locked", "text": f"Rule {i}"} for i in range(6)],
    }
    d.update(over)
    return onboard.BrandDraft.model_validate(d)


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


def test_colours_are_checked_after_the_answer():
    d = a_draft()
    assert d.palette.primary == "#0A7A5A" and d.palette.ink == "#222222" and d.palette.header_bar == "#0A7A5A1A"
    with pytest.raises(ValueError):
        a_draft(palette={**d.palette.model_dump(), "accent": "orange"})


def test_a_draft_becomes_a_pack_that_loads_and_renders(data, tmp_path):
    md = onboard.to_design_md(a_draft(), "acme-health")
    tokens, guide = brandmod.parse_design_md(md)
    assert brandmod.validate_tokens(tokens) == []
    assert tokens["colors"]["on-primary"] == "#FFFFFF" and tokens["colors"]["warm-sand"] == "#F4E9D8"
    assert tokens["typography"]["marker"]["fontSize"] == "40px" and tokens["infographic"]["canvas"]["width"] == 1080
    assert "Calm, airy cards" in guide and "## Icons" in guide
    rules = onboard.to_rules_md(a_draft())
    assert "## Locked" in rules and "R6. Rule 5" in rules
    client = data / "clients" / "acme-health"
    (client / "past").mkdir(parents=True)
    (client / "DESIGN.md").write_text(md)
    (client / "critic_rules.md").write_text(rules)
    b = brandmod.load("acme-health")
    r = render(Spec(**SPEC), b, tmp_path / "out")
    assert r.png.exists() and "--color-tint: #E3F4EE" in b.css_vars()


def test_the_job_drafts_the_designer_edits_and_saves(folder, data):
    seen = {}

    def backend(role, system, prompt, output, images, tools):
        seen.update(role=role, prompt=prompt, images=images)
        return a_draft()

    models.set_backend(backend)
    m = onboard.OnboardManager()
    job = m.start("Acme Health", str(folder), notes="They like orange accents", run_async=False)
    assert job.status == "ready", job.error
    assert seen["role"] == "onboarder" and len(seen["images"]) == 3
    assert "#E3F4EE" in seen["prompt"] and "<designer_notes>" in seen["prompt"] and "Never use red" in seen["prompt"]
    assert job.summary["fonts"] == ["Poppins"] and job.summary["rules"] == 6 and job.summary["problems"] == []
    # The designer edits the draft by hand before saving: the edit is what gets saved.
    design = job.dir / "DESIGN.md"
    design.write_text(design.read_text().replace("name: Acme Health", "name: Acme Health Group"))
    assert m.refresh(job.id).summary["name"] == "Acme Health Group"
    cid = m.save(job.id)
    assert cid == "acme-health" and job.status == "saved"
    b = brandmod.load(cid)
    assert b.name == "Acme Health Group" and len(b.past_work()) == 3
    assert (b.dir / "source" / "brand.md").exists()
    with pytest.raises(onboard.OnboardError, match="not ready"):
        m.save(job.id)


def test_a_broken_edit_is_reported_and_not_saved(folder, data):
    models.set_backend(lambda *a: a_draft())
    m = onboard.OnboardManager()
    job = m.start("Acme", str(folder), run_async=False)
    (job.dir / "DESIGN.md").write_text("no front matter")
    assert m.refresh(job.id).summary["problems"]
    with pytest.raises(onboard.OnboardError):
        m.save(job.id)
    m.discard(job.id)
    assert not job.dir.exists()


def test_a_model_failure_ends_the_job_with_a_reason(folder, data):
    def boom(*a):
        raise models.ModelError("every key and fallback failed")

    models.set_backend(boom)
    job = onboard.OnboardManager().start("Acme", str(folder), run_async=False)
    assert job.status == "failed" and "every key" in job.error and job.facts is not None


def test_the_server_runs_the_onboarding(folder, data):
    models.set_backend(lambda *a: a_draft())
    m = onboard.OnboardManager()
    client = TestClient(create_app("t", onboarder=m))
    h = {"Authorization": "Bearer t"}
    assert client.post("/api/onboard", json={"name": "Acme", "folder": "/nope"}, headers=h).status_code == 422
    r = client.post("/api/onboard", json={"name": "Acme Health", "folder": str(folder)}, headers=h)
    assert r.status_code == 200
    jid = r.json()["id"]
    for t in m.jobs.values():   # the thread may still be working
        import time
        for _ in range(100):
            if t.status in ("ready", "failed"):
                break
            time.sleep(0.1)
    got = client.get(f"/api/onboard/{jid}", headers=h).json()
    assert got["status"] == "ready" and got["summary"]["colors"]["primary"] == "#0A7A5A"
    assert got["design_md"].endswith("DESIGN.md")
    saved = client.post(f"/api/onboard/{jid}/save", json={}, headers=h).json()
    assert saved["client"] == "acme-health"
    assert "acme-health" in [c["client"] for c in client.get("/api/clients", headers=h).json()]
