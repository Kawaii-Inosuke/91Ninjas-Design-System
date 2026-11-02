"""Client packs: create a client locally, export it as one `.91pack` file, import one received over Slack/Drive.

D-009: everything is local; sharing is a file; no library and no approval. An imported pack is untrusted
(SECURITY T10-T12): nothing in it is ever run, paths, sizes, counts and file types are checked, checksums
are verified, SVGs and CSS are sanitised, and it is extracted to a staging folder before the designer
confirms the install.

Pack layout (zip):
    manifest.json        {format, client, display_name, version, author, created_at, app_version, files: {path: sha256}}
    DESIGN.md            design language: YAML tokens + prose            (required)
    critic_rules.md      the client's rulebook                            (required)
    assets/brand.css · assets/fonts/<Family>-<weight>.woff2 · assets/icons/*.svg · assets/canvas.svg
    past/                a few past infographics                          (≥ 3)
    source/              the brand guideline (optional)
"""
from __future__ import annotations

import getpass
import hashlib
import json
import re
import shutil
import stat
import subprocess
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import defusedxml.ElementTree as SafeET

from studio_agents import brand as brandmod
from studio_agents import config

PACK_FORMAT = 1
APP_VERSION = "0.1.0"
CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
ALLOWED_EXT = {".md", ".css", ".woff2", ".woff", ".ttf", ".otf", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".json"}
TOP_LEVEL = {"manifest.json", "DESIGN.md", "critic_rules.md", "assets", "past", "source"}
FONT_EXT = {".woff2", ".woff", ".ttf", ".otf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf"}


class PackError(ValueError):
    pass


# ── sanitising (SECURITY T10) ───────────────────────────────────────────────────────────────────────

SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "{http://www.w3.org/1999/xlink}href"


def sanitize_svg(text: str) -> str:
    """Drop scripts, foreignObject, event handlers and links to anything but in-file ids or inline images."""
    try:
        root = SafeET.fromstring(text)
    except Exception as e:  # defusedxml raises on entities / DTDs too
        raise PackError(f"SVG is not safe or not well-formed: {e}") from None
    for parent in list(root.iter()):
        for child in list(parent):
            tag = child.tag.split("}")[-1].lower() if isinstance(child.tag, str) else ""
            if tag in {"script", "foreignobject", "iframe", "embed", "object"}:
                parent.remove(child)
    for el in root.iter():
        for attr in list(el.attrib):
            name = attr.split("}")[-1].lower()
            value = el.attrib[attr].strip()
            if name.startswith("on"):
                del el.attrib[attr]
            elif attr in ("href", XLINK) and not (value.startswith("#") or value.startswith("data:image/")):
                del el.attrib[attr]
            elif name == "style" and re.search(r"url\(\s*['\"]?(?!#|data:image/)", value, re.I):
                del el.attrib[attr]
    import xml.etree.ElementTree as ET  # serialising only; parsing went through defusedxml
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ET.tostring(root, encoding="unicode")


def sanitize_css(text: str) -> str:
    """No @import, no url() that leaves the pack (only data: and plain relative paths inside it)."""
    text = re.sub(r"@import[^;]*;?", "/* import removed */", text, flags=re.I)
    text = re.sub(r"expression\s*\(", "/* expression removed */(", text, flags=re.I)

    def url(m: re.Match) -> str:
        target = m.group(2).strip()
        if target.startswith("data:") or (not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I)
                                          and not target.startswith(("/", "\\")) and ".." not in target):
            return m.group(0)
        return "url(about:blank)"
    return re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", url, text, flags=re.I | re.S)


def _copy_sanitised(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if ext == ".svg":
        dst.write_text(sanitize_svg(src.read_text(errors="replace")))
    elif ext == ".css":
        dst.write_text(sanitize_css(src.read_text(errors="replace")))
    else:
        shutil.copyfile(src, dst)


# ── create ──────────────────────────────────────────────────────────────────────────────────────────

def check_client_id(client: str) -> str:
    if not CLIENT_ID.match(client):
        raise PackError("client id must be 2-40 characters: lowercase letters, digits and dashes (e.g. 'fcc', 'acme-bank')")
    return client


def create_client(client: str, *, design_md: str, critic_rules: str, past: list[Path],
                  fonts: list[Path] = (), icons: list[Path] = (), brand_css: str | None = None,
                  sources: list[Path] = (), root: Path | None = None, overwrite: bool = False) -> brandmod.Brand:
    """The New-client wizard's last step: write a validated pack into the clients folder."""
    check_client_id(client)
    tokens, _ = brandmod.parse_design_md(design_md)
    if problems := brandmod.validate_tokens(tokens):
        raise PackError("DESIGN.md: " + "; ".join(problems))
    if not critic_rules.strip():
        raise PackError("a rulebook (critic_rules.md) is required")
    if len(past) < config.PACK_PAST_MIN:
        raise PackError(f"add at least {config.PACK_PAST_MIN} past infographics")
    for group, allowed in ((past, IMAGE_EXT), (fonts, FONT_EXT), (icons, {".svg"}), (sources, ALLOWED_EXT)):
        for p in group:
            if p.suffix.lower() not in allowed:
                raise PackError(f"{p.name}: file type {p.suffix} is not allowed here")
    dest = (root or config.clients_dir()) / client
    if dest.exists() and not overwrite:
        raise PackError(f"client {client!r} already exists")
    stage = dest.with_name(f".{client}.creating")
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "assets").mkdir(parents=True)
    (stage / "DESIGN.md").write_text(design_md)
    (stage / "critic_rules.md").write_text(critic_rules)
    if brand_css:
        (stage / "assets" / "brand.css").write_text(sanitize_css(brand_css))
    for i, p in enumerate(past, 1):
        _copy_sanitised(p, stage / "past" / f"{i:02d}-{_safe_name(p.name)}")
    for p in fonts:
        _copy_sanitised(p, stage / "assets" / "fonts" / _safe_name(p.name))
    for p in icons:
        _copy_sanitised(p, stage / "assets" / "icons" / _safe_name(p.name))
    for p in sources:
        _copy_sanitised(p, stage / "source" / _safe_name(p.name))
    if dest.exists():
        shutil.rmtree(dest)
    stage.rename(dest)
    install_fonts(client, root)
    return brandmod.load(client, root)


def _safe_name(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem or ext).strip("-")[:80] or "file"
    return f"{stem}.{ext.lower()}" if dot else stem


# ── export ──────────────────────────────────────────────────────────────────────────────────────────

def export_pack(client: str, out_dir: Path, *, past: list[str] | None = None, author: str | None = None,
                root: Path | None = None) -> Path:
    """Write `<client>-v<version>.91pack` to out_dir. `past` picks which past infographics go in (file names);
    default: the first PACK_PAST_MAX."""
    b = brandmod.load(client, root)
    all_past = b.past_work()
    chosen = [p for p in all_past if past is None or p.name in past][: config.PACK_PAST_MAX]
    files: list[tuple[str, Path]] = []
    for p in sorted(b.dir.rglob("*")):
        rel = p.relative_to(b.dir).as_posix()
        if not p.is_file() or p.name.startswith(".") or rel.split("/")[0] == "past":
            continue
        if p.suffix.lower() in ALLOWED_EXT and rel != "manifest.json":
            files.append((rel, p))
    files += [(f"past/{p.name}", p) for p in chosen]
    manifest = {"format": PACK_FORMAT, "client": client, "display_name": b.name, "version": b.version,
                "author": author or getpass.getuser(), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "app_version": APP_VERSION, "files": {rel: _sha256(p.read_bytes()) for rel, p in files}}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{client}-v{b.version}.91pack"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for rel, p in files:
            z.write(p, rel)
    if out.stat().st_size > config.PACK_MAX_BYTES:
        out.unlink()
        raise PackError(f"pack is over {config.PACK_MAX_BYTES >> 20} MB: include fewer or smaller past infographics")
    return out


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── import: validate → preview → install ────────────────────────────────────────────────────────────

@dataclass
class ImportPreview:
    id: str
    client: str
    display_name: str
    version: str
    author: str
    created_at: str
    colors: dict[str, str]
    fonts: list[str]
    samples: list[Path]
    rules: str
    staging: Path
    exists: bool
    problems: list[str] = field(default_factory=list)


def _check_member(info: zipfile.ZipInfo) -> str:
    name = info.filename
    p = PurePosixPath(name)
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or ".." in p.parts or "\\" in name:
        raise PackError(f"unsafe path in pack: {name!r}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise PackError(f"links are not allowed in a pack: {name!r}")
    if p.parts[0] not in TOP_LEVEL:
        raise PackError(f"unexpected file in pack: {name!r}")
    if not info.is_dir() and p.suffix.lower() not in ALLOWED_EXT:
        raise PackError(f"file type not allowed in a pack: {name!r}")
    return name


def inspect_pack(pack: Path, root: Path | None = None) -> ImportPreview:
    """Validate a received pack and stage it for the designer to preview. Nothing is installed yet."""
    if pack.stat().st_size > config.PACK_MAX_BYTES:
        raise PackError(f"pack is over {config.PACK_MAX_BYTES >> 20} MB")
    try:
        z = zipfile.ZipFile(pack)
    except zipfile.BadZipFile:
        raise PackError("not a .91pack file (not a zip archive)") from None
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > config.PACK_MAX_FILES:
            raise PackError(f"pack has more than {config.PACK_MAX_FILES} files")
        if sum(i.file_size for i in infos) > config.PACK_MAX_BYTES:
            raise PackError(f"pack unpacks to more than {config.PACK_MAX_BYTES >> 20} MB")
        names = [_check_member(i) for i in infos]
        if "manifest.json" not in names or "DESIGN.md" not in names:
            raise PackError("pack is missing manifest.json or DESIGN.md")
        try:
            manifest = json.loads(z.read("manifest.json"))
        except ValueError:
            raise PackError("manifest.json is not valid JSON") from None
        if manifest.get("format") != PACK_FORMAT:
            raise PackError(f"pack format {manifest.get('format')!r} is not supported by this app")
        client = check_client_id(str(manifest.get("client", "")))
        listed = manifest.get("files") or {}
        problems = [f"{n}: not listed in the manifest" for n in names if n != "manifest.json" and n not in listed]
        problems += [f"{n}: listed in the manifest but missing" for n in listed if n not in names]
        staging = (root or config.clients_dir()).parent / "imports" / uuid.uuid4().hex[:12]
        staging.mkdir(parents=True)
        try:
            for n in names:
                if n == "manifest.json":
                    continue
                data = z.read(n)
                if n in listed and _sha256(data) != listed[n]:
                    problems.append(f"{n}: checksum does not match (damaged or changed)")
                dst = staging / n
                dst.parent.mkdir(parents=True, exist_ok=True)
                ext = dst.suffix.lower()
                if ext == ".svg":
                    dst.write_text(sanitize_svg(data.decode("utf-8", "replace")))
                elif ext == ".css":
                    dst.write_text(sanitize_css(data.decode("utf-8", "replace")))
                else:
                    dst.write_bytes(data)
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=1))
            tokens, _ = brandmod.parse_design_md((staging / "DESIGN.md").read_text())
            if bad := brandmod.validate_tokens(tokens):
                raise PackError("DESIGN.md: " + "; ".join(bad))
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    if problems:  # a pack that doesn't match its own manifest was damaged or tampered with
        shutil.rmtree(staging, ignore_errors=True)
        raise PackError("; ".join(problems[:5]))
    b = brandmod.Brand(client, staging, tokens, "", (staging / "critic_rules.md").read_text() if (staging / "critic_rules.md").is_file() else "")
    return ImportPreview(
        id=staging.name, client=client, display_name=str(manifest.get("display_name") or client),
        version=str(manifest.get("version", "")), author=str(manifest.get("author", "")),
        created_at=str(manifest.get("created_at", "")), colors=b.palette(), fonts=b.fonts(),
        samples=b.past_work(), rules=b.rules, staging=staging,
        exists=(root or config.clients_dir()).joinpath(client, "DESIGN.md").is_file(), problems=problems)


def install_pack(preview: ImportPreview, mode: str = "replace", root: Path | None = None) -> str:
    """Install a previewed pack. mode: "replace" an existing client, or "keep_both" (installs as <client>-v<version>)."""
    clients = root or config.clients_dir()
    client = preview.client
    if preview.exists and mode == "keep_both":
        client = check_client_id(re.sub(r"[^a-z0-9-]+", "-", f"{preview.client}-v{preview.version}".lower()).strip("-")[:40])
    elif preview.exists and mode != "replace":
        raise PackError("mode must be 'replace' or 'keep_both'")
    dest = clients / client
    clients.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(preview.staging), dest)
    install_fonts(client, root)
    return client


def discard_import(preview: ImportPreview) -> None:
    shutil.rmtree(preview.staging, ignore_errors=True)


# ── fonts ───────────────────────────────────────────────────────────────────────────────────────────

def install_fonts(client: str, root: Path | None = None) -> list[Path]:
    """Copy a client's font files where the system (and so the editor) finds them, then refresh the font cache."""
    src = (root or config.clients_dir()) / client / "assets" / "fonts"
    files = [p for p in src.glob("*") if p.suffix.lower() in FONT_EXT] if src.is_dir() else []
    if not files:
        return []
    dst = config.fonts_dir() / client
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for p in files:
        shutil.copyfile(p, dst / p.name)
        out.append(dst / p.name)
    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f", str(dst)], capture_output=True, timeout=60, check=False)
    return out
