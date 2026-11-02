# Upstream: VectorCraft

| | |
|---|---|
| Upstream | https://github.com/storytold/vectorcraft |
| Pinned commit | `4ee0ef231aa31f50192f0072554fa390957532bf` (2026-11-02, "Merge pull request #5139") |
| Licence | Dual MIT (`LICENSE-MIT`) / Apache-2.0 (`LICENSE-APACHE`), plus `NOTICE` and `licenses/` |
| Local path | `studio/editor/`, **vendored**: upstream's `.git` is removed and the code is committed as normal files in our repo (D-011) |

## Why we pin
The repo was about one month old at fork time, with about 7.5k commits and dozens of merged PRs a day. Tracking `main` would break our bridge and panel constantly.

## Rules
- **Keep our changes in our own crates where possible:** `crates/studio-bridge`, the AI panel file, and minimal hooks in `apps/vectorcraft`. Every touch to an upstream file gets a `// 91studio:` comment so merges are easy to find.
- **Upgrading upstream:**
  1. Fetch the new commit.
  2. Read the diff of `crates/engine/src/cmd*`, `history.rs`, `crates/svg`, `crates/ui-egui/src/panels/` and the docking code.
  3. Rebuild, then run the bridge smoke test and the Phase 1 SVG fidelity test.
  4. Record the new pin here and in a phase report.
- **Rebranding (Phase 4)** removes the ArtCraft and VectorCraft names and logos from the UI, the app name and the icons, as required by the upstream README trademark note. Licence and NOTICE files stay.

## Our changes to upstream files (D-016)
| File | Change | Why | Test |
|---|---|---|---|
| `crates/engine/src/cmd/place.rs` | `Session::refresh_text_bounds(&mut doc)` before measuring placed art | Centred or right-aligned type was measured left-aligned, so placed designs landed off-centre | `tests/place.rs::placed_svg_with_centred_type_is_measured_by_its_layout` |
| `apps/vectorcraft/src/main.rs` | Control server and bridge share one request queue; `STUDIO_BRIDGE=1` starts the bridge | The bridge (D-008) | `studio/agents/tests/test_bridge_live.py` |
| `apps/vectorcraft/src/control_server.rs` | `start` becomes `start_on(port, ctx, tx)`; test-only imports | Shared queue | upstream control-server tests unchanged |
| `apps/vectorcraft/Cargo.toml`, `Cargo.toml`, `xtask/src/layers.rs` | Bridge dependency, workspace entry, layer row | New crate | `cargo xtask layers` |

| `crates/ui-egui/src/lib.rs`, `services.rs` | `pub mod studio;` and `Services::studio_agents` | The AI panel's host service (D-021) | 2,882 ui-egui tests pass |
| `crates/ui-egui/src/panels.rs`, `state.rs`, `menus/tree.rs` | Register the `studioAi` panel (module, dispatch, `ICON_PANELS` + group, Window menu) | The AI panel | same |
| `apps/vectorcraft/src/main.rs` | Launch the agents before the window, give the panel its client, stop the agents after the window closes | D-021 | live check (PHASE-4) |

| Rebrand (D-022): `ui-egui` `native_menu.rs` (APP_NAME), `chrome.rs`, `canvas/home.rs`, `dialogs/about.rs`, `menus/tree.rs`, `menus/bar.rs`, `menus/ui_commands.rs`, `brand.rs`; app `titlebar.rs`, `main.rs`; tests that assert names (`chrome/tests.rs`, `tests/home.rs`, `tests/nativemenu.rs`, `i18n/tests.rs`, `titlebar.rs`); `README.md`, `NOTICE`, `ASSETS.md`; `docs/brand/` deleted | The product is 91 Studio; ArtCraft trademarks removed as the licence requires | 2,882 ui-egui tests, `xtask assets`, `xtask brands` |

Our own new files: `assets/91studio/` (placeholder icon), `crates/studio-bridge/`, `apps/vectorcraft/src/studio_bridge.rs`, `apps/vectorcraft/src/studio_agents.rs`, `crates/ui-egui/src/studio.rs`, `crates/ui-egui/src/panels/studio_ai.rs`.

## Git setup
- Our repo is https://github.com/Kawaii-Inosuke/91Ninjas-Design-System.git. The repo root is `Design system/`.
- The user makes all commits. Claude only provides the commands (D-011).
- **To pull in a newer upstream later:**
  1. Clone upstream into a scratch folder at the new commit.
  2. Run `diff -ru` (or `git diff --no-index`) against `studio/editor`, excluding `target/`.
  3. Apply the changes, keeping our `// 91studio:` edits.
  4. Rebuild and run the tests, then update the pin above.
