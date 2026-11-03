# Phase 7: Ready for the team (2026-11-04)

*Decisions: D-036 to D-040. Order agreed with the user at the start of the day.*

| # | What | State |
|---|---|---|
| 1 | The panel knows when Claude is connected (D-034) | ✅ built, tested, screenshot: "Ready for Claude · Claude Code connected" |
| 2 | New designs go into the file in front; Claude follows that file (D-036) | ✅ tests + live (two designs side by side in one file) |
| 3 | Shared team clients: join with a key, share, see the changes and approve, manager's reject reverts everyone (D-037) | ✅ tests with three laptops and a fake GitHub; panel checked on screen. Needs the real repo and key (below) |
| 4 | Endless canvas and frames (D-038) | ✅ the canvas is already endless; the 91Ninjas layout now has every tool, the Artboard (frame) tool included |
| 5 | One-click Connect Claude Code / Set up Claude Desktop (D-039) | ✅ tests (the Desktop config keeps the rest of the file) |
| 6 | Windows and Mac installers, publishing, updates (D-040) | ✅ written; installed layout checked live on Linux. **The Windows/Mac builds run on GitHub: the first tag push is their test** |
| 7 | Visible "VectorCraft" texts renamed (Recover Documents, Preferences notes, Updates window…) | ✅ (the file-version menu keeps upstream's release names) |

Cleaned up as we went: the scratch test copies, my test designs and activity lines, test build files (about 3 GB), the
temporary installed layout and its 380 MB environment. The Playwright browser was missing (a cache clean-up) and was
reinstalled.

## What the user needs to do
**Team clients (once, the manager):**
1. Create a **private** GitHub repo, e.g. `91ninjas-studio-clients`.
2. Make a **fine-grained token**: GitHub → Settings → Developer settings → Fine-grained tokens → only that repo,
   *Contents: Read and write*, longest expiry. This is the **team key**.
3. In the app: Clients → Team clients → repo, key, your name, tick "I'm the manager setting up a new team" → Join.
4. Send designers the repo name and the key (Slack, privately). They join without the manager box.

**Releases (once):**
1. Create a **public** repo for downloads only, e.g. `91ninjas-studio-releases` (a README with the download notes).
2. Another fine-grained token: only that repo, *Contents: Read and write*.
3. In the code repo (`91Ninjas-Design-System`) → Settings → Secrets and variables → Actions: secret
   `RELEASES_TOKEN` = that token; variable `RELEASES_REPO` = `Kawaii-Inosuke/91ninjas-studio-releases`.
4. Release: `git tag v1.0.0 && git push origin v1.0.0`. Watch Actions → "91Ninjas Studio release". If a build
   fails, send me the log.

## Next
- The first release run on GitHub (and fixes from its log), then install on a Windows and a Mac laptop.
- "Install when I close the app" for updates.
- A short designer guide (install, Connect, what to ask, team clients).
