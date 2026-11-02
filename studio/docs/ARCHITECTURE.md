# Architecture: 91 Studio

*Last updated: 2026-11-03 (D-033: Claude through the connector). Decisions referenced as D-xxx are in `DECISIONS.md`.*

91 Studio is a **standalone, fully local** desktop app (D-009, D-010). The `Design skill/` folder is a reference for *how* good infographics are made. The app does not use its code, folders or Supabase.

## Components
```
 Claude Code / Claude Desktop (the designer's own Claude plan): the conversation and the thinking
        │ MCP over stdio (started by Claude)
┌───────┴──────────────────────────────────────────────────────────────────────┐
│  studio/agents · connector (mcp_server.py, D-033): tools, no model           │
│  designs · renderer (HTML→PNG→layered SVG) · clients & packs · history       │
│  local store: clients/, designs/, SQLite history (+ local embeddings)        │
└───────┬───────────────────────────────────────────────▲──────────────────────┘
        │ Bridge API (JSON, 127.0.0.1, token, document uid D-032)
┌───────▼─────────────── 91Ninjas Studio (VectorCraft fork, Rust/egui) ─────────┐
│  Editor engine (upstream): artboards, tools, layers, undo, SVG/PDF/AI I/O     │
│  + crates/studio-bridge (ours): allow-listed engine calls, per document       │
│  + AI panel (ours): Claude status, connect, live activity, clients, history   │
└──────────────────────────────────────────────┬───────────────────────────────┘
                                               │ HTTP (127.0.0.1, token): the panel's local service (server.py)
```
No network traffic of our own: Claude's app talks to Anthropic; everything here is local.

| Component | Language | Responsibility |
|---|---|---|
| Editor engine | Rust (upstream VectorCraft) | Document model, ~710 commands, undo history, rendering, file I/O |
| `studio-bridge` | Rust (ours) | Authenticated JSON calls → engine commands on the UI thread, in the document they name (D-032); a batch is one undo step |
| AI panel | Rust/egui (ours) | Connection status and setup, Claude's live activity, clients and packs, history |
| Connector | Python (ours) | MCP tools for Claude: brand, similar check, render, place, look, edit, approve, new clients |
| Local service | Python (ours) | The panel's API: clients, packs, history, designs, activity, connect |
| Renderer | Python (ours) | Spec → Jinja HTML (brand tokens as CSS variables) → Chromium → PNG preview and `auto_checks` → layered SVG |
| Local store | files + SQLite | `clients/<client>/` packs, `designs/<id>/` versions and placements, design history and similar-design index |

## New-design data flow (D-033)
1. The designer pastes content into Claude ("make an FCC infographic from this").
2. Claude: `get_brand` (once per client), writes the brief, `check_similar` (shows what exists and where; never blocks), shows the outline and waits for the designer.
3. `start_design`, then `render_design` (spec → PNG preview + machine checks). Claude looks at the preview against the rules and renders again to fix (≤ 3 rounds).
4. `place_design`: layered SVG in a **new document of its own**; versions go `beside` on new artboards; `remove_versions` takes the others out in one undo step.
5. Edits: by hand, or by asking Claude (`look_at_editor`, then `edit_design` as one undo step). Claude checks the editor first, so Ctrl+Z, deletions and tab switches are seen.
6. Approval: `approve_design` exports SVG/PNG/PDF from the design's own document (the designer's edits included) and records it in the history.

## Claude's workflow
The connector gives Claude its instructions (`studio_agents/claude/workflow.md`), the spec guide (`spec.md`) and the
new-client guide (`new_client.md`). The Strands agent graph of Phases 3–5 was removed (D-033).

## Clients: local packs (D-009)
```
New client wizard ──► <app data>/clients/<client>/ ──► Export ──► client.91pack ──► Slack/Drive
                         ▲ (Brand Onboarder drafts                                      │
                         │  DESIGN.md + rulebook)                                       ▼
                         └──── Install ◄── preview ◄── validate (T10) ◄── Import ◄── receiver
```

## Trust boundaries
Details are in `SECURITY.md`.
- **Panel ↔ sidecar** and **sidecar ↔ bridge:** localhost only, with a per-launch random token.
- **Model output and imported packs are untrusted.** Tools validate their arguments, file writes are confined to the app data folder, and packs are sanitised on import.
- **Model keys** live only in the sidecar's environment.

## Repository layout
```
Design system/
├── Design skill/        reference only: not used by the app (D-010)
└── studio/
    ├── editor/          VectorCraft fork (+ crates/studio-bridge, AI panel)
    ├── agents/          Python sidecar: agents, renderer, clients/packs (Phase 1+)
    └── docs/            this documentation
```
