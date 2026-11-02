# 91 Studio: AI Design Studio

A desktop vector editor for 91Ninjas designers with a built-in team of AI agents. You give it a client and some content. The agents follow the same steps a designer does (brief, duplicate check, layout, render, critique, approval) and put the result on the artboard as **editable, layered vector art**. You then keep working on it by hand or by chatting with the agents.

| Folder | What it is |
|---|---|
| `editor/` | Fork of [VectorCraft](https://github.com/storytold/vectorcraft) (Rust/egui), pinned at `4ee0ef2`. See `docs/UPSTREAM.md`. |
| `agents/` | Python: layered-SVG renderer (Phase 1). The agent sidecar (FastAPI + Strands) comes in Phase 3. |
| `samples/` | Small SVGs for hand checks in the editor |
| `docs/` | PRD, TRD, architecture, security, decisions, phase reports |
| `../Design skill/` | **Reference only** (D-010). It shows the approach that gives good output, which we re-implement in `agents/`. The app does not use it. |

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/DECISIONS.md](docs/DECISIONS.md). Current status is in the latest file in [docs/phases/](docs/phases/).
