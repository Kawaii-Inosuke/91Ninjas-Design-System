"""App updates without signing (D-043): check, download with the checksum, the installer scripts."""
import hashlib
import time

import pytest

from studio_agents import update
from studio_agents.update import UpdateError, Updater, parse_sums, platform_token

REPO = "Kawaii-Inosuke/91ninjas-studio-releases"
DL = f"https://github.com/{REPO}/releases/download/v1.0.2/"
MSI = b"new installer bytes" * 100


def release(version="1.0.2", sums_ok=True, asset_url=None, stem="91ninjas-studio"):
    name = f"{stem}-{version}-windows-x64.msi"
    digest = hashlib.sha256(MSI if sums_ok else b"other").hexdigest()
    files = {DL + name: MSI, DL + "SHA256SUMS.txt": f"{digest}  {name}\n".encode()}
    rel = {"tag_name": f"v{version}", "body": "Manager password.", "html_url": f"https://github.com/{REPO}/releases/tag/v{version}",
           "assets": [{"name": name, "browser_download_url": asset_url or DL + name, "size": len(MSI)},
                      {"name": "SHA256SUMS.txt", "browser_download_url": DL + "SHA256SUMS.txt", "size": 100}]}
    return rel, files


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))


def make(rel, files, current="1.0.1"):
    def download(url, dest, progress):
        dest.write_bytes(files[url])
        progress(len(files[url]), len(files[url]))
    return Updater(REPO, current, fetch=lambda url: rel, download=download)


def wait_done(u):
    for _ in range(200):
        if u.state != "downloading":
            return
        time.sleep(0.01)


def test_a_newer_release_is_found_downloaded_checked_and_its_script_written(windows, tmp_path):
    u = make(*release())
    st = u.check()
    assert st["available"] and st["latest"] == "1.0.2" and st["can_install"] and st["notes"] == "Manager password."
    u.start_download()
    wait_done(u)
    assert u.state == "ready" and u.file.read_bytes() == MSI
    cmd = u.prepare(r"C:\Users\priya\AppData\Local\Programs\91Ninjas Studio\91ninjas-studio.exe", 4242)
    assert cmd["program"] == "powershell.exe"
    script = (tmp_path / "updates" / "install-update.ps1").read_text()
    assert "Wait-Process -Id 4242" in script and "/passive" in script and "91ninjas-studio-1.0.2-windows-x64.msi" in script
    assert "Start-Process -FilePath 'C:\\Users\\priya" in script, "restarts the app"
    u.prepare(r"C:\x\91ninjas-studio.exe", 1, restart=False)
    assert "Start-Process -FilePath 'C:" not in (tmp_path / "updates" / "install-update.ps1").read_text()


def test_same_or_older_versions_are_not_offered(windows):
    assert not make(*release("1.0.1"), current="1.0.1").check()["available"]
    assert not make(*release("1.0.0"), current="1.0.1").check()["available"]
    assert make(*release("1.0.10"), current="1.0.9").check()["available"]
    with pytest.raises(UpdateError, match="no newer"):
        make(*release("1.0.1"), current="1.0.1").start_download()


def test_a_wrong_checksum_or_a_foreign_download_is_refused(windows):
    u = make(*release(sums_ok=False))
    u.check()
    u.start_download()
    wait_done(u)
    assert u.state == "error" and "checksum" in u.error and u.file is None
    with pytest.raises(UpdateError, match="isn't downloaded"):
        u.prepare("x.exe", 1)
    u = make(*release(asset_url="https://evil.example/91ninjas-studio-1.0.2-windows-x64.msi"))
    u.check()
    with pytest.raises(UpdateError, match="releases repo"):
        u.start_download()


def test_older_asset_names_are_found_and_mac_scripts_replace_the_app(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(update.sys, "platform", "win32")
    assert make(*release(stem="vectorcraft")).check()["can_install"], "1.0.0/1.0.1 names still found"
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setenv("STUDIO_APP_ARCH", "aarch64")
    assert platform_token() == "macos-arm64.dmg"
    rel, files = release()
    name = "91ninjas-studio-1.0.2-macos-arm64.dmg"
    rel["assets"][0]["name"], files[DL + name] = name, MSI
    rel["assets"][0]["browser_download_url"] = DL + name
    files[DL + "SHA256SUMS.txt"] = f"{hashlib.sha256(MSI).hexdigest()}  {name}\n".encode()
    u = make(rel, files)
    u.check()
    u.start_download()
    wait_done(u)
    cmd = u.prepare("/Applications/91Ninjas Studio.app/Contents/MacOS/91ninjas-studio", 77)
    assert cmd["program"] == "/bin/sh"
    script = (tmp_path / "updates" / "install-update.sh").read_text()
    assert "kill -0 77" in script and "hdiutil attach" in script and "'/Applications/91Ninjas Studio.app'" in script
    assert "/usr/bin/open '/Applications/91Ninjas Studio.app'" in script
    with pytest.raises(UpdateError, match="isn't an installed app"):
        u.prepare("/usr/local/bin/91ninjas-studio", 77)


def test_off_without_a_releases_repo_and_sums_parsing():
    assert Updater("", "1.0.0").check() == Updater("", "1.0.0").status()
    assert not Updater("", "1.0.0").status()["enabled"]
    h = "a" * 64
    assert parse_sums(f"{h}  a.msi\n{h} *b.dmg\nnot a line\n") == {"a.msi": h, "b.dmg": h}
