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

## Git setup
- Our repo is https://github.com/Kawaii-Inosuke/91Ninjas-Design-System.git. The repo root is `Design system/`.
- The user makes all commits. Claude only provides the commands (D-011).
- **To pull in a newer upstream later:**
  1. Clone upstream into a scratch folder at the new commit.
  2. Run `diff -ru` (or `git diff --no-index`) against `studio/editor`, excluding `target/`.
  3. Apply the changes, keeping our `// 91studio:` edits.
  4. Rebuild and run the tests, then update the pin above.
