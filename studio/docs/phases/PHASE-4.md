# Phase 4: the AI panel in the editor

*Status: **in progress** (2026-11-02). 4a, 4b and 4d are done. 4c (rebrand) is waiting for your choices on name and logo.*

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

## Next
- **4c rebrand** (needs your input): the app name, window title, welcome screen, About, app icon and logo. Licence and NOTICE files stay.
- Live end-to-end design in the panel once the API keys are in (carried over from Phase 3).
