"""App updates without signing certificates (D-043).

The app's own updater (craft-update) installs an update only when the running app and the new installer carry
the same paid signature (Authenticode on Windows, an Apple team id on Mac), and ours are unsigned (D-035). So the
local service does it instead:

1. check: the latest release of the team's public releases repo (`STUDIO_RELEASES_REPO`), compared with the
   running app's version (`STUDIO_APP_VERSION`);
2. download: this computer's installer, only from that repo's release downloads, then checked against the
   release's `SHA256SUMS.txt` (a missing or different checksum is refused);
3. prepare: a small script that waits until the app has closed, installs (Windows: the per-user MSI, which
   needs no admin rights; Mac: the .app from the disk image into the place the old one was) and, when asked,
   opens the app again. The app runs the script itself and then closes, so the script outlives it.

Standard library only. Designers' clients, designs and settings live outside the install folder and are kept.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path, PureWindowsPath
from typing import Any

from studio_agents import config

API = "https://api.github.com"
STEMS = ("91ninjas-studio", "vectorcraft")      # asset names, current first (1.0.0 and 1.0.1 used vectorcraft)
CHECK_CACHE_S = 30 * 60
TIMEOUT = 30
MAC_APP = "91Ninjas Studio.app"


def _version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


def platform_token(system: str | None = None, arch: str | None = None) -> str | None:
    """The end of this computer's asset name: `windows-x64.msi`, `macos-arm64.dmg`, `macos-x86_64.dmg`."""
    system = system or sys.platform
    arch = arch or os.environ.get("STUDIO_APP_ARCH") or platform.machine()
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64", "AMD64": "x86_64"}.get(arch, arch)
    if system.startswith("win"):
        return "windows-x64.msi"
    if system == "darwin":
        return f"macos-{arch}.dmg"
    return None


def parse_sums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$", line.strip())
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


class Updater:
    def __init__(self, repo: str | None = None, current: str | None = None, fetch=None, download=None):
        self.repo = (repo if repo is not None else os.environ.get("STUDIO_RELEASES_REPO", "")).strip()
        self.current = (current if current is not None else os.environ.get("STUDIO_APP_VERSION", "")).strip()
        self._fetch = fetch or self._http_json
        self._download = download or self._http_download
        self.lock = threading.Lock()
        self.release: dict | None = None
        self.checked_at = 0.0
        self.state = "idle"          # idle | downloading | ready | error
        self.progress = 0.0
        self.received = 0
        self.size = 0
        self.error = ""
        self.file: Path | None = None

    @property
    def enabled(self) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo) and self.current)

    def _prefix(self) -> str:
        return f"https://github.com/{self.repo}/releases/download/"

    # ── network (replaced in tests) ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _request(url: str) -> urllib.request.Request:
        return urllib.request.Request(url, headers={"User-Agent": "91ninjas-studio-updater", "Accept": "application/vnd.github+json"})

    def _http_json(self, url: str) -> Any:
        with urllib.request.urlopen(self._request(url), timeout=TIMEOUT) as r:
            return json.loads(r.read())

    def _http_download(self, url: str, dest: Path, on_progress) -> None:
        with urllib.request.urlopen(self._request(url), timeout=TIMEOUT) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                on_progress(done, total)

    # ── 1. check ────────────────────────────────────────────────────────────────────────────────────
    def check(self, force: bool = False, now: float | None = None) -> dict:
        import time
        now = time.time() if now is None else now
        if not self.enabled:
            return self.status()
        if force or self.release is None or now - self.checked_at > CHECK_CACHE_S:
            try:
                rel = self._fetch(f"{API}/repos/{self.repo}/releases/latest")
                self.release = self._describe(rel)
                self.checked_at = now
                if self.state == "error" and not self.file:
                    self.state, self.error = "idle", ""
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                self.checked_at = now
                if self.release is None:
                    return self.status() | {"check_error": f"couldn't reach GitHub ({e})"}
        return self.status()

    def _describe(self, rel: dict) -> dict:
        tag = str(rel.get("tag_name", ""))
        version = tag.lstrip("v")
        token = platform_token()
        asset = sums = None
        for a in rel.get("assets", []):
            name = str(a.get("name", ""))
            if name == "SHA256SUMS.txt":
                sums = a
            elif token and any(name == f"{stem}-{version}-{token}" for stem in STEMS):
                asset = a
        return {"version": version, "tag": tag, "notes": str(rel.get("body") or "")[:4000],
                "page": str(rel.get("html_url") or f"https://github.com/{self.repo}/releases/latest"),
                "asset": asset and {"name": asset["name"], "url": asset["browser_download_url"], "size": int(asset.get("size", 0))},
                "sums": sums and sums["browser_download_url"]}

    def available(self) -> bool:
        return bool(self.release and _version(self.release["version"]) > _version(self.current))

    # ── 2. download ─────────────────────────────────────────────────────────────────────────────────
    def start_download(self) -> dict:
        with self.lock:
            if self.state in ("downloading", "ready"):
                return self.status()
            if not self.available():
                raise UpdateError("there's no newer version to download")
            rel = self.release
            if not rel["asset"]:
                raise UpdateError("this release has no installer for this computer: download it from the page")
            if not rel["sums"]:
                raise UpdateError("this release has no checksums (SHA256SUMS.txt), so it isn't installed automatically")
            for url in (rel["asset"]["url"], rel["sums"]):
                if not url.startswith(self._prefix()) or ".." in url[len(self._prefix()):]:
                    raise UpdateError("the download isn't from the team's releases repo")
            self.state, self.progress, self.received, self.error = "downloading", 0.0, 0, ""
            self.size = rel["asset"]["size"]
        threading.Thread(target=self._download_now, args=(rel,), daemon=True, name="studio-update").start()
        return self.status()

    def _download_now(self, rel: dict) -> None:
        folder = config.data_dir() / "updates"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.iterdir():          # earlier downloads
                if old.is_file():
                    old.unlink(missing_ok=True)
            name = rel["asset"]["name"]
            sums_path = folder / "SHA256SUMS.txt"
            self._download(rel["sums"], sums_path, lambda *_: None)
            want = parse_sums(sums_path.read_text(errors="replace")).get(name)
            if not want:
                raise UpdateError(f"{name} isn't in the release's checksums")
            part = folder / (name + ".part")

            def progress(done: int, total: int) -> None:
                self.received = done
                total = total or self.size
                self.progress = min(done / total, 1.0) if total else 0.0

            self._download(rel["asset"]["url"], part, progress)
            h = hashlib.sha256()
            with open(part, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != want:
                part.unlink(missing_ok=True)
                raise UpdateError("the download doesn't match its checksum (damaged or replaced): not installed")
            final = folder / name
            part.replace(final)
            with self.lock:
                self.file, self.state, self.progress = final, "ready", 1.0
        except UpdateError as e:
            self._fail(str(e))
        except (urllib.error.URLError, OSError, ValueError) as e:
            self._fail(f"the download didn't finish ({e})")

    def _fail(self, message: str) -> None:
        with self.lock:
            self.state, self.error, self.file = "error", message, None

    # ── 3. the installer script ─────────────────────────────────────────────────────────────────────
    def prepare(self, exe: str, pid: int, restart: bool = True, system: str | None = None) -> dict:
        """The command the app runs before closing: it waits for `pid` to end, installs, and (`restart`) opens
        the app again. Returns {"program", "args"}."""
        system = system or sys.platform
        with self.lock:
            if self.state != "ready" or not self.file or not self.file.is_file():
                raise UpdateError("the update isn't downloaded yet")
            file = self.file
        folder = file.parent
        log = folder / "install.log"
        exe_path = Path(exe)
        if system.startswith("win"):
            script = folder / "install-update.ps1"
            q = lambda p: "'" + str(p).replace("'", "''") + "'"
            script.write_text("\r\n".join([
                "$ErrorActionPreference = 'Continue'",
                f"Start-Transcript -Path {q(log)} -Force | Out-Null",
                f"try {{ Wait-Process -Id {int(pid)} -Timeout 86400 -ErrorAction SilentlyContinue }} catch {{}}",
                "Start-Sleep -Seconds 2",
                f"$p = Start-Process -FilePath msiexec.exe -ArgumentList @('/i', '\"{file}\"', '/passive', '/norestart', 'INSTALLFOLDER=\"{PureWindowsPath(exe).parent}\"') -Wait -PassThru",
                "Write-Output \"msiexec exit code $($p.ExitCode)\"",
                *([f"Start-Process -FilePath {q(exe_path)}"] if restart else []),
                "Stop-Transcript | Out-Null",
            ]), encoding="utf-8")
            return {"program": "powershell.exe",
                    "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script)]}
        if system == "darwin":
            bundle = next((p for p in [exe_path, *exe_path.parents] if p.suffix == ".app"), None)
            if bundle is None:
                raise UpdateError("this copy isn't an installed app (no .app around it): download the update instead")
            script = folder / "install-update.sh"
            b, d, m = shlex.quote(str(bundle)), shlex.quote(str(file)), shlex.quote(str(folder / "mount"))
            script.write_text("\n".join([
                "#!/bin/sh",
                f"exec >{shlex.quote(str(log))} 2>&1",
                f"while kill -0 {int(pid)} 2>/dev/null; do sleep 1; done",
                "sleep 1",
                f"rm -rf {m}; mkdir -p {m}",
                f"/usr/bin/hdiutil attach -nobrowse -readonly -noautoopen -mountpoint {m} {d} || exit 1",
                f"rm -rf {b}.new",
                f"if /usr/bin/ditto {m}/{shlex.quote(MAC_APP)} {b}.new; then",
                f"  rm -rf {b}.old; mv {b} {b}.old && mv {b}.new {b} && rm -rf {b}.old",
                f"  /usr/bin/xattr -dr com.apple.quarantine {b} 2>/dev/null",
                "else",
                f"  echo 'copy failed: the old app is kept'; rm -rf {b}.new",
                "fi",
                f"/usr/bin/hdiutil detach {m} -quiet",
                *([f"/usr/bin/open {b}"] if restart else []),
            ]) + "\n")
            script.chmod(0o755)
            return {"program": "/bin/sh", "args": [str(script)]}
        raise UpdateError("automatic updates are for the Windows and Mac apps: download it from the page")

    # ── status ──────────────────────────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        rel = self.release or {}
        return {"enabled": self.enabled, "current": self.current, "available": self.available(),
                "latest": rel.get("version", ""), "notes": rel.get("notes", ""), "page": rel.get("page", ""),
                "can_install": bool(rel.get("asset") and rel.get("sums") and platform_token()),
                "state": self.state, "progress": round(self.progress, 3), "received": self.received, "size": self.size,
                "error": self.error}


class UpdateError(RuntimeError):
    pass
