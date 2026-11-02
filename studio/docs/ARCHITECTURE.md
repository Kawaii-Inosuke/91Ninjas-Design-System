# Architecture: 91 Studio

*Last updated: 2026-11-02 (Phase 0). Decisions referenced as D-xxx are in `DECISIONS.md`.*

91 Studio is a **standalone, fully local** desktop app (D-009, D-010). The `Design skill/` folder is a reference for *how* good infographics are made. The app does not use its code, folders or Supabase.

## Components
```
┌──────────── 91 Studio desktop app (VectorCraft fork, Rust/egui) ─────────────┐
│  Editor engine (upstream, unchanged): artboards, tools, layers, undo,        │
│  SVG/PDF/AI import + export                                                  │
│  + crates/studio-bridge (ours, D-002/D-008): token-auth localhost JSON API   │
│  + AI panel (ours, egui): chat, approval cards, progress, clients, packs     │
└───────────────▲──────────────────────────────────────────▲───────────────────┘
                │ Bridge API (JSON, 127.0.0.1, token)       │ HTTP + SSE (127.0.0.1)
┌───────────────┴──────────────────────────────────────────┴───────────────────┐
│  studio/agents: Python sidecar, FastAPI + Strands Agents (D-004)             │
│  agents graph · tools · renderer (HTML→PNG→layered SVG) · clients & packs    │
│  local store: clients/ folder + SQLite history (+ local embeddings)          │
└───────┬──────────────────────────────────────────────────────────────────────┘
        │ Gemini / Groq APIs (company keys, D-003): the only network traffic
```

| Component | Language | Responsibility |
|---|---|---|
| Editor engine | Rust (upstream VectorCraft) | Document model, ~710 commands, undo history, rendering, file I/O |
| `studio-bridge` | Rust (ours) | Turns authenticated JSON requests into engine commands on the UI thread. One AI turn is one undo step. |
| AI panel | Rust/egui (ours) | Chat, streamed agent events, approval cards, client wizard, pack import and export |
| Agent sidecar | Python (ours) | Orchestration (Strands Graph), model calls, tools |
| Renderer | Python (ours; approach learned from the skill) | Spec → Jinja HTML (brand tokens as CSS variables) → Playwright Chromium → PNG preview and `auto_checks` → layered SVG |
| Local store | files + SQLite | `clients/<client>/` brand packs, design history and similar-design index |

## New-design data flow
1. **Panel → sidecar:** `POST /runs {client, content, image?, instruction?}`. The sidecar opens an SSE stream.
2. **Brief Writer:** produces a brief (topic, angle, key points, layout family) as structured output.
3. **Similar-design check:** compares the brief against the local history using embeddings. If anything similar exists, the run pauses (gate `similar_exists`) and shows each match with its status and **file locations**. The designer chooses *generate again* or *abandon*. It never blocks (D-020).
4. **Layout Designer:** produces `spec.json` (layout, items, icons, copy).
5. **Interrupt "outline":** the designer chooses approve, change (notes go back to step 4), or abandon.
6. **Renderer:** builds HTML, then PNG, then runs `auto_checks` (canvas size, fonts, off-palette colours, overflow, minimum text size).
7. **Critic:** checks the preview PNG against the client's `critic_rules.md` and our base rules. On fail, the fixes go back to step 4, for at most 3 rounds.
8. **Layered SVG:** named groups, live text, one outer group (TRD "Rules for the SVG we place").
9. **Bridge `place_svg`:** places the design on a new artboard as one undo step.
10. **Designer edits:** by hand, or by chat through the Canvas Editor agent (`inspect`, then a turn wrapped in an undo group).
11. **Interrupt "final":** bridge `export` (SVG/PNG/PDF), then the design is recorded in the local history.

## Agent graph (Strands)
```
Art Director ──► Brief Writer ──► Dup check ──► Layout Designer ──► [outline gate]
                                                    ▲                  │
                                                    │ fixes            ▼
                                                  Critic ◄──── Renderer
                                                    │ pass
                                                    ▼
                                             Place on artboard ──► [final gate] ──► Export + record
Chat edits:   Art Director ──► Canvas Editor (bridge tools)
New client:   Art Director ──► Brand Onboarder (drafts DESIGN.md + rulebook from past work)
```

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
