"""The sidecar API: token and origin checks, client packs over HTTP, a whole run driven through the API."""
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio_agents import models, packs
from studio_agents.history import History
from studio_agents.runs import RunManager
from studio_agents.server import create_app
from test_runs import Script

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
    models.set_backend(Script())
    app = create_app(TOKEN, RunManager(History(data / "history.db"), bridge_factory=lambda: None))
    yield TestClient(app)
    models.set_backend(None)


def test_token_and_origin_are_required(client):
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/health", headers={**AUTH, "Origin": "https://evil.example"}).status_code == 403
    r = client.get("/api/health", headers=AUTH)
    assert r.status_code == 200 and r.json()["editor"] is False and "critic" in r.json()["models"]
    assert client.get("/").status_code == 200                          # the page itself holds no data


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


def events(client, rid, until):
    """Read the event stream until an event of type `until` (gate/done) arrives."""
    out = []
    with client.stream("GET", f"/api/runs/{rid}/events", headers=AUTH) as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                e = json.loads(line[6:])
                out.append(e)
                if e["type"] in until:
                    return out
    return out


def test_the_panel_starts_runs_with_json(client):
    r = client.post("/api/runs/json", headers=AUTH, json={"client": "demo", "content": "How to check a payment request..."})
    assert r.status_code == 200 and r.json()["id"]
    assert client.post("/api/runs/json", headers=AUTH, json={"client": "nobody", "content": "x"}).status_code == 422


def test_a_run_through_the_api(client):
    rid = client.post("/api/runs", headers=AUTH, data={"client": "demo", "content": "How to check a payment request..."}).json()["id"]
    ev = events(client, rid, {"gate"})
    assert ev[-1]["data"]["kind"] == "outline" and any(e["type"] == "brief" for e in ev)
    assert client.post(f"/api/runs/{rid}/decide", headers=AUTH, json={"gate": "final", "decision": "approve"}).status_code == 409
    assert client.post(f"/api/runs/{rid}/decide", headers=AUTH, json={"gate": "outline", "decision": "approve"}).json()["ok"]
    for _ in range(100):
        run = client.get(f"/api/runs/{rid}", headers=AUTH).json()
        if run["gate"]:
            break
        time.sleep(0.1)
    assert run["gate"]["kind"] == "final"
    preview = run["versions"][-1]["preview"].split(f"/runs/{rid}/")[1]
    img = client.get(f"/api/runs/{rid}/files/{preview}", params={"token": TOKEN})
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/api/runs/{rid}/files/../../history.db", headers=AUTH).status_code == 404
    client.post(f"/api/runs/{rid}/decide", headers=AUTH, json={"gate": "final", "decision": "approve"})
    done = [e for e in events(client, rid, {"done"}) if e["type"] == "done"][0]
    assert done["data"]["status"] == "done" and set(done["data"]["files"]) >= {"png", "svg"}
    assert client.post(f"/api/runs/{rid}/messages", headers=AUTH, json={"text": "bigger"}).status_code == 409   # not in the editor


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
