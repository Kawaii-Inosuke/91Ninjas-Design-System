# 91Ninjas Studio: where it stands

*Last updated: 2026-11-03. Details: `phases/PHASE-0..6.md`, decisions `DECISIONS.md` (D-001 to D-034).*

## What it is now
**91Ninjas Studio** is a desktop vector editor (a fork of VectorCraft, Rust) with the 91Ninjas look. Designers make
on-brand infographics by **talking to Claude** (Claude Code or Claude Desktop, on their own Claude plan). Claude reaches
the app through our **connector** (an MCP server on the designer's computer). It builds the design on artboards as
editable layers in the client's brand, and the designer finishes it by hand or by asking. Everything is local:
clients, history and files. The app has no AI keys of its own.

## Built so far
| Area | What works | Decisions |
|---|---|---|
| Editor | VectorCraft fork, pinned and vendored; rebranded (name, icon, welcome screen, About); teal-black theme with a mint accent; layout like the mockup (Control bar, three panel columns, Share and Export, mint tab) | D-001, D-022–D-025, D-028 |
| Studio Bridge | Token-protected localhost API into the editor; allow-listed commands; each AI change is one undo step; every call names its document, so tab switches are safe | D-008, D-015, D-032 |
| Rendering | Spec → brand HTML → Chromium PNG + machine checks → layered SVG with named groups and live text | D-013, D-014 |
| Claude connector | 15 tools: clients and brand, similar-design check, render (with preview), place in its own document or beside, remove versions, look at the editor, edit, approve, list designs, new clients from past work | D-033 |
| AI panel | Claude tab (status, connect with Copy buttons, live activity), Clients (packs, brand folders, new-client request), History (approved files) | D-033, D-034 |
| Clients | Local packs (`.91pack`) shared over Slack or Drive; import with checks; new clients from past work (measured, drafted by Claude) | D-009, D-031 |
| Variants | 2–3 versions side by side; keep one; the others removed in one undo step | D-030 |
| History | Similar topics shown with their files, never blocked; approved designs recorded with their exports | D-020 |

Removed along the way: Gemini, Groq and Strands (D-033), and usage/cost tracking (D-029).

## How to run (developer machine)
```bash
cd "studio/editor" && ./target/debug/vectorcraft          # the app (starts its local service itself)
claude                                                     # in any folder; the connector is registered at user scope
```
Setup on a new machine: `RUNBOOK.md` (Rust build, `uv sync`, Playwright, then the panel's Connect command).

## Open items (as of 2026-11-03)
- **Build and test the panel's "connected" state** (D-034): written and committed, not yet built (PHASE-6 › End of day).
- **Real-use check** with Claude: a design from content, "another one with icons", "make the title bigger", Ctrl+Z then "add it back", 3 versions then "keep B", new content in a new document, approval files in History. Then tune `studio_agents/claude/workflow.md` and `spec.md`.
- **Disk:** about 2–3 GB free. Builds need about 1 GB headroom; a release build or installer needs about 10 GB.

## Before sending it to designers (to decide)
A starting list for the next session; the user will pick and order what's needed.
- **Installer** for designers' machines (5e): a release build and the Python environment bundled, so no Rust, `uv` or terminal setup is needed. Linux first; Mac/Windows if designers use them.
- **One-click connect:** the panel runs the Claude Code `mcp add` (or writes the Claude Desktop config) after asking, instead of copy-and-paste.
- **Updates:** check a releases page, show "Update available", and only update when the designer chooses (ask first, "remind me later", "install when I close the app").
- **Clients for the team:** export FCC (and others) as `.91pack` files to share.
- **Rebrand leftovers:** a few upstream texts still say "VectorCraft" (e.g. the Recover Documents dialog).
- **A short guide for designers:** connect Claude once, what to ask, versions, approving, where files go.
- **Which Claude plan designers need** (Pro vs Max), measured from a day of real use.
