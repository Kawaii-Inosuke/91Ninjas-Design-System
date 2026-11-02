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

MAX_UPLOAD = 25 * 1024 * 1024


# Request bodies (module level: FastAPI resolves annotations by name, which fails for classes local to create_app).
class InstallBody(BaseModel):
    mode: str = "replace"


class PathBody(BaseModel):
    path: str
    client: str | None = None


def create_app(token: str, studio: Studio | None = None) -> FastAPI:
    """The editor's AI panel back end (D-033): clients, history, what Claude is doing, how to connect Claude, and
    the files to preview. No model calls: Claude does the AI work through the connector (`mcp_server`)."""
    app = FastAPI(title="91Ninjas Studio", docs_url=None, redoc_url=None, openapi_url=None)
    studio = studio or Studio()
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
        return {"ok": True, "editor": studio.bridge_factory() is not None, "ai": "claude"}

    @app.get("/api/connect", dependencies=[api])
    def connect() -> dict:
        """How to connect Claude Code or Claude Desktop to this machine's connector."""
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
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    args = ["--directory", str(AGENTS_DIR), "run", "python", "-m", "studio_agents.mcp_server"]
    quoted = " ".join(f'"{a}"' if " " in a else a for a in [uv, *args])
    return {
        "name": "91ninjas-studio",
        "claude_code": f"claude mcp add --scope user 91ninjas-studio -- {quoted}",
        "claude_desktop": {"mcpServers": {"91ninjas-studio": {"command": uv, "args": args}}},
        "claude_code_installed": shutil.which("claude") is not None,
        "connected": connected_apps(),
    }


SERVER_NAME = "91ninjas-studio"


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


def _exit_with_parent(pid: int) -> None:
    """The editor started us (STUDIO_PARENT_PID): stop when it is gone, even if it was killed (D-021)."""
    import threading
    import time

    def watch() -> None:
        while True:
            time.sleep(2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass
    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


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
    uvicorn.run(create_app(token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
