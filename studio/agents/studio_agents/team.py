"""Shared clients for the team (D-037): a private GitHub repo the app reads and writes itself, with one team key.

Designers have no GitHub accounts and never see git. The manager creates a private repo and a fine-grained access
token for that repo only (contents: read and write), and gives the key to the team once; each designer joins
with it and their name. Managers also type the **manager password** (set by whoever creates the team; only
its salted hash is in the repo); without it you join as a designer, whatever name you type. Only clients are shared (DESIGN.md, the rules, past work, fonts, icons, documents);
designs stay on each laptop.

    repo/team.json                      {"managers": ["Shrikant"], "manager_password": {"salt", "hash", "iterations"}}
    repo/clients/<id>/meta.json         {"version", "updated_by", "updated_at", "message", "previous_commit", "reverted"}
    repo/clients/<id>/DESIGN.md, critic_rules.md, past/…, assets/fonts/…, assets/icons/…, source/…

- share: one commit with the client's files and a new meta version.
- check (on start, every few minutes): new clients and new versions become *pending*, shown with what changed
  (lines added and removed in the design language and rules, files added and removed); the designer installs or
  says not now. A manager's rejection is a new version marked `reverted` (the files as they were before the
  change), and it installs everywhere without asking.

The GitHub REST API is used directly (git data endpoints: one commit per share), with the standard library only.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from studio_agents import brand as brandmod
from studio_agents import config, packs

API = "https://api.github.com"
REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SHARED_DIRS = ("past", "assets/fonts", "assets/icons", "source")
TEXT_FILES = ("DESIGN.md", "critic_rules.md")
MAX_FILE = 20 * 1024 * 1024
MAX_CLIENT = 100 * 1024 * 1024
CHECK_EVERY_S = 180
PASSWORD_ITERATIONS = 200_000
MIN_PASSWORD = 6
PARALLEL = 8              # GitHub calls at once when sharing or installing a client


class TeamError(RuntimeError):
    pass


# ── the repo ────────────────────────────────────────────────────────────────────────────────────────

class Repo(Protocol):
    def head(self) -> str | None: ...
    def files(self, commit: str) -> dict[str, str]: ...      # path → blob sha
    def read(self, blob: str) -> bytes: ...
    def commit(self, changes: dict[str, bytes | None], message: str, parent: str | None) -> str: ...


class GitHub:
    """A private GitHub repo through the REST API with the team key (no git)."""

    def __init__(self, repo: str, token: str, timeout: float = 30):
        if not REPO_NAME.match(repo):
            raise TeamError("the team repo is written owner/name, e.g. 91ninjas/studio-clients")
        self.repo, self.token, self.timeout = repo, token, timeout
        self._branch: str | None = None

    def _call(self, method: str, path: str, body: dict | None = None, ok404: bool = False) -> Any:
        req = urllib.request.Request(f"{API}/repos/{self.repo}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "91ninjas-studio"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if ok404 and e.code in (404, 409):
                return None
            detail = e.read()[:300].decode(errors="replace")
            if e.code in (401, 403):
                raise TeamError("the team key was refused (wrong, expired, or without access to this repo)") from None
            if e.code == 404:
                raise TeamError(f"no repo {self.repo} for this key") from None
            raise TeamError(f"GitHub said {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TeamError(f"can't reach GitHub: {e}") from None

    @property
    def branch(self) -> str:
        if self._branch is None:
            info = self._call("GET", "")
            self._branch = str(info.get("default_branch") or "main")
        return self._branch

    def head(self) -> str | None:
        ref = self._call("GET", f"/git/ref/heads/{self.branch}", ok404=True)
        return ref["object"]["sha"] if ref else None

    def files(self, commit: str) -> dict[str, str]:
        c = self._call("GET", f"/git/commits/{commit}")
        tree = self._call("GET", f"/git/trees/{c['tree']['sha']}?recursive=1")
        return {t["path"]: t["sha"] for t in tree.get("tree", []) if t.get("type") == "blob"}

    def read(self, blob: str) -> bytes:
        b = self._call("GET", f"/git/blobs/{blob}")
        return base64.b64decode(b["content"])

    def commit(self, changes: dict[str, bytes | None], message: str, parent: str | None) -> str:
        if parent is None:
            # An empty repo has no tree to build on: the first file goes in through the contents API.
            first = next(p for p, d in changes.items() if d is not None)
            r = self._call("PUT", f"/contents/{first}", {"message": message, "content": base64.b64encode(changes[first] or b"").decode()})
            parent = r["commit"]["sha"]
            changes = {p: d for p, d in changes.items() if p != first}
            if not changes:
                return parent
        base = self._call("GET", f"/git/commits/{parent}")["tree"]["sha"]

        def entry(item: tuple[str, bytes | None]) -> dict:
            path, data = item
            if data is None:
                return {"path": path, "mode": "100644", "type": "blob", "sha": None}
            blob = self._call("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            return {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}

        # Each GitHub call takes about a second: the files go up 8 at a time, not one after another.
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            entries = list(pool.map(entry, changes.items()))
        tree = self._call("POST", "/git/trees", {"base_tree": base, "tree": entries})
        c = self._call("POST", "/git/commits", {"message": message, "tree": tree["sha"], "parents": [parent]})
        try:
            self._call("PATCH", f"/git/refs/heads/{self.branch}", {"sha": c["sha"]})
        except TeamError as e:
            raise TeamError(f"someone shared at the same time; try again ({e})") from None
        return c["sha"]


# ── the team ────────────────────────────────────────────────────────────────────────────────────────

def team_dir() -> Path:
    return config.data_dir() / "team"


@dataclass
class Settings:
    repo: str = ""
    name: str = ""
    installed: dict[str, int] = field(default_factory=dict)     # client → shared version installed here
    declined: dict[str, int] = field(default_factory=dict)      # client → version the designer said "not now" to
    last_check: str = ""
    error: str = ""
    manager_proof: str = ""     # the team's manager-password hash, kept once the right password was typed here

    @staticmethod
    def load() -> "Settings":
        p = team_dir() / "team.json"
        try:
            return Settings(**json.loads(p.read_text()))
        except (OSError, ValueError, TypeError):
            return Settings()

    def save(self) -> None:
        team_dir().mkdir(parents=True, exist_ok=True)
        p = team_dir() / "team.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=1))
        tmp.replace(p)


def _key_file() -> Path:
    return team_dir() / "key"


def load_key() -> str:
    try:
        return _key_file().read_text().strip()
    except OSError:
        return ""


def _save_key(token: str) -> None:
    team_dir().mkdir(parents=True, exist_ok=True)
    f = _key_file()
    f.write_text(token)
    try:
        f.chmod(0o600)   # only this user can read the key
    except OSError:
        pass


def client_files(client: str) -> dict[str, bytes]:
    """The files of an installed client that the team shares (relative path → bytes)."""
    d = config.clients_dir() / packs.check_client_id(client)
    out: dict[str, bytes] = {}
    total = 0
    for name in TEXT_FILES:
        if (d / name).is_file():
            out[name] = (d / name).read_bytes()
    css = d / "assets" / "brand.css"
    if css.is_file():
        out["assets/brand.css"] = css.read_bytes()
    for sub in SHARED_DIRS:
        for p in sorted((d / sub).glob("*")) if (d / sub).is_dir() else []:
            if p.is_file() and p.suffix.lower() in packs.ALLOWED_EXT and p.stat().st_size <= MAX_FILE:
                total += p.stat().st_size
                if total > MAX_CLIENT:
                    raise TeamError(f"{client} is over {MAX_CLIENT // 2**20} MB: remove some past work before sharing")
                out[f"{sub}/{p.name}"] = p.read_bytes()
    return out


def text_diff(old: str, new: str) -> list[dict]:
    """What changed in a text file, as lines: {"op": "+"|"-", "text"} (unchanged lines left out)."""
    out = []
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line[:1] in "+-" and line[2:].strip():
            out.append({"op": line[0], "text": line[2:][:300]})
    return out[:400]


def hash_password(password: str, salt: bytes | None = None, iterations: int = PASSWORD_ITERATIONS) -> dict:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return {"salt": salt.hex(), "hash": h.hex(), "iterations": iterations}


def check_password(password: str, stored: dict) -> bool:
    try:
        h = hash_password(password, bytes.fromhex(stored["salt"]), int(stored["iterations"]))
        return hmac.compare_digest(h["hash"], str(stored["hash"]))
    except (KeyError, ValueError, TypeError):
        return False


class Team:
    def __init__(self, repo_factory=None):
        self.repo_factory = repo_factory or (lambda repo, key: GitHub(repo, key))
        self.lock = threading.Lock()
        self.pending: list[dict] = []
        self.unshared: list[str] = []
        self.managers: list[str] = []
        self.manager_hash = ""            # the hash in the repo's team.json (a changed password ends old manager logins)
        self.applied: list[dict] = []     # reverts installed without asking, to tell the designer

    # ── joining ─────────────────────────────────────────────────────────────────────────────────────
    def _repo(self) -> Repo:
        s, key = Settings.load(), load_key()
        if not s.repo or not key:
            raise TeamError("not in a team yet: join with the team repo and key")
        return self.repo_factory(s.repo, key)

    def join(self, repo: str, key: str, name: str, password: str = "") -> dict:
        """Join the team's repo (checked with the key). With the manager password you join as a manager; the first
        manager (an empty repo, or a team from before passwords) sets the password."""
        repo, key, name = repo.strip(), key.strip(), name.strip()
        if not name:
            raise TeamError("give your name: the team sees who shared what")
        r = self.repo_factory(repo, key)
        head = r.head()
        files = r.files(head) if head else {}
        team = json.loads(r.read(files["team.json"])) if "team.json" in files else None
        proof = ""
        if team is None and not password:
            raise TeamError("that repo isn't set up for a team yet: the manager joins first, with a manager password")
        if password:
            created = team is None
            team = team or {"managers": []}
            stored = team.get("manager_password")
            changed = created
            if stored:
                if not check_password(password, stored):
                    raise TeamError("wrong manager password (leave it empty to join as a designer)")
            else:
                if len(password) < MIN_PASSWORD:
                    raise TeamError(f"the manager password needs at least {MIN_PASSWORD} characters")
                stored = team["manager_password"] = hash_password(password)
                changed = True
            proof = str(stored["hash"])
            managers = [str(m) for m in team.get("managers", [])]
            if name.lower() not in {m.lower() for m in managers}:
                team["managers"] = [*managers, name]
                changed = True
            if changed:
                r.commit({"team.json": json.dumps(team, indent=1).encode()},
                         f"{'Team created' if created else 'Manager joined'}: {name}", head)
        s = Settings.load()
        s.repo, s.name, s.error, s.manager_proof = repo, name, "", proof
        s.save()
        _save_key(key)
        return self.status(refresh=True)

    def leave(self) -> None:
        shutil.rmtree(team_dir(), ignore_errors=True)
        self.pending, self.unshared, self.managers = [], [], []

    def _load_managers(self, repo: Repo) -> None:
        head = repo.head()
        files = repo.files(head) if head else {}
        self._team_from(json.loads(repo.read(files["team.json"])) if "team.json" in files else {})

    def _team_from(self, team: dict) -> None:
        self.managers = [str(m) for m in team.get("managers", [])]
        self.manager_hash = str((team.get("manager_password") or {}).get("hash", ""))

    def is_manager(self) -> bool:
        """Manager only with the manager password typed on this laptop (the name alone counts for nothing)."""
        proof = Settings.load().manager_proof
        return bool(proof and self.manager_hash and hmac.compare_digest(proof, self.manager_hash))

    # ── sharing ─────────────────────────────────────────────────────────────────────────────────────
    def share(self, client: str, message: str = "") -> dict:
        """Send this computer's version of a client to the team as a new version."""
        repo, s = self._repo(), Settings.load()
        brandmod.load(client)   # only a working client is shared
        with self.lock:
            head = repo.head()
            files = repo.files(head) if head else {}
            meta = self._meta(repo, files, client) or {}
            version = int(meta.get("version", 0)) + 1
            mine = client_files(client)
            prefix = f"clients/{client}/"
            changes: dict[str, bytes | None] = {prefix + k: v for k, v in mine.items()}
            for path in files:
                if path.startswith(prefix) and path != prefix + "meta.json" and path[len(prefix):] not in mine:
                    changes[path] = None
            changes[prefix + "meta.json"] = json.dumps({
                "version": version, "updated_by": s.name, "updated_at": _now(), "message": message.strip()[:300],
                "previous_commit": head, "reverted": False}, indent=1).encode()
            repo.commit(changes, f"{client} v{version} shared by {s.name}" + (f": {message.strip()[:80]}" if message.strip() else ""), head)
            s.installed[client] = version
            s.declined.pop(client, None)
            s.save()
        return {"client": client, "version": version}

    # ── checking ────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _meta(repo: Repo, files: dict[str, str], client: str) -> dict | None:
        sha = files.get(f"clients/{client}/meta.json")
        return json.loads(repo.read(sha)) if sha else None

    def check(self) -> dict:
        """Look at the team repo: what's new or changed (pending), what's only here (unshared); a manager's
        rejection installs at once."""
        s = Settings.load()
        try:
            repo = self._repo()
        except TeamError:
            return self.status()
        with self.lock:
            try:
                head = repo.head()
                files = repo.files(head) if head else {}
                team = json.loads(repo.read(files["team.json"])) if "team.json" in files else {}
                self._team_from(team)
                shared = sorted({p.split("/")[1] for p in files if p.startswith("clients/") and p.endswith("/meta.json")})
                pending = []
                for client in shared:
                    meta = self._meta(repo, files, client) or {}
                    v = int(meta.get("version", 0))
                    if s.installed.get(client, 0) >= v:
                        continue
                    if meta.get("reverted") and client in s.installed:
                        self._install(repo, files, client, meta)
                        s.installed[client] = v
                        self.applied.append({"client": client, "version": v, "by": meta.get("updated_by", "")})
                        continue
                    if s.declined.get(client, 0) >= v:
                        continue
                    pending.append(self._describe(repo, files, client, meta))
                self.pending = pending
                local = brandmod.list_clients()
                self.unshared = [c for c in local if c not in shared]
                s.last_check, s.error = _now(), ""
            except TeamError as e:
                s.error = str(e)
            s.save()
        return self.status()

    def _describe(self, repo: Repo, files: dict[str, str], client: str, meta: dict) -> dict:
        """A pending version, with what it changes against this computer's copy."""
        prefix = f"clients/{client}/"
        theirs = {p[len(prefix):]: sha for p, sha in files.items() if p.startswith(prefix) and p != prefix + "meta.json"}
        installed = client in brandmod.list_clients()
        mine = client_files(client) if installed else {}
        diffs = {}
        for name in TEXT_FILES:
            new = repo.read(theirs[name]).decode(errors="replace") if name in theirs else ""
            old = mine.get(name, b"").decode(errors="replace")
            if new != old:
                diffs[name] = text_diff(old, new)
        name = client
        if "DESIGN.md" in theirs:
            try:
                name = str(brandmod.parse_design_md(repo.read(theirs["DESIGN.md"]).decode(errors="replace"))[0].get("name") or client)
            except brandmod.BrandError:
                pass
        return {"client": client, "name": name, "kind": "update" if installed else "new", "version": int(meta.get("version", 0)),
                "by": meta.get("updated_by", ""), "at": meta.get("updated_at", ""), "message": meta.get("message", ""),
                "reverted": bool(meta.get("reverted")), "changes": diffs,
                "files_added": sorted(set(theirs) - set(mine) - set(TEXT_FILES)) if installed else [],
                "files_removed": sorted(set(mine) - set(theirs) - set(TEXT_FILES)) if installed else []}

    # ── deciding ────────────────────────────────────────────────────────────────────────────────────
    def accept(self, client: str) -> dict:
        repo, s = self._repo(), Settings.load()
        with self.lock:
            head = repo.head()
            files = repo.files(head) if head else {}
            meta = self._meta(repo, files, client)
            if not meta:
                raise TeamError(f"{client} isn't shared in the team")
            self._install(repo, files, client, meta)
            s.installed[client] = int(meta.get("version", 0))
            s.declined.pop(client, None)
            s.save()
            self.pending = [p for p in self.pending if p["client"] != client]
        return {"client": client, "version": s.installed[client]}

    def decline(self, client: str) -> None:
        s = Settings.load()
        p = next((p for p in self.pending if p["client"] == client), None)
        if p:
            s.declined[client] = p["version"]
            s.save()
        self.pending = [p for p in self.pending if p["client"] != client]

    def reject(self, client: str) -> dict:
        """Manager only: undo the last shared change of a client for everyone (its files as they were before it)."""
        repo, s = self._repo(), Settings.load()
        self._load_managers(repo)
        if not self.is_manager():
            raise TeamError("only a manager can reject a change for everyone")
        with self.lock:
            head = repo.head()
            files = repo.files(head) if head else {}
            meta = self._meta(repo, files, client)
            if not meta or not meta.get("previous_commit"):
                raise TeamError(f"there is no earlier version of {client} to go back to")
            before = repo.files(meta["previous_commit"])
            prefix = f"clients/{client}/"
            old_meta = self._meta(repo, before, client)
            changes: dict[str, bytes | None] = {}
            for p in files:
                if p.startswith(prefix) and p not in before:
                    changes[p] = None
            for p, sha in before.items():
                if p.startswith(prefix) and p != prefix + "meta.json" and files.get(p) != sha:
                    changes[p] = repo.read(sha)
            version = int(meta.get("version", 0)) + 1
            if old_meta is None:   # the change being rejected added the client: remove it from the team
                changes.update({p: None for p in files if p.startswith(prefix)})
                commit_message = f"{client}: added by {meta.get('updated_by', '')}, rejected by {s.name}"
            else:
                changes[prefix + "meta.json"] = json.dumps({
                    "version": version, "updated_by": s.name, "updated_at": _now(),
                    "message": f"rejected v{meta.get('version')} by {meta.get('updated_by', '')}", "previous_commit": head,
                    "reverted": True}, indent=1).encode()
                commit_message = f"{client} v{version}: {s.name} rejected v{meta.get('version')}"
            repo.commit(changes, commit_message, head)
        return self.check() | {"rejected": client}

    def _install(self, repo: Repo, files: dict[str, str], client: str, meta: dict) -> None:
        """The shared version replaces this computer's copy (checked and sanitised like an imported pack)."""
        prefix = f"clients/{client}/"
        stage = Path(tempfile.mkdtemp(prefix="91studio-team-"))
        try:
            got: dict[str, list[Path]] = {sub: [] for sub in SHARED_DIRS}
            text: dict[str, str] = {}
            css = None
            wanted = [(p, sha) for p, sha in files.items() if p.startswith(prefix) and p != prefix + "meta.json"]
            with ThreadPoolExecutor(max_workers=PARALLEL) as pool:   # downloads 8 at a time
                blobs = dict(zip([p for p, _ in wanted], pool.map(lambda w: repo.read(w[1]), wanted)))
            for path, _sha in wanted:
                rel = path[len(prefix):]
                data = blobs[path]
                if rel in TEXT_FILES:
                    text[rel] = data.decode(errors="replace")
                elif rel == "assets/brand.css":
                    css = data.decode(errors="replace")
                else:
                    sub, _, fname = rel.rpartition("/")
                    if sub in got and fname and "/" not in fname:
                        p = stage / sub.replace("/", "_") / packs._safe_name(fname)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(data)
                        got[sub].append(p)
            packs.create_client(client, design_md=text.get("DESIGN.md", ""), critic_rules=text.get("critic_rules.md", ""),
                                past=got["past"], fonts=got["assets/fonts"], icons=got["assets/icons"], brand_css=css,
                                sources=got["source"], overwrite=True)
        except (packs.PackError, brandmod.BrandError) as e:
            raise TeamError(f"the shared {client} can't be installed: {e}") from None
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    # ── status ──────────────────────────────────────────────────────────────────────────────────────
    def status(self, refresh: bool = False) -> dict:
        if refresh:
            return self.check()
        s = Settings.load()
        applied, self.applied = self.applied, []
        return {"joined": bool(s.repo and load_key()), "repo": s.repo, "name": s.name, "manager": self.is_manager(),
                "managers": self.managers, "pending": self.pending, "unshared": self.unshared, "applied": applied,
                "last_check": s.last_check, "error": s.error}

    def watch(self) -> None:
        """Check now and every few minutes, in the background (the local service starts this)."""
        def loop() -> None:
            import time
            while True:
                try:
                    self.check()
                except Exception:  # a check that fails tries again next time
                    pass
                time.sleep(CHECK_EVERY_S)
        threading.Thread(target=loop, name="team-watch", daemon=True).start()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def team_enabled() -> bool:
    return os.environ.get("STUDIO_TEAM", "1") != "0"
