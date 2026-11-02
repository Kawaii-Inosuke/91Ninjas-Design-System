# Phase 0: setup and study

*Date: 2026-11-02 · Status: **done**. The editor builds, and the headless SVG placement test passed.*

## Goal
Get the editor code in place, understand how VectorCraft's MCP drives the engine so we can build our own bridge, and write the initial project docs.

## What was done
1. **Research** on the three inputs:
   - **Design skill:** its Python scripts are plain CLIs with no LLM calls. The user later clarified it is a *reference* for good output, not part of the app (D-010).
   - **VectorCraft:** a Rust/egui desktop app. It has no built-in AI, but it does have an MCP server and a control port.
   - **rea:** a reverse-engineering toolkit. Not needed, because both VectorCraft and the skill are readable source code.
   - **Strands Agents:** the API surface we'll use (Graph, interrupts, `stream_async`, structured output, model providers).
2. **VectorCraft cloned** into `studio/editor/`, pinned at `4ee0ef2` (2026-11-02). It is a shallow clone, 95 MB. See `UPSTREAM.md`.
3. **MCP internals studied** and written up in `research/vectorcraft-mcp.md` (10 sections with file:line citations). I spot-checked the main claims in the code (`file.place`, `command.batch`, `edit.undoGroup`, `steps::resolve`, `ControlRequest`).
4. **Docs created:** `README.md`, `PRD.md`, `TRD.md`, `ARCHITECTURE.md`, `SECURITY.md`, `DECISIONS.md` (D-001 to D-008), `AI-MODELS.md`, `TESTING.md`, `RUNBOOK.md`, `UPSTREAM.md`.

5. **Rust installed** (1.99.1, minimal profile, user-level) and the **editor built** as a dev build of `vectorcraft` + `vectorcraft-cli`. The build is about 6 minutes of compile time; the 1h06 wall clock includes a laptop sleep.
   - **Measured disk:** `target/` 3.2 GB, `~/.cargo` 0.7 GB, `~/.rustup` 0.6 GB, so **~4.5 GB in total**.
6. **SVG placement test (headless, `vectorcraft-cli run`):** placed a hand-written layered SVG with `file.place`, then ran `document.inspect` and exported SVG and PNG.
   - ✅ Named groups survive as editor groups (`background`, `title`, `item-1`).
   - ✅ Text arrives as **live Type objects** (editable). Two `<tspan>` lines merge into **one multi-line text object** ("Five steps to\nsafer payments").
   - ✅ Colours are exact (`#0b1f3a`, `#1e90ff`, `#ffffff`), and the rounded rect is kept.
   - ✅ The place shows as **one undo step** (history: `["Place"]`).
   - ⚠️ The SVG's outer `<g id="design-test">` is *replaced* by the placed group, which is named after the `name` param. So we pass `name: "design-<slug>"` to `file.place`.
   - ⚠️ The design is centred on the current artboard, which in a new document is letter-size 612×792. The bridge must **create an artboard of the design's size first**, then place at its origin (see the TRD `place_svg` row).
   - Every node has a numeric `id` and a `name`, so the agents can address `title` and `item-1` after placement.
7. **Desktop app check by the user (File › Place `studio/samples/test.svg`):** the design came in as one 800×600 pt Group and rendered correctly. It overhangs the default artboard, as expected.
   - ⚠️ **New documents in the desktop app default to CMYK** (tab title "CMYK/Preview"), while the headless CLI uses RGB. Brand colours are RGB hex, so the bridge must create AI documents and artboards in **RGB** to avoid colour shifts on export.
   - ✅ **Layers panel** shows `test.svg › item-1 / title / background` as separate named layers.
   - ✅ **Isolation** into `item-1` works (breadcrumb Layer 1 › test.svg › item-1).
   - ✅ **Text editing:** the user retyped "Verify the sender" as "yes it is typable". The style was kept.
   - Tip for the GTK file picker: press **Ctrl+L** to type a path. The search box only searches inside Home.

## Decisions taken
Full entries are in `DECISIONS.md`.
- **D-001:** fork VectorCraft for the editor and build on it.
- **D-002:** build our own bridge; we don't use VectorCraft's MCP server.
- **D-003:** AI providers are Gemini and Groq, behind a swappable `models.yaml`.
- **D-004:** agents run as a local Python sidecar.
- **D-005:** designs reach the artboard as layered SVG from the existing HTML pipeline.
- **D-006:** Claude-subscription usage only through Claude Code ("Mode B", optional).
- **D-007:** every phase ends with a phase report.
- **D-008:** the bridge reuses the app's internal `ControlRequest` path (not the unauthenticated port), with one undo group per AI turn.

## Key findings that shape the next phases
- Placing an SVG takes **one command** (`file.place`) and is one undo step. Text comes in as **live text** with per-tspan styles.
- **Gotchas for `layered_svg()`:**
  - Wrap the design in one outer `<g id>`.
  - Give every editable element an id or name.
  - Use one font family per attribute.
  - Use no SVG filters.
  - Brand fonts must be installed locally.
- There is no "place at exact x/y on artboard N" command yet. Workaround: `file.place`, then `object.move`, both inside the undo group.
- `text.setText` loses mixed styling. Use `text.editRange` per run instead.
- **A rebrand is mandatory** for any build we distribute. The build's `xtask brands` check also rejects vendor names like "Illustrator" in Rust string literals.

## Open issues / blockers
| # | Issue | Needed from you |
|---|---|---|
| 1 | ✅ *Resolved:* 16 GB was freed, and the build used ~4.5 GB. Previously: **Disk:** only ~3.9 GB free. ~14 GB free needed (one build at a time, see RUNBOOK). | Free up space (e.g. `Design skill/` is 3.1 GB, mostly `.venv` and `reference/`, and other projects on `/home`) |
| 2 | ✅ *Resolved:* Rust 1.99.1 is installed. Previously: **Rust isn't installed** (needs ≥ 1.95) | OK to install rustup (user-level, no sudo). See `RUNBOOK.md`. |
| 3 | ✅ *Resolved:* the repo is `Kawaii-Inosuke/91Ninjas-Design-System`, with the editor vendored and the user committing (D-011). Previously: **No git repo** for `studio/` or our fork yet | Decide where the 91Ninjas repo lives (e.g. a private GitHub repo), so we can set up `origin` and `upstream` |
| 4 | **API keys** | Put `GEMINI_API_KEY` and `GROQ_API_KEY` in `studio/agents/.env` when Phase 3 starts. Don't paste them in chat. |
| 6 | ✅ *Resolved:* a local duplicate check is confirmed OK by the user. |
| 5 | ✅ *Resolved:* met headless (test 6 above). Still to do: open it in the desktop app and check by eye | Blocked by #1 and #2. Can be done at the start of Phase 1. |

## Next: Phase 1 (layered SVG)
1. Build `layered_svg(html_path) -> svg` in our own `studio/agents/render/`. It walks the DOM with Playwright and emits named `<g>` elements, `<rect>` elements for boxes and backgrounds, inline icon SVGs, and `<text>`/`<tspan>` per wrapped line using `Range.getClientRects()`. The outer group is `design-<slug>`.
2. Write golden tests on the FCC specs (`TESTING.md`).
3. Once the editor builds: place the SVG in the editor and complete the fidelity checklist.

Step 1 doesn't need the editor, so Phase 1 can start before the disk and Rust blockers are resolved.
