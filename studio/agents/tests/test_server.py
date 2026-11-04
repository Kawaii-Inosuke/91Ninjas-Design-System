"""The panel's API (D-033): token and origin checks, client packs over HTTP, history, Claude's activity, connecting Claude."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio_agents.designs import Studio
from studio_agents.history import History
from studio_agents.server import create_app

FIX = Path(__file__).parent / "fixtures"
TOKEN = "t" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(Path.home() / ".cache" / "91studio" / "models"))
    shutil.copytree(FIX / "brands" / "demo", data / "clients" / "demo")
    app = create_app(TOKEN, Studio(History(data / "history.db"), bridge_factory=lambda: None))
    yield TestClient(app)


def test_token_and_origin_are_required(client):
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/health", headers={**AUTH, "Origin": "https://evil.example"}).status_code == 403
    r = client.get("/api/health", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"ok": True, "editor": False, "ai": "claude", "setup": ""}


def test_clients_export_import_install(client, tmp_path):
    lst = client.get("/api/clients", headers=AUTH).json()
    assert lst[0]["client"] == "demo" and lst[0]["colors"]["primary"] == "#0164F0"
    # the demo fixture has no past work, so add some through a fresh create
    from PIL import Image
    files = []
    for i in range(3):
        p = tmp_path / f"p{i}.png"
        Image.new("RGB", (30, 20), (i * 60, 90, 200)).save(p)
        files.append(("past", (p.name, p.read_bytes(), "image/png")))
    form = {"client": "acme", "design_md": (FIX / "brands/demo/DESIGN.md").read_text(), "critic_rules": "# rules"}
    r = client.post("/api/clients", headers=AUTH, data=form, files=files)
    assert r.status_code == 200, r.text
    pack = client.post("/api/clients/acme/export", headers=AUTH)
    assert pack.status_code == 200 and pack.headers["content-type"] == "application/zip"
    pv = client.post("/api/clients/import", headers=AUTH, files={"pack": ("acme.91pack", pack.content, "application/zip")}).json()
    assert pv["client"] == "acme" and pv["exists"] and len(pv["samples"]) == 3
    r = client.post(f"/api/clients/import/{pv['id']}/install", headers=AUTH, json={"mode": "keep_both"})
    assert r.json()["client"] == "acme-v1-0-0"
    bad = client.post("/api/clients/import", headers=AUTH, files={"pack": ("x.91pack", b"not a zip", "application/zip")})
    assert bad.status_code == 422 and "zip" in bad.json()["detail"]
    assert client.get("/api/clients/acme/samples/..%2F..%2Fhistory.db", headers=AUTH).status_code == 404


def test_clients_from_a_folder_and_a_pack_path(client, tmp_path):
    from PIL import Image
    folder = tmp_path / "Acme Bank"
    (folder / "past").mkdir(parents=True)
    (folder / "DESIGN.md").write_text((FIX / "brands/demo/DESIGN.md").read_text())
    (folder / "critic_rules.md").write_text("# rules")
    for i in range(3):
        Image.new("RGB", (30, 20), (i * 60, 90, 200)).save(folder / "past" / f"p{i}.png")
    r = client.post("/api/clients/from-folder", headers=AUTH, json={"path": str(folder)})
    assert r.status_code == 200 and r.json()["client"] == "acme-bank", r.text
    assert client.post("/api/clients/from-folder", headers=AUTH, json={"path": str(folder)}).status_code == 422  # exists
    assert client.post("/api/clients/from-folder", headers=AUTH, json={"path": str(tmp_path)}).status_code == 422  # no DESIGN.md
    pack = client.post("/api/clients/acme-bank/export", headers=AUTH)
    p = tmp_path / "acme.91pack"
    p.write_bytes(pack.content)
    pv = client.post("/api/clients/import-path", headers=AUTH, json={"path": str(p)}).json()
    assert pv["client"] == "acme-bank" and pv["exists"]
    assert client.post(f"/api/clients/import/{pv['id']}/install", headers=AUTH, json={"mode": "replace"}).json()["client"] == "acme-bank"


def test_history_activity_designs_and_connecting_claude(client, tmp_path):
    from studio_agents import mcp_server
    assert client.get("/api/history", headers=AUTH, params={"client": "demo"}).json() == []
    assert client.get("/api/history", headers=AUTH, params={"client": "../x"}).status_code == 422
    # What Claude did through the connector reaches the panel, newest last, and `after` skips what was shown.
    mcp_server.log("start_design", "x: loyalty")
    mcp_server.log("render_design", "x v1", ok=False)
    rows = client.get("/api/activity", headers=AUTH).json()
    assert [r["tool"] for r in rows] == ["start_design", "render_design"] and rows[1]["ok"] is False
    assert client.get("/api/activity", headers=AUTH, params={"after": rows[1]["at"]}).json() == []
    c = client.get("/api/connect", headers=AUTH).json()
    assert c["claude_code"].startswith("claude mcp add --scope user 91ninjas-studio -- ")
    assert c["claude_code"].endswith("run python -m studio_agents.mcp_server")
    assert c["claude_desktop"]["mcpServers"]["91ninjas-studio"]["args"][-1] == "studio_agents.mcp_server"
    assert client.get("/api/designs", headers=AUTH).json() == []
    assert client.get("/api/designs/nope/files/x.png", headers=AUTH).status_code == 404


def test_the_panel_knows_which_claude_apps_are_connected(tmp_path):
    from studio_agents.server import connected_apps
    assert connected_apps(tmp_path) == []
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"91ninjas-studio": {}}}))
    assert connected_apps(tmp_path) == ["Claude Code"]
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"/x": {"mcpServers": {"91ninjas-studio": {}}}}}))
    (tmp_path / ".config" / "Claude").mkdir(parents=True)
    (tmp_path / ".config" / "Claude" / "claude_desktop_config.json").write_text(json.dumps({"mcpServers": {"91ninjas-studio": {}}}))
    assert connected_apps(tmp_path) == ["Claude Code", "Claude Desktop"]
    (tmp_path / ".claude.json").write_text("not json")
    assert connected_apps(tmp_path) == ["Claude Desktop"]


def test_the_team_through_the_api(tmp_path, monkeypatch):
    from studio_agents.designs import Studio
    from studio_agents.team import Team
    from test_team import FakeRepo
    data = tmp_path / "data"
    monkeypatch.setenv("STUDIO_DATA_DIR", str(data))
    shutil.copytree(FIX / "brands" / "demo", data / "clients" / "demo")
    repo = FakeRepo()
    c = TestClient(create_app(TOKEN, Studio(History(data / "history.db"), bridge_factory=lambda: None), Team(lambda n, k: repo)))
    assert c.get("/api/team", headers=AUTH).json()["joined"] is False
    assert c.post("/api/team/join", headers=AUTH, json={"repo": "o/r", "key": "k", "name": "Ana"}).status_code == 422
    st = c.post("/api/team/join", headers=AUTH, json={"repo": "o/r", "key": "k", "name": "Ana", "password": "ninja-boss"}).json()
    assert st["joined"] and st["manager"] and st["unshared"] == ["demo"]
    assert c.post("/api/team/share", headers=AUTH, json={"client": "nobody"}).status_code == 422
    assert c.post("/api/team/reject", headers=AUTH, json={"client": "demo"}).status_code == 422
    assert c.delete("/api/team", headers=AUTH).json()["joined"] is False


def test_one_click_claude_desktop_keeps_the_rest_of_its_config(tmp_path):
    from studio_agents.server import ConnectError, connect_claude_desktop, connected_apps, desktop_config_path
    p = desktop_config_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"mcpServers": {"figma": {"command": "x"}}, "theme": "dark"}))
    connect_claude_desktop(tmp_path)
    cfg = json.loads(p.read_text())
    assert set(cfg["mcpServers"]) == {"figma", "91ninjas-studio"} and cfg["theme"] == "dark"
    assert cfg["mcpServers"]["91ninjas-studio"]["args"][-1] == "studio_agents.mcp_server"
    assert p.with_suffix(".json.before-91ninjas").is_file(), "the old file is kept"
    assert "Claude Desktop" in connected_apps(tmp_path)
    p.write_text("{broken")
    with pytest.raises(ConnectError, match="isn't valid JSON"):
        connect_claude_desktop(tmp_path)


def test_an_installed_app_starts_the_connector_with_its_own_uv_and_environment(monkeypatch):
    from studio_agents.server import connect_info
    monkeypatch.setenv("STUDIO_UV", "/Applications/91Ninjas Studio.app/Contents/MacOS/uv")
    monkeypatch.setenv("STUDIO_BUNDLED", "1")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/Users/a/.local/share/91studio/agents-env")
    c = connect_info()
    d = c["claude_desktop"]["mcpServers"]["91ninjas-studio"]
    assert d["command"].endswith("MacOS/uv") and "--frozen" in d["args"] and "--no-dev" in d["args"]
    assert d["env"] == {"UV_PROJECT_ENVIRONMENT": "/Users/a/.local/share/91studio/agents-env"}
    assert ' -e "UV_PROJECT_ENVIRONMENT=/Users/a/.local/share/91studio/agents-env" 91ninjas-studio -- "/Applications/91Ninjas Studio.app/Contents/MacOS/uv"' in c["claude_code"]


def test_the_parent_check_only_looks():
    """It must never end the process it checks (os.kill(pid, 0) does on Windows: the app closed after 2 s)."""
    import os
    import subprocess
    import sys
    from studio_agents.server import process_alive
    assert process_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_alive(p.pid) and process_alive(p.pid)
        assert p.poll() is None, "still running after being checked"
    finally:
        p.kill()
        p.wait()
    assert not process_alive(p.pid)
    import inspect, studio_agents.server as srv
    win = inspect.getsource(srv.process_alive).split('if sys.platform == "win32":')[1].split("try:\n        os.kill")[0]
    assert "os.kill" not in win, "the Windows branch must not call os.kill"
