# Testing

*Last updated: 2026-11-03 (D-032, D-033).*

Python: `cd studio/agents && uv run pytest -q` (tests that need a running editor skip themselves).
Rust: `cd studio/editor && CARGO_INCREMENTAL=0 cargo test -p vectorcraft-ui-egui --lib` and `-p vectorcraft-studio-bridge`.
Disk is tight: delete the test binaries in `target/debug/deps` afterwards (RUNBOOK).

| Layer | Test | How |
|---|---|---|
| Designs with Claude ✅ | `tests/test_designs.py`, against a fake editor with documents, artboards, undo and closed tabs: each design gets its own document and the designer's tab never matters; versions side by side, the others removed in one step; `look` sees Ctrl+Z and closed tabs, and `place` brings a version back; approval exports from the design's own document; bad input refused. The connector's tools return text and images (`render_design`, `look_at_editor`). | pytest |
| Connector live ✅ | An MCP client starts the connector over stdio (as Claude does) against a running editor: list, start, render (with image), place, place beside, look, remove one version, edit the other. A script in the session notes, not in CI yet. | manual script |
| Local service ✅ | `tests/test_server.py`: token and origin, client packs over HTTP (create, export, import, install, from folder, pack path), history, activity feed (`after`), connect commands, which Claude apps are connected. | pytest |
| New clients ✅ | `tests/test_onboard.py`: measuring past work (exact colours snapped to declared ones, coverage, radii, sizes, docs), too-few/no folder refused, the DESIGN.md template Claude is shown is a valid pack, `save_new_client` installs and refuses overwrite/bad files, `measure_past_work` returns facts, images and template. | pytest |
| Similar-design check ✅ | `tests/test_history.py`: similar designs are reported with their files and never blocked; other topics and clients pass; abandoned reservations stop counting; look-alikes reported on approval. | pytest |
| Rendering ✅ | `tests/test_render.py`: specs of every layout family render in a brand; auto-checks. | pytest |
| Layered SVG ✅ | `tests/test_layered_svg.py`: layer order, one outer group, unique ids, live text with lines and runs, CSS features; pixel match < 0.3%. | pytest |
| SVG import fidelity ✅ | `tests/test_editor_import.py` (headless `vectorcraft-cli`): RGB artboard at size, named groups, live Type objects, exact colours, editor export within 1%. | pytest |
| Client packs ✅ | `tests/test_packs.py`: create, export, import (zip-slip, caps, sanitising), install modes. | pytest |
| Studio bridge ✅ | Rust (`-p vectorcraft-studio-bridge`, 12 tests): token, origin, allow-list, path keys, inline place data, batches with refs, cancel on failure, calls and batches naming a document (D-032). Python live (`tests/test_bridge_live.py`): placement, one undo step per turn, rollback, preview/export, refusals. | cargo + pytest |
| Documents by uid ✅ | Rust `control::tests`: `studio.inDocument` lands in the named document while the designer's stays in front; closed documents and other methods refused. | cargo |
| Panel ✅ | Rust `panels::studio_ai::tests`: answers fill the panel, quiet retry while the service starts, the new-client request text, which Claude apps are connected. Visual check by screenshot. | cargo + screenshot |
| Team clients ✅ | `tests/test_team.py`: three laptops and a fake repo: join (manager creates), share, new client asks first, update shows the exact lines added/removed, install, the manager's reject reverts everyone without asking, "not now", designs never shared, the GitHub client's requests (no network). `tests/test_server.py`: the team API. | pytest |
| Installed app ✅ (Linux) | An installed layout (the app with `agents/` and `uv` beside it): it builds its environment in the data folder and gives Claude the bundled uv and environment. The Windows/Mac installers are tested by the first run of `studio-release.yml`. | manual + workflow |
| Real use | The designer's checklist in Claude: a design from content, "another one with icons", "make the title bigger", Ctrl+Z then "add it back", 3 versions then "keep B", new content opens a new document, approval files in History. | manual |
