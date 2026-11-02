"""The agent sidecar: a localhost HTTP API (+ a temporary test page) for the panel (TRD › Sidecar API).

    uv run python -m studio_agents.server            # prints the page URL with its token

Security (SECURITY T2): binds 127.0.0.1; every /api call needs the per-launch token (Authorization: Bearer, or
?token= for <img> sources); requests from other origins are refused; files are served only from inside a run
or client folder.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from studio_agents import brand as brandmod
from studio_agents import config, packs
from studio_agents.runs import RunError, RunManager

PAGE = Path(__file__).with_name("web") / "index.html"
MAX_UPLOAD = 25 * 1024 * 1024


# Request bodies (module level: FastAPI resolves annotations by name, which fails for classes local to create_app).
class InstallBody(BaseModel):
    mode: str = "replace"


class Decision(BaseModel):
    gate: str
    decision: str
    notes: str = ""
    choice: int | None = None        # the `pick` gate: which version (0 = A)


class Message(BaseModel):
    text: str


class PathBody(BaseModel):
    path: str
    client: str | None = None


class StartBody(BaseModel):
    client: str
    content: str = ""
    instruction: str = ""
    image_path: str | None = None    # a picture of the content the designer attached in the panel
    variants: int = Field(1, ge=1, le=3)   # 5c: 2–3 versions with different layouts, side by side


def create_app(token: str, manager: RunManager | None = None) -> FastAPI:
    app = FastAPI(title="91 Studio agents", docs_url=None, redoc_url=None, openapi_url=None)
    runs = manager or RunManager()
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

    def run_or_404(rid: str):
        try:
            return runs.get(rid)
        except RunError as e:
            raise HTTPException(404, str(e)) from None

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

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text()

    @app.get("/api/health", dependencies=[api])
    def health() -> dict:
        from studio_agents.models import roles
        return {"ok": True, "editor": runs.bridge_factory() is not None,
                "models": {r.name: f"{r.provider}/{r.model}" for r in roles().values()}}

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

    # ── runs ────────────────────────────────────────────────────────────────────────────────────────
    @app.post("/api/runs", dependencies=[api])
    async def start(client: str = Form(), content: str = Form(""), instruction: str = Form(""),
                    image: UploadFile | None = File(None)) -> dict:
        images = []
        if image is not None and image.filename:
            images.append(await saved(image, Path(tempfile.mkdtemp(prefix="91studio-in-"))))
        try:
            run = runs.start(client, content, images, instruction)
        except (RunError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        return {"id": run.id}

    @app.post("/api/runs/json", dependencies=[api])
    def start_json(body: StartBody) -> dict:
        """The editor's AI panel starts runs with JSON (the test page uses the form above, for images)."""
        images = []
        if body.image_path:
            p = Path(body.image_path).expanduser()
            if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not p.is_file() or p.stat().st_size > MAX_UPLOAD:
                raise HTTPException(422, "attach a PNG, JPEG or WebP image under 25 MB")
            images.append(p)
        try:
            run = runs.start(body.client, body.content, images, body.instruction, variants=body.variants)
        except (RunError, brandmod.BrandError) as e:
            raise HTTPException(422, str(e)) from None
        return {"id": run.id}

    @app.get("/api/history", dependencies=[api])
    def history(client: str, limit: int = 50) -> list[dict]:
        """The panel's History tab: a client's designs and where their files are."""
        try:
            client = packs.check_client_id(client)
        except packs.PackError as e:
            raise HTTPException(422, str(e)) from None
        return runs.history.list(client, min(max(limit, 1), 200))

    @app.get("/api/runs/{rid}", dependencies=[api])
    def get_run(rid: str) -> dict:
        return run_or_404(rid).public()

    @app.get("/api/runs/{rid}/events", dependencies=[api])
    async def events(rid: str, after: int = -1) -> StreamingResponse:
        run = run_or_404(rid)

        async def stream():
            seen = after
            while True:
                for e in run.events[seen + 1:]:
                    seen = e["seq"]
                    yield f"id: {seen}\nevent: {e['type']}\ndata: {json.dumps(e, default=str)}\n\n"
                    if e["type"] == "done":
                        return
                await asyncio.sleep(0.3)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/runs/{rid}/decide", dependencies=[api])
    def decide(rid: str, body: Decision) -> dict:
        run_or_404(rid)
        try:
            runs.decide(rid, body.gate, body.decision, body.notes, body.choice)
        except RunError as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True}

    @app.post("/api/runs/{rid}/messages", dependencies=[api])
    def message(rid: str, body: Message) -> dict:
        run_or_404(rid)
        try:
            return {"reply": runs.message(rid, body.text[:2000])}
        except RunError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/runs/{rid}/files/{rel:path}", dependencies=[api])
    def run_file(rid: str, rel: str) -> FileResponse:
        return FileResponse(inside(run_or_404(rid).dir, rel))

    return app


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
    print(f"\n  91 Studio agents: open http://127.0.0.1:{port}/#token={token}\n")
    uvicorn.run(create_app(token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
