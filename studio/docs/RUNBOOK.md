# Runbook: set up, build, run

*Last updated: 2026-11-02 (Phase 0). Platform tested so far: Linux, the developer machine only.*

## Prerequisites
| Need | Why | Status on the dev machine (2026-11-02) |
|---|---|---|
| **Free disk ≥ 8 GB** (measured: ~4.5 GB for one dev build, plus headroom) | Rust toolchain plus **one** VectorCraft build at a time (see the disk policy below) | ✅ 16 GB freed. ~11 GB free after the build. |
| **Rust ≥ 1.95** (edition 2024, see `editor/Cargo.toml`) | Builds the editor | ✅ 1.99.1 (`rustup --profile minimal`) |
| Python 3.11+ (own venv in `studio/agents/.venv`) | Agents and renderer | ✅ Python present. The venv is created in Phase 1. |
| Playwright Chromium + `all-MiniLM-L6-v2` | Renderer and duplicate search | Installed into our own venv (Phase 1 and 3) |
| Node | Not needed. Optional for tooling. | ✅ |

## 1. Install Rust (one time)
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # choose the default install
source "$HOME/.cargo/env"
rustc --version   # must be >= 1.95
```
On Linux, the windowing and graphics stack may need system libraries such as X11/Wayland and Vulkan/GL. If the build fails, the error names the missing `-dev` package. Install it with your package manager and record it here.

## 2. Build and run the editor
```bash
cd studio/editor
cargo run --release -p vectorcraft                  # desktop app
cargo run --release -p vectorcraft-cli -- --help    # headless CLI
```
**Disk policy: keep only one build at a time.**
- Use the dev build (`cargo run -p vectorcraft`, output in `target/debug`) day to day.
- Before a release build, delete the dev one (`rm -rf target/debug`). Delete `target/release` again once you no longer need it.
- Install Rust with `--profile minimal`, and set `CARGO_INCREMENTAL=0` to shrink `target/`.
- Build only `-p vectorcraft` and `-p vectorcraft-cli`.
- **Measured (2026-11-02, dev build):** `target/` 3.2 GB + `~/.cargo` 0.7 GB + `~/.rustup` 0.6 GB = **~4.5 GB**. The first build takes ~6 minutes of compile time.
- Linux system libraries needed (all were already present): gcc, pkg-config, x11, xkbcommon, wayland-client, fontconfig.

## 2b. Run the editor with the AI bridge
```bash
cd studio/editor
STUDIO_BRIDGE=1 ./target/debug/vectorcraft --new-instance   # --new-instance: a separate window even if one is open
```
The log prints `91 Studio bridge listening on 127.0.0.1:<port>`. The agents find it through `$XDG_RUNTIME_DIR/91studio/bridge.json`.

**Disk:** the test builds leave large binaries in `target/debug/deps` (the engine's test binary alone is ~430 MB). Deleting them is safe; they are rebuilt when needed.

## 3. Agents project (Python)
```bash
cd studio/agents
uv sync                                          # creates .venv (~230 MB)
uv run playwright install chromium-headless-shell # ~280 MB, shared in ~/.cache/ms-playwright
uv run pytest -q                                 # editor tests need the editor built (step 2)
uv run python -m studio_agents.render.layered_svg design.html -o design.svg --name design-<slug>
```
The sidecar server comes in Phase 3.

## Troubleshooting
- **GPU or window problems on Linux:** see `editor/docs/development.md`, sections "Linux: Wayland and X11" and "Desktop graphics processor". `WGPU_POWER_PREF=high` and `WAYLAND_DISPLAY=` are the usual fixes.
