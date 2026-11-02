# Phase 4: the AI panel in the editor

*Status: **done** (2026-11-02). The rebrand (4c) was finished at the start of Phase 5 with default choices (D-022).*

## Done
| Part | What | Checked |
|---|---|---|
| 4a | **The editor starts the agents** (`apps/vectorcraft/src/studio_agents.rs`): finds `studio/agents` and `uv`, starts the sidecar on a free loopback port with a random per-launch token, turns the bridge on, and stops the agents when the window closes. The agents also exit by themselves if the editor is killed (`STUDIO_PARENT_PID`). | Live: agents start with the app; after `kill -9` of the editor, no agent processes are left |
| 4b | **The AI panel** (`crates/ui-egui/src/panels/studio_ai.rs`, Window › 91 Studio AI, wand icon). It shows the agents and editor status, client picker, content and instruction, "Make the infographic", live progress lines (brief, similar designs, versions with critic findings), the latest preview image, approval cards (generate again / approve / change with notes / abandon) and, once placed, a chat box for edits. The panel only draws: I/O goes through the `StudioAgents` host service (D-021). | Live screenshot in the desktop app: "Agents ready · editor connected", the Demo Co client listed, a run started from the panel ends with the clear "GEMINI_API_KEY is not set" message |
| 4d | Tests | ui-egui: 2,882 pass (2 new for the panel). Python: 58 (53 pass; 5 live-bridge tests skip when no editor is open). clippy `-D warnings` clean (app, ui-egui, bridge) |

## Decisions
- **D-021:** the editor launches the agents, and the panel talks to them through a host service.

## Problems found and fixed
- A force-killed editor left its agents running. The agents now watch the editor's process and exit.
- The editor forbids egui's `ComboBox` (clippy rule, egui#5225), so the panel uses `craft_ui::ComboBox`.
- egui's `Id::new` is deprecated in this fork, so panel state uses the shared `pstate` helpers.

## 4c Rebrand (D-022)
- **Changes:**
  - "91 Studio" everywhere designers see the app's name.
  - Welcome screen: "Welcome to 91 Studio", plus an "Open the 91 Studio AI panel" button.
  - About: "91 Studio by 91Ninjas, built on VectorCraft".
  - Removed: the Discord button and items, the ArtCraft links, and the ArtCraft logos (`docs/brand/`).
  - Placeholder "91" icon.
  - The upstream update check is turned off.
- **Checked:**
  - 2,882 ui-egui tests pass (11 tests updated for the new names).
  - `xtask assets` (330 files attributed) and `xtask brands` pass; clippy is clean.
  - Live screenshot of the welcome screen and About.

## Carried over
- Live end-to-end design in the panel once the API keys are in (from Phase 3).
- The real 91Ninjas logo, to replace `assets/91studio/` (same file names).
