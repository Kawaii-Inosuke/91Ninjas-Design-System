# Testing

*Last updated: 2026-11-02 (Phase 1)*

Run with `cd studio/agents && uv run pytest -q`. The editor tests skip themselves if the editor isn't built.

| Layer | Test | How | Phase |
|---|---|---|---|
| Duplicate check | Our `dup_check` against a temporary `history.db`: ok, warn and block verdicts, plus layouts to avoid | pytest | 3 |
| Layered SVG ✅ | `tests/test_layered_svg.py` on `fixtures/sample_infographic.html` checks: layer order, one outer group, unique readable ids, 16 live texts with lines, runs, uppercase and letter-spacing, one font per element, no filters, CSS features (clip polygon, gradient, border stroke, dashed `::before`, alpha tint, inline icon with `currentColor`). Pixel match against the browser render is < 0.3% (`tests/imaging.py`: light blur, threshold 48). | pytest | 1 |
| SVG import fidelity ✅ | `tests/test_editor_import.py` runs headless through `vectorcraft-cli run`. It checks: RGB artboard at the design's size, background at (0,0), named groups, 16 Type objects with the right text, exact colours, and the editor's own PNG export within 1% of the HTML. Plus an optional hand check in the desktop app. | pytest + manual | 1 |
| Real designs (local only) | The 13 skill outputs in `Design skill/output/fcc/` gave ≤ 0.03% difference (PHASE-1). They are not in the repo because they are client material, so this can only be re-run locally. | ad-hoc script | 1 |
| Studio bridge ✅ | Rust: 11 tests against a fake app (`cargo test -p vectorcraft-studio-bridge`): token, origin/host, allow-list, path keys, inline place data, batch with refs, cancel on failure, refusal before running, malformed input. Python live tests (`tests/test_bridge_live.py`, skipped without a running editor): exact RGB placement in one step, a turn is one undo step, a failing turn is rolled back, preview/export, refusals. | cargo test + pytest | 2 |
| Engine fix | `cargo test -p vectorcraft-engine --lib`: 3,979 pass, including `placed_svg_with_centred_type_is_measured_by_its_layout` (fails without the fix) | cargo test | 2 |
| Agent graph | Strands graph tests with a stubbed model (deterministic tool calls). Interrupt pause and resume. Critic loop capped at 3. | pytest | 3 |
| Model eval | 5 FCC contents run through the Gemini/Groq agents, compared with the skill's Claude results. Measured: auto_checks failures, critic rounds, designer score. | script + human review | 3 |
| End to end | `POST /runs` on FCC sample content → outline interrupt → resume → design placed → final approval → a history entry recorded with the exported files. | manual, then scripted | 3–4 |
| UI | Chat edit "make the title 20% bigger" changes only the title. Ctrl+Z reverts it in one step. | manual checklist | 4 |
