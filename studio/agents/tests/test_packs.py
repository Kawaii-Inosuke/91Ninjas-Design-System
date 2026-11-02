"""Client packs (D-009): create, export, import with every safety check (SECURITY T10), install, fonts."""
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from studio_agents import brand, packs
from studio_agents.packs import PackError

DEMO = Path(__file__).parent / "fixtures" / "brands" / "demo"
SYSTEM_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))


def pngs(tmp_path, n=3):
    out = []
    for i in range(n):
        p = tmp_path / f"old work {i}.png"
        Image.new("RGB", (60, 40), (i * 40, 100, 200)).save(p)
        out.append(p)
    return out


def make_demo(tmp_path, **kw):
    return packs.create_client("demo", design_md=(DEMO / "DESIGN.md").read_text(),
                               critic_rules=(DEMO / "critic_rules.md").read_text(), past=pngs(tmp_path), **kw)


def test_create_client_writes_a_loadable_pack(tmp_path):
    icon = tmp_path / "logo.svg"
    icon.write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script><circle r="4"/></svg>')
    b = make_demo(tmp_path, icons=[icon], brand_css="@import url(https://evil.example/x.css); .card{color:red}")
    assert b.name == "Demo Co" and b.version == "1.0.0" and b.canvas_width == 1200
    assert b.palette()["primary"] == "#0164F0" and b.fonts() == ["DejaVu Sans"]
    assert len(b.past_work()) == 3 and brand.list_clients() == ["demo"]
    svg = (b.dir / "assets/icons/logo.svg").read_text()
    assert "script" not in svg and "onload" not in svg and "circle" in svg
    assert "@import" not in b.brand_css() and ".card{color:red}" in b.brand_css()
    assert "Base critic rules" in b.critic_rules() and b.critic_rules().startswith("# Demo Co critic rules")


def test_create_client_rejects_bad_input(tmp_path):
    with pytest.raises(PackError, match="client id"):
        packs.create_client("Demo Co!", design_md="", critic_rules="x", past=[])
    with pytest.raises(PackError, match="at least 3"):
        packs.create_client("demo", design_md=(DEMO / "DESIGN.md").read_text(), critic_rules="x", past=pngs(tmp_path, 2))
    with pytest.raises(brand.BrandError, match="front matter"):
        packs.create_client("demo", design_md="no front matter", critic_rules="x", past=pngs(tmp_path))
    bad = (DEMO / "DESIGN.md").read_text().replace('ink: "#383838"', 'ink: "dark grey"')
    with pytest.raises(PackError, match="colors.ink"):
        packs.create_client("demo", design_md=bad, critic_rules="x", past=pngs(tmp_path))


def test_export_import_install_round_trip(tmp_path):
    make_demo(tmp_path)
    pack = packs.export_pack("demo", tmp_path / "out", author="tester")
    assert pack.name == "demo-v1.0.0.91pack"
    manifest = json.loads(zipfile.ZipFile(pack).read("manifest.json"))
    assert manifest["author"] == "tester" and "DESIGN.md" in manifest["files"] and len([f for f in manifest["files"] if f.startswith("past/")]) == 3
    preview = packs.inspect_pack(pack)
    assert preview.client == "demo" and preview.exists and preview.display_name == "Demo Co"
    assert preview.colors["primary"] == "#0164F0" and len(preview.samples) == 3 and "B5." in preview.rules
    assert packs.install_pack(preview, "keep_both") == "demo-v1-0-0"
    assert brand.list_clients() == ["demo", "demo-v1-0-0"]
    preview = packs.inspect_pack(pack)
    assert packs.install_pack(preview, "replace") == "demo"
    assert not any((tmp_path / "data" / "imports").iterdir())     # staging cleaned up


def zip_with(path, members, manifest_files=None, fmt=1):
    """A pack built by hand; members = {name: bytes}. The manifest lists checksums unless overridden."""
    files = manifest_files if manifest_files is not None else {n: packs._sha256(b) for n, b in members.items()}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": fmt, "client": "evil", "files": files}))
        for n, b in members.items():
            z.writestr(n, b)
    return path


def good_members():
    return {"DESIGN.md": (DEMO / "DESIGN.md").read_bytes(), "critic_rules.md": b"# rules"}


@pytest.mark.parametrize("name", ["../escape.md", "/etc/passwd.md", "past/../../x.png", "C:/x.md", "scripts/run.md"])
def test_unsafe_paths_are_refused(tmp_path, name):
    with pytest.raises(PackError):
        packs.inspect_pack(zip_with(tmp_path / "p.91pack", {**good_members(), name: b"x"}))


def test_disallowed_types_links_and_tampering_are_refused(tmp_path):
    with pytest.raises(PackError, match="not allowed"):
        packs.inspect_pack(zip_with(tmp_path / "a.91pack", {**good_members(), "assets/run.py": b"print(1)"}))
    link = tmp_path / "b.91pack"
    with zipfile.ZipFile(link, "w") as z:
        info = zipfile.ZipInfo("past/x.png")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr("manifest.json", "{}")
        z.writestr(info, "/etc/passwd")
    with pytest.raises(PackError, match="links"):
        packs.inspect_pack(link)
    members = good_members()
    tampered = {n: packs._sha256(b) for n, b in members.items()}
    tampered["DESIGN.md"] = "0" * 64
    with pytest.raises(PackError, match="checksum"):
        packs.inspect_pack(zip_with(tmp_path / "c.91pack", members, tampered))
    unlisted = {**good_members(), "past/extra.png": b"x"}
    with pytest.raises(PackError, match="not listed"):
        packs.inspect_pack(zip_with(tmp_path / "d.91pack", unlisted, {n: packs._sha256(b) for n, b in good_members().items()}))
    with pytest.raises(PackError, match="format"):
        packs.inspect_pack(zip_with(tmp_path / "e.91pack", good_members(), fmt=99))
    (tmp_path / "f.91pack").write_bytes(b"not a zip")
    with pytest.raises(PackError, match="zip"):
        packs.inspect_pack(tmp_path / "f.91pack")
    assert not (tmp_path / "data" / "imports").exists() or not any((tmp_path / "data" / "imports").iterdir())


def test_imported_svg_and_css_are_sanitised(tmp_path):
    evil_svg = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
                b'<script>steal()</script><foreignObject><p>x</p></foreignObject>'
                b'<a xlink:href="https://evil.example"><rect onclick="steal()" width="5" height="5" style="fill:url(https://evil.example/x)"/></a>'
                b'<use href="#ok"/></svg>')
    members = {**good_members(), "assets/icons/x.svg": evil_svg,
               "assets/brand.css": b"@import 'https://evil.example/a.css'; .x{background:url(https://evil.example/t.png)} .y{background:url(data:image/png;base64,AA==)} @font-face{src:url(fonts/A-400.woff2)}"}
    preview = packs.inspect_pack(zip_with(tmp_path / "p.91pack", members))
    svg = (preview.staging / "assets/icons/x.svg").read_text()
    for bad in ("script", "steal", "foreignObject", "evil.example"):
        assert bad not in svg
    assert 'href="#ok"' in svg
    css = (preview.staging / "assets/brand.css").read_text()
    assert "evil.example" not in css and "@import" not in css
    assert "data:image/png" in css and "fonts/A-400.woff2" in css
    packs.discard_import(preview)


def test_entity_bombs_are_refused(tmp_path):
    bomb = b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><svg xmlns="http://www.w3.org/2000/svg">&b;</svg>'
    with pytest.raises(PackError, match="not safe"):
        packs.inspect_pack(zip_with(tmp_path / "p.91pack", {**good_members(), "assets/icons/b.svg": bomb}))


@pytest.mark.skipif(not SYSTEM_FONT.exists(), reason="DejaVu Sans not installed")
def test_fonts_are_installed_for_the_editor(tmp_path):
    font = tmp_path / "DejaVuSans-400.ttf"
    font.write_bytes(SYSTEM_FONT.read_bytes())
    b = make_demo(tmp_path, fonts=[font])
    installed = tmp_path / "xdg" / "fonts" / "91studio" / "demo" / "DejaVuSans-400.ttf"
    assert installed.is_file()
    assert "@font-face { font-family: 'DejaVuSans'; font-weight: 400;" in b.font_faces()
