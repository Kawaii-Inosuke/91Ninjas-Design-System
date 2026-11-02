# Phase 2: Studio Bridge

*Date: 2026-11-02 · Status: **done**. Rust: 11 bridge tests, plus 3,979/3,979 engine tests with the fix. Python: 17/17, including 5 live tests against the running editor.*

## Goal
Our own secure connection between the AI agents (Python) and the running editor (D-002, D-008):
- the agents can place designs, read them, and edit them;
- each AI turn is **one undo step**;
- nothing outside the agents can drive the editor.

## What was built

### Rust: the editor fork (`studio/editor/`)
| File | Change |
|---|---|
| `crates/studio-bridge/` (new crate, layer L6) | `lib.rs`: start-up, a per-launch random 256-bit token, and `bridge.json` written owner-only. `http.rs`: a minimal HTTP/1.1 server with size caps and deadlines, plus the Origin, Host and token checks. `api.rs`: endpoints, allow-list, and undo-group batching with `$N` references. `tests.rs`: 11 tests against a fake app. |
| `apps/vectorcraft/src/studio_bridge.rs` (new) | Glue: hands bridge calls to the app's existing control-request queue (same give-up and timeout rules). It confines file access to the bridge folder *before* starting the bridge. |
| `apps/vectorcraft/src/main.rs` | `// 91studio:` the control server and the bridge share one request queue. The bridge is on with `STUDIO_BRIDGE=1`. |
| `apps/vectorcraft/src/control_server.rs` | `// 91studio:` `start` becomes `start_on(port, ctx, tx)`, feeding the shared queue. Test-only imports kept. |
| `apps/vectorcraft/Cargo.toml`, `Cargo.toml`, `xtask/src/layers.rs` | Dependency, workspace entry, and layer-table registration (`studio-bridge`, L6). |
| **`crates/engine/src/cmd/place.rs`** | **Engine fix** (`// 91studio:`): `file.place` now lays out type before measuring the art, as opening a file does. Before, centred and right-aligned type was measured with a left-aligned estimate, so placed designs landed off-centre (PHASE-1 issue 1). Regression test `placed_svg_with_centred_type_is_measured_by_its_layout` fails without the fix (width 536.6 instead of 400) and passes with it. |

### Python: `studio/agents/`
| File | What it does |
|---|---|
| `studio_agents/bridge.py` | `Bridge.connect()` reads `bridge.json`. Methods: `call`/`method`/`batch`, `turn()` (one undo step per `with` block, with `$N` refs), `inspect`, `find`, `node`, `new_document` (RGB), `place_svg` (D-014, returns a name → node id map), `render_preview` (PNG), `export` (bytes, no files), `undo`/`redo`. |
| `tests/test_bridge_live.py` | 5 live tests: exact RGB placement in one undo step, a turn is one undo step, a failing turn leaves nothing behind, preview and export, security refusals. Skipped when no editor is running. |

## The bridge API
Full details in TRD › Bridge API.
- `GET /v1/health`
- `POST /v1/call {command, params}`: one allow-listed engine command.
- `POST /v1/call {method, params}`: one of `ui.render`, `app.export`, `engine.commands`.
- `POST /v1/batch {label, calls}`: one undo group. The first failure cancels the whole group and the reply says which call failed.

## Results (live, against the desktop app)
- **Placement:** the design is placed on an RGB 1200×900 artboard with the background **exactly at (0,0)** and **no corrective move needed** (the engine fix works). History shows one step, "Place design-sample", and **51 named layers** are mapped to node ids.
- **One undo step per turn:** the turn "Bigger title, new wording" (`text.setText` + `object.scale`) was one history step, and **one undo reverted both edits**.
- **Rollback:** a turn whose second call failed was **fully rolled back**; the first edit in it was undone too.
- **Preview:** `render_preview` returns the editor's own PNG, matching the design. This is what the critic agent will see.
- **Security:**
  - no token → 401
  - `Origin` header → 403
  - wrong `Host` → 403
  - `document.save`, `file.open`, `file.place {path}` → 403
  - `bridge.json` mode 600, folder mode 700

## Quality gates run
- **Ran:** `cargo fmt --check`, `cargo clippy -D warnings` (bridge and app, tests included), `cargo xtask layers` (OK, 29 crates), `cargo xtask brands` (no vendor names), the engine library tests (3,979 passed), and the bridge tests (11 passed).
- **Not run:** the full `cargo xtask ci`. It also builds the WebAssembly app and runs every crate's tests, which needs the wasm toolchain and more disk than is free. Our changes don't touch the web build (the bridge is only a dependency of the desktop app).

## Decisions taken
Recorded in `DECISIONS.md`.
- **D-015:** A thin Rust bridge with a generic allow-listed `call` plus an atomic `batch`. The logic (placement procedure, name mapping, turns) lives in Python. It is enabled by `STUDIO_BRIDGE=1` for now; Phase 4 turns it on whenever the app launches its agents.
- **D-016:** Upstream engine fixes are allowed when the agents need them, kept minimal, marked `// 91studio:`, covered by a test, and listed in `UPSTREAM.md`.

## Open issues
| # | Issue | Plan |
|---|---|---|
| 1 | `text.setText` keeps only the first run's style (a mixed bold/regular paragraph becomes one style when retyped) | The Canvas Editor agent uses `text.editRange` for mixed-style text (Phase 3) |
| 2 | Brand fonts must be installed for the editor to draw them | Pack install copies them (Phase 3, PHASE-1 issue 2) |
| 3 | The bridge only runs when `STUDIO_BRIDGE=1` is set | Phase 4: the app starts the bridge and the sidecar together |
| 4 | Bridge calls wait for the UI thread to draw a frame, so a minimised window may slow them (upstream behaviour) | Watch in Phase 4; the panel keeps the window active while agents work |
| 5 | Full `cargo xtask ci` not run (wasm toolchain and disk) | Run it before the first release build |

## How to try it
```bash
# Terminal 1: the editor with the bridge
cd studio/editor && STUDIO_BRIDGE=1 ./target/debug/vectorcraft --new-instance
# Terminal 2: all Python tests, including the live ones
cd studio/agents && uv run pytest -q
```

## Next: Phase 3 (agent sidecar)
- `studio/agents/` gets FastAPI + Strands.
- `models.yaml` maps Gemini and Groq models to agents.
- Our own templates are built with `data-layer` names.
- Client packs: local store, `.91pack` import/export, and font install.
- The agent graph: Brief → Dup check → Layout → [outline approval] → Render → Critic loop → Place → [final approval] → Export.
- A temporary web chat page for testing.
- **You'll need to put the Gemini and Groq keys in `studio/agents/.env`.**
