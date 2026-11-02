# Phase 3: agent sidecar

*Status: **closed for now** (2026-11-02). Everything that can be built without API keys is built and tested (57/57). The live-AI items below **carry over**: they run as soon as the keys are in `studio/agents/.env`.*

## Done so far
| Part | What | Tests |
|---|---|---|
| 3a | **Client packs** (`brand.py`, `packs.py`): DESIGN.md tokens and validation, the create-client wizard back end, `.91pack` export, import (zip-slip, links, types, size and count caps, manifest and checksums, SVG and CSS sanitising, entity bombs) and install (replace or keep both), and font install to `~/.local/share/fonts/91studio/<client>` + `fc-cache`. Base critic rules ported (`rules/base_critic_rules.md`). | 12 |
| 3b | **Templates and renderer** (`render/templates`, `render/spec.py`, `render/build.py`, `render/icons.py`): 7 layout families with layer names, a validated spec, auto-checks (canvas, fonts, palette, overflow, < 20 px, missing icons), the offline browser, the markup and CSS sanitiser, and 5,203 Tabler icons with search. Each render takes about 0.9 s. | 13 |
| 3c | **Local history** (`history.py`): reserve, release, approve, plus past work. Similar designs are shown with their file locations and the designer decides whether to generate again; nothing is blocked (D-020). Recent layouts are a preference. Visual look-alikes are reported. Uses fastembed MiniLM (D-018). | 5 |
| 3d | **Agents and run** (`models.py` + `models.yaml`, `steps.py`, `prompts/*.md`, `runs.py`, `editor_agent.py`): brief, layout (with the `search_icons` tool), critic (vision), the state machine with gates (D-017), and the Canvas Editor agent through the bridge. **Server** (`server.py`): token- and origin-checked localhost API and SSE event stream. **Test page** (`web/index.html`), checked in a headless browser end to end. | 7 + 3 |

Whole suite: **57 passed**, including the live bridge tests when the editor is open.

## Changed during the phase
- **D-020:** the user asked that a repeat topic never be blocked. The run now shows the existing design and its file locations and asks *generate again?*.

## Carried over (need API keys)
1. **API keys** in `studio/agents/.env`, then `uv run python -m studio_agents.models --list` to pin real model ids in `models.yaml`.
2. First real runs on the demo brand, then on a real client pack. Tune the prompts on the results.
3. **Brand Onboarder**: drafts DESIGN.md and the rulebook from past work (vision), and tags past work into history.
4. Live Canvas Editor test (chat edit, then one undo).
5. The phase report, with cost per design and an eval against the skill's output.
