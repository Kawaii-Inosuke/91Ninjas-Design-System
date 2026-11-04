"""The editor's local back end for the AI panel (D-033): clients, client packs, history, what Claude is doing
(the connector's activity log) and how to connect Claude. It makes no AI calls: Claude does that work through
the connector (`studio_agents.mcp_server`), on the designer's own Claude plan.

Security (SECURITY T2): binds 127.0.0.1; every /api call needs the per-launch token (Authorization: Bearer, or
?token= for <img> sources); requests from other origins are refused; files are served only from inside a design
or client folder.
"""
from __future__ import annotations

import json
import os
import sys
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from studio_agents import brand as brandmod
from studio_agents import config, packs
from studio_agents.designs import DesignError, Studio
from studio_agents.team import Team, TeamError
from studio_agents.update import Updater, UpdateError

MAX_UPLOAD = 25 * 1024 * 1024


# Request bodies (module level: FastAPI resolves annotations by name, which fails for classes local to create_app).
class InstallBody(BaseModel):
    mode: str = "replace"


class JoinBody(BaseModel):
    repo: str
    key: str
    name: str
    password: str = ""


class ShareBody(BaseModel):
    client: str
    message: str = ""


class PrepareBody(BaseModel):
    exe: str
    pid: int
    restart: bool = True


class ClientBody(BaseModel):
    client: str


class PathBody(BaseModel):
    path: str
    client: str | None = None


def create_app(token: str, studio: Studio | None = None, team: Team | None = None, updater: Updater | None = None) -> FastAPI:
    """The editor's AI panel back end (D-033): clients, history, what Claude is doing, how to connect Claude, and
    the files to preview. No model calls: Claude does the AI work through the connector (`mcp_server`)."""
    app = FastAPI(title="91Ninjas Studio", docs_url=None, redoc_url=None, openapi_url=None)
    studio = studio or Studio()
    team = team or Team()
    updater = updater or Updater()
    previews: dict[str, packs.ImportPreview] = {}

    def auth(request: Request) -> None:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if origin and origin not in (f"http://{host}",):
            raise HTTPException(403, "requests from other sites are not accepted")
        given = request.headers.get("authorization", "").removeprefix("Bearer ") or request.query_params.get("token", "")
        if not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "missing or wrong token")

    api = Depends(auth)

    def inside(base: Path, rel: str) -> Path:
        p = (base / rel).resolve()
        if not p.is_file() or base.resolve() not in p.parents:
            raise HTTPException(404, "no such file")
        return p

    async def saved(upload: UploadFile, folder: Path) -> Path:
        data = await upload.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large")
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / packs._safe_name(upload.filename or "upload")
        p.write_bytes(data)
        return p

    @app.get("/api/health", dependencies=[api])
    def health() -> dict:
        return {"ok": True, "editor": studio.bridge_factory() is not None, "ai": "claude", "setup": SETUP.get("state", "")}

    @app.get("/api/connect", dependencies=[api])
    def connect() -> dict:
        """How to connect Claude Code or Claude Desktop to this machine's connector."""
        return connect_info()

    @app.post("/api/connect/{app_name}", dependencies=[api])
    def connect_app(app_name: str) -> dict:
        """One click in the panel: set the connector up in Claude Code or Claude Desktop (D-039)."""
        try:
            if app_name == "claude-code":
                connect_claude_code()
            elif app_name == "claude-desktop":
                connect_claude_desktop()
            else:
                raise HTTPException(404, "unknown app")
        except ConnectError as e:
            raise HTTPException(422, str(e)) from None
        return connect_info()

    @app.get("/api/activity", dependencies=[api])
    def activity(after: str = "", limit: int = 30) -> list[dict]:
        """What Claude did through the connector, newest last (`after`: an ISO time already shown)."""
        f = mcp_activity_file()
        rows = []
        for line in (f.read_text().splitlines() if f.is_file() else [])[-200:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("at", "") > after:
                rows.append(r)
        return rows[-min(max(limit, 1), 200):]

    # ── clients ─────────────────────────────────────────────────────────────────────────────────────
    @app.get("/api/clients", dependencies=[api])
    def clients() -> list[dict]:
        out = []
        for c in brandmod.list_clients():
            try:
                b = brandmod.load(c)
                out.append({"client": c, "name": b.name, "version": b.version, "colors": b.palette(), "fonts": b.fonts(),
                            "samples": [p.name for p in b.past_work()], "problems": b.problems})
            except brandmod.BrandError as e:
                out.append({"client": c, "error": str(e)})
        return out

    @app.get("/api/clients/{client}/samples/{name}", dependencies=[api])
    def sample(client: str, name: str) -> FileResponse:
        return FileResponse(inside(config.clients_dir() / packs.check_client_id(client) / "past", name))

    @app.post("/api/clients", dependencies=[api])
    async def create(client: str = Form(), design_md: str = Form(), critic_rules: str = Form(),
                     past: list[UploadFile] = File(), fonts: list[UploadFile] = File(default=[])) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="91studio-new-"))
        try:
            past_files = [await saved(u, tmp / "past") for u in past]
            font_files = [await saved(u, tmp / "fonts") for u in fonts]
            b = packs.create_client(client, design_md=design_md, critic_rules=critic_rules, past=past_files, fonts=font_files)
        except (packs.PackError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return {"client": b.client, "problems": b.problems}

    @app.post("/api/clients/{client}/export", dependencies=[api])
    def export(client: str) -> FileResponse:
        try:
            p = packs.export_pack(packs.check_client_id(client), config.data_dir() / "exports")
        except (packs.PackError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        return FileResponse(p, filename=p.name, media_type="application/zip")

    @app.post("/api/clients/import", dependencies=[api])
    async def import_pack(pack: UploadFile = File()) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="91studio-pack-"))
        try:
            p = await saved(pack, tmp)
            pv = packs.inspect_pack(p)
        except (packs.PackError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        previews[pv.id] = pv
        return {"id": pv.id, "client": pv.client, "name": pv.display_name, "version": pv.version, "author": pv.author,
                "created_at": pv.created_at, "colors": pv.colors, "fonts": pv.fonts, "exists": pv.exists,
                "samples": [s.name for s in pv.samples], "rules": pv.rules[:4000]}

    def preview_json(pv: packs.ImportPreview) -> dict:
        return {"id": pv.id, "client": pv.client, "name": pv.display_name, "version": pv.version, "author": pv.author,
                "created_at": pv.created_at, "colors": pv.colors, "fonts": pv.fonts, "exists": pv.exists,
                "samples": [s.name for s in pv.samples], "rules": pv.rules[:4000]}

    @app.post("/api/clients/import-path", dependencies=[api])
    def import_path(body: PathBody) -> dict:
        """The editor's panel: a pack the designer picked in the file dialog (validated as any import)."""
        try:
            pv = packs.inspect_pack(Path(body.path).expanduser())
        except (OSError, packs.PackError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        previews[pv.id] = pv
        return preview_json(pv)

    @app.post("/api/clients/from-folder", dependencies=[api])
    def from_folder(body: PathBody) -> dict:
        """The editor's panel: a client from a brand folder (DESIGN.md, critic_rules.md, past/, assets/fonts…)."""
        try:
            b = packs.create_from_folder(Path(body.path), body.client or None, overwrite=False)
        except (OSError, packs.PackError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        return {"client": b.client, "name": b.name, "problems": b.problems}

    @app.post("/api/clients/import/{pid}/install", dependencies=[api])
    def install(pid: str, body: InstallBody) -> dict:
        pv = previews.pop(pid, None)
        if not pv:
            raise HTTPException(404, "no such import (it may have been installed or discarded)")
        try:
            return {"client": packs.install_pack(pv, body.mode)}
        except packs.PackError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/history", dependencies=[api])
    def history(client: str, limit: int = 50) -> list[dict]:
        """The panel's History tab: a client's designs and where their files are."""
        try:
            client = packs.check_client_id(client)
        except packs.PackError as e:
            raise HTTPException(422, str(e)) from None
        return studio.history.list(client, min(max(limit, 1), 200))

    # ── the team's shared clients (D-037) ───────────────────────────────────────────────────────────
    def team_call(fn, *args):
        try:
            return fn(*args)
        except (TeamError, brandmod.BrandError, packs.PackError) as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/team", dependencies=[api])
    def team_status() -> dict:
        return team.status()

    @app.post("/api/team/join", dependencies=[api])
    def team_join(body: JoinBody) -> dict:
        return team_call(team.join, body.repo, body.key, body.name, body.password)

    @app.post("/api/team/check", dependencies=[api])
    def team_check() -> dict:
        return team.check()

    @app.post("/api/team/share", dependencies=[api])
    def team_share(body: ShareBody) -> dict:
        r = team_call(team.share, body.client, body.message)
        return team.check() | {"shared": r}

    @app.post("/api/team/accept", dependencies=[api])
    def team_accept(body: ClientBody) -> dict:
        team_call(team.accept, body.client)
        return team.check()

    @app.post("/api/team/decline", dependencies=[api])
    def team_decline(body: ClientBody) -> dict:
        team.decline(body.client)
        return team.status()

    @app.post("/api/team/reject", dependencies=[api])
    def team_reject(body: ClientBody) -> dict:
        return team_call(team.reject, body.client)

    # ── app updates without signing (D-043) ─────────────────────────────────────────────────────────
    def update_call(fn, *args):
        try:
            return fn(*args)
        except UpdateError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/update", dependencies=[api])
    def update_status(force: bool = False) -> dict:
        return updater.check(force=force)

    @app.post("/api/update/download", dependencies=[api])
    def update_download() -> dict:
        return update_call(updater.start_download)

    @app.post("/api/update/prepare", dependencies=[api])
    def update_prepare(body: PrepareBody) -> dict:
        return update_call(updater.prepare, body.exe, body.pid, body.restart)

    @app.delete("/api/team", dependencies=[api])
    def team_leave() -> dict:
        team.leave()
        return team.status()

    @app.get("/api/designs", dependencies=[api])
    def designs(client: str | None = None) -> list[dict]:
        """Designs made with Claude, newest first (the panel's History shows the approved files)."""
        return [d.public() for d in studio.list(client)]

    @app.get("/api/designs/{did}/files/{rel:path}", dependencies=[api])
    def design_file(did: str, rel: str) -> FileResponse:
        try:
            d = studio.get(did)
        except DesignError as e:
            raise HTTPException(404, str(e)) from None
        return FileResponse(inside(d.dir, rel))

    return app


def mcp_activity_file() -> Path:
    return config.data_dir() / "activity.jsonl"


AGENTS_DIR = Path(__file__).resolve().parents[1]


def connect_info() -> dict:
    """The commands that connect Claude to this machine's connector (paths filled in)."""
    uv, args = _uv_args()
    env = _connector_env()
    quoted = " ".join(f'"{a}"' if " " in a else a for a in [uv, *args])
    flags = "".join(f' -e "{k}={v}"' for k, v in env.items())
    desktop: dict = {"command": uv, "args": args} | ({"env": env} if env else {})
    return {
        "name": "91ninjas-studio",
        "claude_code": f"claude mcp add --scope user{flags} 91ninjas-studio -- {quoted}",
        "claude_desktop": {"mcpServers": {"91ninjas-studio": desktop}},
        "claude_code_installed": shutil.which("claude") is not None,
        "connected": connected_apps(),
    }


SERVER_NAME = "91ninjas-studio"


class ConnectError(RuntimeError):
    pass


def _uv_args() -> tuple[str, list[str]]:
    """How the connector is started: the same uv, folder and environment the app runs its service with
    (an installed app ships its own uv and keeps the Python environment in the user's data folder, D-040)."""
    uv = os.environ.get("STUDIO_UV") or shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    args = ["--directory", str(AGENTS_DIR), "run"]
    if os.environ.get("STUDIO_BUNDLED") == "1":
        args += ["--frozen", "--no-dev"]
    return uv, [*args, "python", "-m", "studio_agents.mcp_server"]


def _connector_env() -> dict[str, str]:
    return {"UV_PROJECT_ENVIRONMENT": os.environ["UV_PROJECT_ENVIRONMENT"]} if os.environ.get("UV_PROJECT_ENVIRONMENT") else {}


def connect_claude_code() -> None:
    """`claude mcp add --scope user …` (replacing an older entry of ours)."""
    import subprocess
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not Path(claude).exists():
        raise ConnectError("Claude Code isn't installed on this computer")
    uv, args = _uv_args()
    subprocess.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True, timeout=30, check=False)
    env_flags = [x for k, v in _connector_env().items() for x in ("-e", f"{k}={v}")]
    r = subprocess.run([claude, "mcp", "add", "--scope", "user", *env_flags, SERVER_NAME, "--", uv, *args], capture_output=True, text=True, timeout=30, check=False)
    if r.returncode != 0:
        raise ConnectError(f"Claude Code said: {(r.stderr or r.stdout).strip()[:300]}")


def desktop_config_path(home: Path | None = None) -> Path:
    home = home or Path.home()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    if (home / "Library").is_dir():   # macOS
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def connect_claude_desktop(home: Path | None = None) -> Path:
    """Add our entry to Claude Desktop's config, keeping everything else in it (a copy of the old file is kept)."""
    p = desktop_config_path(home)
    cfg: dict = {}
    if p.is_file():
        try:
            cfg = json.loads(p.read_text() or "{}")
        except ValueError:
            raise ConnectError(f"Claude Desktop's config ({p}) isn't valid JSON; fix or delete it first") from None
        shutil.copyfile(p, p.with_suffix(".json.before-91ninjas"))
    uv, args = _uv_args()
    env = _connector_env()
    cfg.setdefault("mcpServers", {})[SERVER_NAME] = {"command": uv, "args": args} | ({"env": env} if env else {})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(p)
    return p


def connected_apps(home: Path | None = None) -> list[str]:
    """Which Claude apps on this computer have the connector set up (read-only look at their config files)."""
    home = home or Path.home()
    out = []
    try:
        cfg = json.loads((home / ".claude.json").read_text())
        projects = (cfg.get("projects") or {}).values()
        if SERVER_NAME in (cfg.get("mcpServers") or {}) or any(SERVER_NAME in (p.get("mcpServers") or {}) for p in projects if isinstance(p, dict)):
            out.append("Claude Code")
    except (OSError, ValueError, AttributeError):
        pass
    desktop = [home / ".config" / "Claude", home / "Library" / "Application Support" / "Claude"]
    if appdata := os.environ.get("APPDATA"):
        desktop.append(Path(appdata) / "Claude")
    for d in desktop:
        try:
            if SERVER_NAME in (json.loads((d / "claude_desktop_config.json").read_text()).get("mcpServers") or {}):
                out.append("Claude Desktop")
                break
        except (OSError, ValueError, AttributeError):
            continue
    return out


SETUP: dict[str, str] = {}


def ensure_browser() -> None:
    """First start of an installed app: fetch the drawing engine (Chromium for Playwright, ~110 MB) once, in the
    background; the panel says so meanwhile (D-040). Quick when it's there already."""
    import subprocess
    import sys
    import threading

    def run() -> None:
        SETUP["state"] = "Getting the drawing engine ready (first start only)…"
        try:
            r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium-headless-shell"], capture_output=True, text=True, timeout=1800, check=False)
            SETUP["state"] = "" if r.returncode == 0 else f"The drawing engine couldn't be installed: {(r.stderr or r.stdout).strip()[-200:]}"
        except (OSError, subprocess.TimeoutExpired) as e:
            SETUP["state"] = f"The drawing engine couldn't be installed: {e}"
    threading.Thread(target=run, name="setup-browser", daemon=True).start()


def _exit_with_parent(pid: int) -> None:
    """The editor started us (STUDIO_PARENT_PID): stop when it is gone, even if it was killed (D-021)."""
    import threading
    import time

    def watch() -> None:
        while True:
            time.sleep(2)
            if not process_alive(pid):
                os._exit(0)
    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def process_alive(pid: int) -> bool:
    """Whether process `pid` still runs. Never `os.kill(pid, 0)` on Windows: there it *ends* the process
    (TerminateProcess), which closed the app two seconds after it started."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        process_query_limited_information, still_active, access_denied = 0x1000, 259, 5
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == access_denied   # there, but not ours to look at
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    import uvicorn
    if parent := os.environ.get("STUDIO_PARENT_PID", "").strip():
        _exit_with_parent(int(parent))
    token = os.environ.get("STUDIO_SIDECAR_TOKEN") or secrets.token_hex(32)
    port = int(os.environ.get("STUDIO_SIDECAR_PORT", "8791"))
    info = config.data_dir() / "sidecar.json"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}))
    info.chmod(0o600)
    if os.environ.get("STUDIO_BUNDLED") == "1":
        ensure_browser()
    team = Team()
    team.watch()   # new and changed team clients, every few minutes (D-037)
    uvicorn.run(create_app(token, team=team), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
