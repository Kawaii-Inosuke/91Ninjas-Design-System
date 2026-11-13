# Moving 91Ninjas Studio development to Windows

**For Claude Code.** Follow this file top to bottom on the Windows laptop to set the project up and continue work. Run each step, check its result, and stop and ask the user if a step fails. Do not skip the "Working rules" section.

The project: an internal desktop design app for 91Ninjas.
- **Editor:** `studio/editor`, Rust, a fork of the VectorCraft vector editor.
- **AI service and Claude connector:** `studio/agents`, Python with uv.
- **Releases:** built on GitHub Actions (`.github/workflows/studio-release.yml`) and published to the public releases repo `Kawaii-Inosuke/91ninjas-studio-releases`.
- **Docs:** everything is in `studio/docs/`. Start with `README.md`, `ARCHITECTURE.md`, `DECISIONS.md` (D-001 to D-049), `RUNBOOK.md` and `phases/`.

Repository: `https://github.com/Kawaii-Inosuke/91Ninjas-Design-System.git` (branch `main`). It is **public**.

---

## 0. What comes from the old Linux laptop (not in git)

The user brings these on a hard disk. None of them are in the repository, on purpose.

| What | Linux location | Windows location | Notes |
|---|---|---|---|
| Security fix list | `Design system/studio/docs/SECURITY-ISSUES.md` | same path inside the clone | **Gitignored. Never commit it while the repo is public.** |
| Legacy Design skill | `Design system/Design skill/` (its own git repo, contains `.env`) | same path inside the clone | Gitignored. Never commit it. `.env` holds credentials. |
| Local Claude settings | `Design system/.claude/settings.local.json` | same path inside the clone | Gitignored. |
| App data (clients, designs, history, team key) | `~/.local/share/91studio/` | `C:\Users\<you>\.local\share\91studio\` | Only if this computer should keep the Linux laptop's clients and history. The **installed** 91Ninjas Studio on this computer already uses this folder. Do not overwrite it without asking the user. |
| Security audit report | `~/security-audit-skill/91Ninjas-Design-System/run-1/` | `C:\Users\<you>\security-audit-skill\91Ninjas-Design-System\run-1\` | Detail for each item in SECURITY-ISSUES.md (N01–N21). Keep it out of the repo. |
| Claude's project memory | `~/.claude/projects/-home-yashodhan-91Ninjas-Design-system/memory/` | the new project's memory folder (see §6) | Optional: §7 below already summarises it. |

**Do not bring:** `studio/editor/target/` (Linux build output), `studio/agents/.venv/` (Linux Python environment), `__pycache__`. They are rebuilt on Windows.

---

## 1. Install the tools (PowerShell)

Use `winget` (built into Windows 10/11). Open a new PowerShell after each install so PATH updates.

```powershell
winget install --id Git.Git -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
winget install --id Rustlang.Rustup -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e        # optional: only for the security-audit validators
winget install --id GitHub.cli -e                # optional: checking releases and runs
```

Then:
```powershell
rustup default stable          # the editor needs Rust 1.95 or newer
rustc --version; cargo --version; uv --version; git --version
git config --global core.autocrlf false    # keep the repo's LF line endings (there is no .gitattributes)
git config --global core.longpaths true    # Cargo's build paths get long on Windows
```

Rust on Windows needs the **MSVC C++ build tools** (the second line above). If `cargo build` fails with "link.exe not found", that is what's missing.

---

## 2. Get the code

Prefer a **fresh clone** over the hard-disk copy, then add the files from §0:
```powershell
cd D:\          # or wherever the user wants it; avoid OneDrive-synced folders
git clone https://github.com/Kawaii-Inosuke/91Ninjas-Design-System.git "Design system"
cd "Design system"
```
Copy in `studio\docs\SECURITY-ISSUES.md`, the `Design skill\` folder and `.claude\settings.local.json` from the hard disk. Then:
```powershell
git status --short    # must print nothing: the copied files are all gitignored
```

**Optional but recommended:** add a Windows Defender exclusion for the clone folder and `%USERPROFILE%\.cargo`. Real-time scanning slows Rust builds a lot. Ask the user before changing Defender.

---

## 3. Build and run the editor

```powershell
cd "studio\editor"
cargo build -p vectorcraft            # first build ~10-15 min; needs ~10 GB free
.\target\debug\vectorcraft.exe
```
- The dev build finds `studio\agents` by walking up from the .exe, starts the AI service with `uv`, and shows the AI panel.
- The installed 91Ninjas Studio (e.g. `D:\91ninjas`) is separate and keeps working. Both use the same data folder (`%USERPROFILE%\.local\share\91studio`), so **don't run the dev build and the installed app at the same time.**
- Start without the AI service: `$env:STUDIO_AGENTS="0"; .\target\debug\vectorcraft.exe`.

---

## 4. Set up the AI service (Python)

```powershell
cd "studio\agents"
uv sync                                            # creates .venv
uv run playwright install chromium-headless-shell  # the renderer's browser (~280 MB)
uv run pytest -q                                   # all tests should pass
```

### Connect Claude Code to the project's MCP connector (once)
```powershell
claude mcp add --scope user 91ninjas-studio -- uv --directory "<full path to>\studio\agents" run python -m studio_agents.mcp_server
claude mcp list        # 91ninjas-studio: Connected
```
The installed app's **AI panel › Claude › Connect** button does the same for Claude Desktop and Claude Code. If both the installed app and this checkout register `91ninjas-studio`, the last one wins.

---

## 5. Tests (run before handing over any change)

From `studio\editor`:
```powershell
$env:CARGO_INCREMENTAL="0"
cargo test -p vectorcraft-ui-egui --lib        # ~2,900 tests
cargo test -p vectorcraft --bin vectorcraft    # ~200 tests
cargo test -p vectorcraft-engine --lib         # ~4,000 tests (big build)
```
Plus `uv run pytest -q` in `studio\agents`.
- Release builds and packaging run only on GitHub Actions. `studio\packaging\windows\package.ps1` needs WiX and is meant for CI, not for local use.
- `studio/editor/target` grows to 10–30 GB. Run `cargo clean` when disk space is short.

---

## 6. Claude's project memory (optional copy)

Claude Code keeps per-project memory under `%USERPROFILE%\.claude\projects\<the project path with separators replaced>\memory\`. The folder name depends on where the clone lives, so start Claude Code once in the new folder, find the new `projects\...` folder, and copy the old memory files into its `memory\` subfolder. If you skip this, §7 has the essentials.

---

## 7. Working rules and current state (read before doing anything)

### Rules from the user
- **The user commits and pushes by hand.** Never run `git commit`, `git push` or `git tag`. Prepare the changes, then give the user a ready-to-paste command block with a commit message.
- **Never commit** `Design skill/`, `studio/docs/SECURITY-ISSUES.md`, the audit report, `.env` files, `.venv`, or any team key.
- **Document every decision** in `studio/docs/DECISIONS.md` (the next number is **D-050**). After each phase, write `studio/docs/phases/PHASE-N.md` and update the affected docs.
- **Explain things to the user in plain words.** They are not a Rust developer.
- **Save tokens.** Prefer working directly over spawning many subagents, and keep build and test output short (grep for results).
- **Releasing:** a `vX.Y.Z` tag push builds Windows and Mac installers on GitHub and publishes them. Installed apps then offer **Update and restart**. The Mac runners use `macos-latest`, because GitHub retired `macos-14` in Nov 2026.

### Where things stand (2026-11-13)
- **Live version: 1.0.5.**
  - It brought the 91Ninjas brand colours (D-048): warm ink and cream with a **Lime** accent. Existing installs keep their saved accent, so the user switches it in Edit › Preferences › User Interface › Accent Color.
  - It locks the install folder to the designer (D-049), verified with `icacls` on the user's laptop.
- **The Windows one-click update works**, tested 1.0.3 → 1.0.4. The Mac update path has **not** been tested on a real Mac yet.
- **Next work is the security fixes in `studio/docs/SECURITY-ISSUES.md`**, in this order: groups 1–3 (approve export scope, client files reading local files, the design-rendering page), then 4–6, then the rest. Item 8 is done. Each fix comes with a test that first shows the problem.
- **Offered, not yet decided:**
  - make the update move installs from the old default Mint to Lime once;
  - automatic install and update tests on GitHub's Windows and Mac runners;
  - making the main repo private (costs about $3 per release in Actions minutes beyond the free $12 a month).
