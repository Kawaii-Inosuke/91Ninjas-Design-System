"""Shared team clients (D-037): three laptops (a manager and two designers) and one fake GitHub repo."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from studio_agents import brand as brandmod
from studio_agents import team as teammod
from studio_agents.team import GitHub, Team, TeamError

FIX = Path(__file__).parent / "fixtures"


class FakeRepo:
    """Commits as snapshots: {path: bytes}; blobs by content hash, as git does."""

    def __init__(self):
        self.commits: dict[str, dict[str, bytes]] = {}
        self.order: list[str] = []
        self.messages: list[str] = []

    def head(self):
        return self.order[-1] if self.order else None

    def files(self, commit):
        return {p: hashlib.sha1(b).hexdigest() for p, b in self.commits[commit].items()}

    def read(self, sha):
        for snap in self.commits.values():
            for b in snap.values():
                if hashlib.sha1(b).hexdigest() == sha:
                    return b
        raise KeyError(sha)

    def commit(self, changes, message, parent):
        assert parent == self.head(), "commits build on the current head"
        snap = dict(self.commits[parent]) if parent else {}
        for p, b in changes.items():
            if b is None:
                snap.pop(p, None)
            else:
                snap[p] = b
        sha = hashlib.sha1(f"{len(self.order)}{message}".encode()).hexdigest()
        self.commits[sha], self.order, self.messages = snap, [*self.order, sha], [*self.messages, message]
        return sha


@pytest.fixture
def laptops(tmp_path, monkeypatch):
    repo = FakeRepo()
    homes = {}
    for who in ("manager", "priya", "arjun"):
        homes[who] = tmp_path / who
        (homes[who] / "clients").mkdir(parents=True)

    def be(who):
        monkeypatch.setenv("STUDIO_DATA_DIR", str(homes[who]))
        return Team(repo_factory=lambda name, key: repo)

    return repo, homes, be


def add_client(home: Path, cid="acme", rules="# Acme rules\nR1. White page.\n"):
    d = home / "clients" / cid
    shutil.copytree(FIX / "brands" / "demo", d, dirs_exist_ok=True)
    (d / "critic_rules.md").write_text(rules)
    (d / "past").mkdir(exist_ok=True)
    for i in range(3):
        from PIL import Image
        Image.new("RGB", (30, 20), (i * 60, 90, 200)).save(d / "past" / f"p{i}.png")


def test_join_share_install_update_with_a_diff_and_the_managers_reject(laptops):
    repo, homes, be = laptops
    # The manager sets the team up; designers join; joining a repo that isn't set up is refused.
    with pytest.raises(TeamError, match="manager creates it first"):
        be("priya").join("91ninjas/clients", "key", "Priya")
    be("manager").join("91ninjas/clients", "key", "Shrikant", create=True)
    assert json.loads(repo.commits[repo.head()]["team.json"])["managers"] == ["Shrikant"]
    be("priya").join("91ninjas/clients", "key", "Priya")
    be("arjun").join("91ninjas/clients", "key", "Arjun")
    assert (homes["priya"] / "team" / "key").stat().st_mode & 0o077 == 0, "the key is readable by its owner only"

    # Priya adds Acme: it isn't shared yet, so the app offers to share it.
    add_client(homes["priya"])
    t = be("priya")
    assert t.check()["unshared"] == ["acme"]
    assert t.share("acme", "first version") == {"client": "acme", "version": 1}
    assert "clients/acme/past/p0.png" in repo.commits[repo.head()] and t.check()["unshared"] == []

    # Arjun is asked about the new client, and installs it.
    t = be("arjun")
    p = t.check()["pending"]
    assert [(x["client"], x["kind"], x["by"], x["version"]) for x in p] == [("acme", "new", "Priya", 1)]
    t.accept("acme")
    assert brandmod.load("acme").rules.startswith("# Acme rules") and len(brandmod.load("acme").past_work()) == 3
    assert t.check()["pending"] == []

    # Priya changes a rule and shares v2: Arjun sees exactly what changed, and installs it.
    rules = homes["priya"] / "clients" / "acme" / "critic_rules.md"
    rules.write_text("# Acme rules\nR1. White page.\nR2. Titles in Poppins 44px.\n")
    be("priya").share("acme", "title rule")
    t = be("arjun")
    [p] = t.check()["pending"]
    assert p["kind"] == "update" and p["message"] == "title rule"
    assert p["changes"]["critic_rules.md"] == [{"op": "+", "text": "R2. Titles in Poppins 44px."}]
    t.accept("acme")
    assert "R2." in brandmod.load("acme").rules

    # The manager doesn't like it: rejecting puts v1's rules back for everyone, without asking.
    m = be("manager")
    m.check()
    assert m.status()["manager"]
    with pytest.raises(TeamError, match="only a manager"):
        be("arjun").reject("acme")
    be("manager").reject("acme")
    st = be("arjun").check()
    assert st["pending"] == [] and st["applied"] == [{"client": "acme", "version": 3, "by": "Shrikant"}]
    assert "R2." not in brandmod.load("acme").rules, "reverted on Arjun's laptop automatically"
    assert "R2." not in be("priya").check() and "R2." not in brandmod.load("acme").rules

    # "Not now" hides a version until a newer one comes.
    rules.write_text("# Acme rules\nR1. White page.\nR3. Icons are line icons.\n")
    be("priya").share("acme", "icons")
    t = be("arjun")
    assert len(t.check()["pending"]) == 1
    t.decline("acme")
    assert t.check()["pending"] == []


def test_designs_are_never_shared_and_a_broken_client_is_not_sent(laptops):
    repo, homes, be = laptops
    be("manager").join("91ninjas/clients", "key", "Shrikant", create=True)
    add_client(homes["manager"])
    (homes["manager"] / "designs" / "d1").mkdir(parents=True)
    (homes["manager"] / "designs" / "d1" / "design.json").write_text("{}")
    be("manager").share("acme")
    assert not any("designs" in p for p in repo.commits[repo.head()])
    (homes["manager"] / "clients" / "acme" / "DESIGN.md").write_text("broken")
    with pytest.raises(Exception):
        be("manager").share("acme")


def test_not_joined_and_bad_repo_names(laptops, tmp_path, monkeypatch):
    _, _, be = laptops
    t = be("arjun")
    assert t.check()["joined"] is False
    with pytest.raises(TeamError, match="not in a team"):
        t.share("acme")
    with pytest.raises(TeamError, match="owner/name"):
        GitHub("not a repo", "k")
    with pytest.raises(TeamError, match="your name"):
        t.join("a/b", "k", " ")


def test_the_github_client_speaks_the_git_data_api(monkeypatch):
    """The requests a share sends (no network: urlopen is replaced)."""
    import base64
    import io
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen.append((req.get_method(), req.full_url.split("/repos/o/r")[1], json.loads(req.data) if req.data else None))
        path = req.full_url.split("/repos/o/r")[1]
        answers = {"": {"default_branch": "main"}, "/git/ref/heads/main": {"object": {"sha": "c1"}},
                   "/git/commits/c1": {"tree": {"sha": "t1"}}, "/git/blobs": {"sha": "b1"}, "/git/trees": {"sha": "t2"},
                   "/git/commits": {"sha": "c2"}, "/git/refs/heads/main": {}}
        assert req.headers["Authorization"] == "Bearer k"
        return Resp(json.dumps(answers[path]).encode())

    monkeypatch.setattr(teammod.urllib.request, "urlopen", fake)
    g = GitHub("o/r", "k")
    assert g.head() == "c1"
    assert g.commit({"clients/a/DESIGN.md": b"x", "clients/a/old.png": None}, "share", "c1") == "c2"
    tree = next(b for m, p, b in seen if p == "/git/trees")
    assert tree["base_tree"] == "t1" and {"path": "clients/a/old.png", "mode": "100644", "type": "blob", "sha": None} in tree["tree"]
    blob = next(b for m, p, b in seen if p == "/git/blobs")
    assert base64.b64decode(blob["content"]) == b"x"
    assert seen[-1][:2] == ("PATCH", "/git/refs/heads/main")
