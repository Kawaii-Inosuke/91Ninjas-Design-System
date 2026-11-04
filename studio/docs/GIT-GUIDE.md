# Git and GitHub guide for 91Ninjas Studio

*For the owner and manager of 91Ninjas Studio. Last updated: 2026-11-04 (D-035, D-037, D-040).*

The project uses **three GitHub repos**. Designers never need a GitHub account: the app talks to GitHub for them.

| Repo | Visibility | What's in it | Who uses it |
|---|---|---|---|
| `91Ninjas-Design-System` (exists) | **Private** | The code: editor, connector, docs | You (and Claude Code) |
| `91ninjas-studio-clients` (new) | **Private** | The shared clients: design language, rules, past work, fonts | Every designer's app, through the **team key** |
| `91ninjas-studio-releases` (new) | **Public** | Only the installers (Windows `.msi`, Mac `.dmg`) and a download page | Designers download from it; the app checks it for updates |

You do the setup below once. It takes about 20 minutes.

---

## Part A: the shared clients repo (private)

### A1. Create the repo
1. Go to <https://github.com/new>.
2. **Repository name:** `91ninjas-studio-clients`
3. **Private** (important: client material).
4. Leave "Add a README" **unticked** (the app sets the repo up itself).
5. **Create repository.**

### A2. Make the team key
The team key is a GitHub token that can only read and write this one repo.
1. GitHub → your profile picture (top right) → **Settings**.
2. Left menu, at the bottom → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**.
3. **Generate new token**:
   - **Token name:** `91ninjas-studio team key`
   - **Expiration:** the longest it offers. Write the date in your calendar to renew it (A6).
   - **Repository access:** **Only select repositories** → choose `91ninjas-studio-clients`.
   - **Permissions** → **Repository permissions** → **Contents** → **Read and write**. ("Metadata: Read" turns on by itself. Leave everything else at "No access".)
4. **Generate token** and **copy it now**: GitHub shows it only once. It starts with `github_pat_`.

### A3. Set up the team in your app (you, as manager)
1. Open 91Ninjas Studio → AI panel → **Clients** tab → **Team clients**.
2. **Team repo:** `Kawaii-Inosuke/91ninjas-studio-clients`
3. **Team key:** paste the token.
4. **Your name:** e.g. `Shrikant` (the team sees this name on what you share).
5. **Manager password:** choose one (at least 6 characters) → **Join team**. The first manager to join sets it; the repo keeps only a scrambled hash. Tell it only to other managers.
6. Your clients are listed as "not shared with the team yet". Press **Share** on each one you want the team to have.

### A4. Add each designer
Send each designer privately (Slack DM, not a public channel):
- the repo: `Kawaii-Inosuke/91ninjas-studio-clients`
- the team key

They open **Clients → Team clients**, fill in the repo, the key and **their own name**, leave **Manager password** empty, and press **Join team**. The shared clients then appear in their app, waiting for them to install.

### A5. How it works day to day
- **New client:** whoever adds it gets "Share with the team?" (from the app, or Claude asks). Everyone else sees "Priya shared a new client" → **Install** / **Not now**.
- **Changed client:** everyone sees what changed, lines added in green and removed in red, then **Install update** / **Not now**.
- **You, as manager:** besides Install, you see **Reject for everyone**. It puts every laptop back to the earlier version automatically, without asking anyone.
- **Designs are never shared;** they stay on each laptop.
- **Members:** everyone who joined is listed in the repo's `members/` folder (the name only) and, for managers, in the app under Team clients → Members. Leave team removes the name.
- **History:** every share is a commit in the repo. On GitHub, open the repo → **Commits** to see who changed what and when.

### A6. If a laptop is lost, someone leaves, or the key expires
1. GitHub → Settings → Developer settings → Fine-grained tokens → open the team key → **Delete** (or **Regenerate**).
2. Make a new key (A2) and send it to the team.
3. Each designer: **Clients → Team clients → Leave team**, then join again with the new key. Their installed clients stay.

**Another manager** (e.g. Akash) joins with the manager password; the app adds the name to `"managers"` itself. Don't edit `team.json` by hand. To change the password (or remove a manager): delete the `"manager_password"` part of `team.json` on GitHub, then join again with a new password; old manager logins stop working.

---

## Part B: the releases repo (public), for installing and updating

### B1. Create the repo
1. <https://github.com/new>
2. **Repository name:** `91ninjas-studio-releases`
3. **Public** (designers download without an account; it holds only installers, never code or client material).
4. Tick **Add a README file** → **Create repository**.
5. Open `README.md` in the new repo → pencil icon → replace it with the text in **B5** → **Commit changes**.

### B2. Make the release token
This works like A2, but for the releases repo:
1. Settings → Developer settings → Fine-grained tokens → **Generate new token**.
2. **Name:** `91ninjas-studio releases`
3. **Expiration:** the longest offered (calendar reminder).
4. **Only select repositories** → `91ninjas-studio-releases`.
5. **Contents: Read and write**.
6. **Generate** and copy it.

### B3. Give the code repo the token and the name
In the **code** repo `91Ninjas-Design-System` (not the new one):
1. **Settings** → left menu **Secrets and variables** → **Actions**.
2. **Secrets** tab → **New repository secret**:
   - Name: `RELEASES_TOKEN`
   - Secret: paste the token from B2
   - **Add secret**
3. **Variables** tab → **New repository variable**:
   - Name: `RELEASES_REPO`
   - Value: `Kawaii-Inosuke/91ninjas-studio-releases`
   - **Add variable**
4. Check that Actions is allowed: **Settings → Actions → General** → "Allow all actions and reusable workflows" → Save.

### B4. Make a release
Each release has a version number, and every new one must be higher: `1.0.0`, then `1.0.1` for fixes, `1.1.0` for new features.

```bash
cd "/home/yashodhan/91Ninjas/Design system"
git status                         # everything committed?
git pull                           # up to date with GitHub?
git tag v1.0.0                     # the version, with a v in front
git push origin v1.0.0             # this starts the build
```
Then:
1. Code repo on GitHub → **Actions** tab → **91Ninjas Studio release**.
2. Wait about 30–60 minutes for the Windows and the two Mac builds, then publish.
3. A **red ✗:** open the failed job, copy the last lines of the log and send them to Claude Code.
   - After fixing, use a new tag (`v1.0.1`). Or, to rebuild the same version: on the Actions page, **Run workflow** with the version.
4. A **green ✓:** the releases repo → **Releases** has "91Ninjas Studio 1.0.0" with the `.msi`, the two `.dmg` files and `SHA256SUMS.txt`.

**What designers who already have the app see:** the next time they open it (or within 3 hours if it stays open), "91Ninjas Studio 1.0.1 is available" with the release notes and **Update and restart / When I close the app / Later**. One click downloads, checks and installs it; nothing to do by hand (D-043).

**Build time is limited on a free account.** Private repos get 2,000 build minutes a month. Mac minutes count ×10 and Windows ×2, so one release uses roughly **400–500** of them: about **4 releases a month** for free. Save releases for when there's something worth sending; for small fixes, wait and bundle them.

### B5. The download page (paste into the releases repo's README.md)
```markdown
# 91Ninjas Studio

On-brand infographics with Claude, finished in a full vector editor. For the 91Ninjas team.

## Download
Open **[Releases](../../releases/latest)** and download:
- **Windows:** the file ending in `windows-x64.msi` (named `91ninjas-studio-…` from 1.0.2)
- **Mac with Apple chips (M1, M2, M3, M4):** the file ending in `macos-arm64.dmg`
- **Mac with Intel chips:** the file ending in `macos-x86_64.dmg`
  (Apple menu → About This Mac: "Chip: Apple M…" means Apple chips; "Processor: Intel" means Intel.)

## Install (first time only)
**Windows**
1. Double-click the `.msi`.
2. If you see "Windows protected your PC": click **More info → Run anyway**. (This happens once: the app isn't signed with a paid certificate.)
3. Click through the setup. Keep **Create a desktop shortcut** ticked. The app is also in the Start menu.

**Mac**
1. Double-click the `.dmg` and drag **91Ninjas Studio** into **Applications**.
2. Open it from Applications. If macOS says it can't verify the app: **System Settings → Privacy & Security** → scroll down → **Open Anyway** → **Open**. (Once only.)

**Both**
- The first start takes a few minutes and needs internet: the app is getting its AI tools ready.
- Install **Claude Desktop** (<https://claude.ai/download>) and sign in with your Claude account.
- In 91Ninjas Studio: AI panel → **Claude** tab → **Set up Claude Desktop** → restart Claude Desktop.
- AI panel → **Clients** tab → **Team clients**: join with the repo name and team key your manager sent you.

## Updates
The app tells you when there's a new version and asks before installing. Updates don't show the warnings above.
Your clients, designs and settings are kept.
```

---

## Part C: everyday Git for the code repo

You make every commit yourself. Claude Code gives you the commands.
```bash
cd "/home/yashodhan/91Ninjas/Design system"
git status                          # what changed
git add -A studio .github .gitignore
git status --short | grep -iE "\.env$|\.venv|\.db$|team/key"   # must print NOTHING
git commit -m "What changed, in a few words"
git push
```

**Never commit:**
- `.env` files or any key or token;
- `Design skill/` (it has its own repo, `91ninjas-designskills`);
- `.venv/`, `target/`, `studio/dist/`, `*.db`, `*.91pack`.

`.gitignore` already blocks all of these, and the `grep` line above double-checks.

**Release:** tag and push, as in B4.

**If the push is refused** ("fetch first"): run `git pull`, then `git push` again.

---

## Checklist
- [ ] A1 private repo `91ninjas-studio-clients`
- [ ] A2 team key copied (renewal date in calendar)
- [ ] A3 joined as manager, clients shared
- [ ] A4 key sent privately to each designer
- [ ] B1 public repo `91ninjas-studio-releases`, README from B5
- [ ] B2 release token
- [ ] B3 secret `RELEASES_TOKEN` and variable `RELEASES_REPO` in the code repo
- [ ] B4 first release `v1.0.0`: green ✓ and the files on the releases page
- [ ] Install on one Windows and one Mac laptop and try it
