# TRD: 91 Studio

*Status: draft v0.1 · Last updated: 2026-11-02 (Phase 0)*

## Stack
| Layer | Choice | Notes |
|---|---|---|
| Editor | VectorCraft fork, Rust ≥ 1.95 (edition 2024), egui/eframe, vello_cpu + wgpu | Pinned at `4ee0ef2` (`UPSTREAM.md`) |
| Bridge | New Rust crate `crates/studio-bridge` | Section "Bridge API" below; design in `research/vectorcraft-mcp.md` |
| Agents | Python 3.11+, `strands-agents` (≈1.6x), FastAPI, uvicorn, Pydantic v2 | Sidecar process started by the app |
| Models | Gemini (Strands Google provider), Groq (OpenAI-compatible) | `AI-MODELS.md` |
| Rendering | Ours: Jinja2 → HTML → Playwright Chromium → PNG, then `layered_svg()` | Approach learned from `Design skill/skill/render.py`. No code dependency (D-010). |
| Data | Local only: the `clients/` folder plus SQLite (`history.db`: designs, briefs, layouts, phash) | No Supabase (D-009, D-010) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-d, local CPU), vectors stored in SQLite | For the similar-design check on this machine |

## Sidecar API (panel → agents), localhost only
Every request carries `Authorization: Bearer <SIDECAR_TOKEN>`. The app generates the token at launch and passes it to the sidecar through an environment variable.

| Method | Path | Body / result |
|---|---|---|
| `POST` | `/runs` | `{client, content?, image_b64?, instruction?, artboard_id?}` → `{run_id}` |
| `GET` | `/runs/{id}/events` | SSE stream: `text` (agent message chunks), `step` (agent/node started or finished), `tool` (tool call name and short args), `gate` (`{kind: similar_exists\|outline\|final, options, payload}`), `artboard` (placed node ids), `usage` (tokens and cost), `done`, `error` |
| `POST` | `/runs/{id}/interrupts/{interrupt_id}` | `{decision: approve\|change\|abandon, notes?}` → resumes the graph |
| `POST` | `/runs/{id}/messages` | `{text, selection_ids?}`. A chat turn, e.g. an edit request, on an existing run or artboard. |
| `POST` | `/runs/{id}/cancel` | Stops the run |
| `GET` | `/clients` | Clients installed on this machine |
| `GET` | `/health` | Versions, model config, bridge reachability |

Runs persist through a Strands `FileSessionManager` under `studio/agents/.sessions/<run_id>`.

## Bridge API (agents → editor)
Implemented in Phase 2 (D-008, D-015). The code is in `editor/crates/studio-bridge/` and the Python client is `agents/studio_agents/bridge.py`.

**Endpoints (as built):**
- `GET /v1/health`
- `POST /v1/call {command, params}`: one engine command.
- `POST /v1/call {method, params}`: one of `ui.render`, `app.export`, `engine.commands`.
- `POST /v1/batch {label, calls: [{command|method, params}]}`: one undo group, with `$N` references and a full rollback on the first failure (the reply carries `failedCall`).

Responses are `{ok, result|error}`. Status codes: 200 OK, 401 token, 403 refused by policy or origin/host, 422 the app refused the call, 400/404/405/411/413 malformed.

**Turning it on:** `STUDIO_BRIDGE=1`. Optional settings: `STUDIO_BRIDGE_PORT`, `STUDIO_BRIDGE_DIR`, and `STUDIO_BRIDGE_TOKEN` (≥ 32 characters, for CI).

*Original design notes, kept for reference:*

**Transport and placement**
- JSON over HTTP on `127.0.0.1:<random port>`, with `Authorization: Bearer <token>`.
- Requests that have an `Origin` header or the wrong `Host` are rejected.
- The crate sits at layer L6 (`xtask/src/layers.rs`).
- The app binary connects the bridge to the existing `ControlRequest` channel. That is the same path the upstream control server uses (`apps/vectorcraft/src/control_server.rs`): the UI thread drains requests each frame, then `control::handle` → `app.run` → `Session::execute`, with panic guard and path confinement.
- We never enable upstream's unauthenticated `--control` port.

**Calls and the engine commands they map to**

| Bridge call | Engine path |
|---|---|
| `inspect {artboard?, ids?}` | `document.inspect`, `document.find` (by name, kind or text) |
| `place_svg {svg, name, artboard?, x, y}` | Python `Bridge.place_svg`: `file.new` (RGB, design size), then a batch with `file.place {name, dataBase64}`, then `document.node {summary}` to build the name→id map. A corrective move runs only if `background` isn't at (0,0); since the Phase 2 engine fix it isn't needed. **Verified live (PHASE-2).** |
| `select {ids}` | selection commands |
| `set_text {id, text}` | `text.setText` for single-style text, or `text.editRange` per run to keep mixed styles |
| `set_fill {ids, color}` | paint commands |
| `transform {ids, dx, dy, scale, rotate}` | object transform commands |
| `turn_begin {label}` / `turn_end {cancel?}` | `edit.undoGroup.begin/end`. One undo step per AI turn, with full rollback on cancel or error. |
| `batch {commands}` | `command.batch`, which is atomic. Steps can reference earlier results as `$N.id` (`engine/src/steps.rs:resolve`). |
| `render_preview {artboard, scale}` | `ui.render` |
| `export {artboard, format, path}` | `app.export`, with the path confined to the output folder |
| `undo` / `redo` | history commands |

**Placement procedure (D-014):** `file.new` (RGB, design size) → `file.place {name: "design-<slug>"}` → read the `background` bounds → `object.move` by minus those bounds, all inside one undo group.

**Document colour mode:** the desktop app defaults new documents to CMYK. The bridge creates AI documents and artboards in **RGB**, because brand tokens are RGB hex (PHASE-0 test 7).

**Rules for the SVG we place** (from the import findings)
- Wrap the whole design in **one outer `<g id="design-…">`**. A top level made only of groups is otherwise imported as separate layers, and their names are lost.
- Every element we may edit later gets an `id`/`data-name`. Node ids are assigned when the design is placed, so the bridge maps names to node ids by walking the placed subtree.
- Use **one** font family per `font-family` attribute, because only the first is used. Brand fonts must be installed on the machine, or rendering silently falls back.
- Use no SVG filters (unsupported filters are dropped).

## Agent tools (Python, all in `studio/agents/`)
| Tool | Does | Idea taken from (reference only) |
|---|---|---|
| `load_brand(client)` | Reads `DESIGN.md` front matter, then returns tokens, palette and rule texts | skill `brand.py` |
| `History.check(client, topic, angle)` | Local embedding search over `history.db`. Returns `ok`, or `similar` with matches (status, similarity, layout, **files**), plus recently used layouts as a *preference*. | skill warn threshold (≥ 0.65); no blocking (D-020) |
| `search_icons(word)` | Searches a bundled open icon set (e.g. Tabler, MIT) | skill icon search |
| `render(spec)` | Jinja HTML, then PNG, then `auto_checks` | skill `render.py` |
| `to_layered_svg(html)` | Layered SVG for the editor | new |
| `canvas_*` (place, inspect, edit, export) | Bridge client (`agents/bridge.py`) | new |
| `History.approve(...)` | Records the approved design and its files; reports visually similar approved designs for information only | skill `registry.log` |

## Client packs (D-009)
**Local storage:** `<app data>/clients/<client>/`, for example `~/.local/share/91studio/clients/` on Linux. It uses the same layout as the skill's `brands/<client>/`:
```
DESIGN.md            design language (tokens + infographic block)  — required
critic_rules.md      rulebook                                       — required
assets/brand.css · assets/fonts/*.woff2 · assets/icons/*.svg · assets/canvas.svg
past/                sample past infographics (PNG/JPG/SVG/PDF)     — required, ≥ 3
source/              original guideline (optional)
```
**Fonts:** installing a pack copies its fonts to `~/.local/share/fonts/91studio/<client>/` and runs `fc-cache`, because the editor draws text with system fonts (PHASE-1 open issue 2).

The pack format follows the skill's `brands/<client>/` layout because it is proven to work. The app reads packs only through its own `load_brand()`.

**Share file `.91pack`:** a zip archive containing the folder above plus `manifest.json`:
```json
{ "format": 1, "client": "fcc", "display_name": "FCC", "version": 3,
  "author": "designer name", "created_at": "2026-11-02T10:00:00Z",
  "app_version": "0.1.0", "files": { "DESIGN.md": "<sha256>", "...": "..." } }
```
- **Export:** writes the manifest with a checksum for every file. It includes at most `past_max` sample infographics (default 10, chosen by the designer), and the whole pack is capped at 100 MB.
- **Import:**
  - Validate the pack (SECURITY T10): extract it to a temporary folder, check the manifest and the checksums, and check that the required files are present.
  - Show a preview: colours, fonts and sample images.
  - Install it. If the client already exists, ask whether to **Replace** or **Keep both** (the copy is installed as `<client>-<version>`).
- **Sidecar API:** `GET /clients`, `POST /clients` (wizard), `POST /clients/{c}/export`, `POST /clients/import` (validate and preview), `POST /clients/import/{id}/install`.

## Non-functional targets (v1)
- From content to a placed first draft: **≤ 90 s** at p50, critic rounds included.
- A chat edit is applied within **≤ 10 s**.
- Model cost per approved design: measured in Phase 3, then budgeted.
- The sidecar's idle memory stays under 1 GB, the embedding model included.
