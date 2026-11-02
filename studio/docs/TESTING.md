# Testing

*Last updated: 2026-11-02 (Phase 0)*

| Layer | Test | How | Phase |
|---|---|---|---|
| Duplicate check | Our `dup_check` against a temporary `history.db`: ok, warn and block verdicts, plus layouts to avoid | pytest | 3 |
| Layered SVG | Golden tests. For each FCC spec, `layered_svg()` produces an SVG with the expected group ids, live `<text>` count and the brand `font-family`. Visual diff of the SVG rendered by Chromium against `vN.png` (pixel-diff threshold). | pytest | 1 |
| SVG import fidelity | Manual checklist in the editor: groups selectable, layer names = ids, text editable, fonts correct, colors exact. Recorded in the phase report. | manual (later headless through the CLI) | 1 |
| Studio bridge | Rust unit tests for the request parser and auth. Smoke test: start the app → Python `place_svg` → `inspect` finds the groups → `set_text` → one `undo` reverts the turn. A request without the token gets 401. | `cargo test -p studio-bridge`, `python -m agents.smoke` | 2 |
| Agent graph | Strands graph tests with a stubbed model (deterministic tool calls). Interrupt pause and resume. Critic loop capped at 3. | pytest | 3 |
| Model eval | 5 FCC contents run through the Gemini/Groq agents, compared with the skill's Claude results. Measured: auto_checks failures, critic rounds, designer score. | script + human review | 3 |
| End to end | `POST /runs` on FCC sample content → outline interrupt → resume → design placed → final approval → a history entry recorded with the exported files. | manual, then scripted | 3–4 |
| UI | Chat edit "make the title 20% bigger" changes only the title. Ctrl+Z reverts it in one step. | manual checklist | 4 |
