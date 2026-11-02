# Decision log

Format per entry: **Context → Decision → Alternatives → Consequences**. Entries are never deleted. When a decision is replaced, mark the old one *Superseded by D-x*.

---

## D-001: Fork VectorCraft for the editor (2026-11-02)
- **Context:** We need an Illustrator-class editor. The team is one person working with Claude Code and has no Rust experience.
- **Decision:** Fork [storytold/vectorcraft](https://github.com/storytold/vectorcraft) and build on it, pinned at `4ee0ef2`. It is dual-licensed MIT / Apache-2.0.
- **Alternatives:**
  - Write our own editor from scratch, taking ideas from VectorCraft. Rejected: it would take months to reach a usable feature set.
  - Build a web editor in React with an SVG canvas library. Rejected: far fewer professional tools.
- **Consequences:**
  - We get ~710 engine commands, unlimited undo, artboards, and SVG/PDF/AI import and export for free.
  - The UI must be written in Rust/egui, using Claude Code.
  - Upstream changes very fast (repo about one month old), so we pin a commit and merge deliberately (`UPSTREAM.md`).
  - We must remove the ArtCraft/VectorCraft trademarks (Phase 4).

## D-002: Build our own bridge instead of VectorCraft's MCP server (2026-11-02)
- **Context:** VectorCraft ships an MCP server and an unauthenticated TCP control port. The user wants to own the integration.
- **Decision:** Study how `crates/mcp` drives the engine (`research/vectorcraft-mcp.md`), then build our own crate `crates/studio-bridge`. It is a token-authenticated localhost JSON API with a small tool set designed for our agents.
- **Alternatives:** Use `vectorcraft-cli mcp --connect` as it is (less code, but its tool set and security model are theirs, not ours).
- **Consequences:** More Rust to write in Phase 2, but we control the API, the auth and the undo behaviour. The bridge can later also be exposed as an MCP server (Mode B, D-006).

## D-003: AI providers are Gemini and Groq, behind a swappable model layer (2026-11-02)
- **Context:** The user is supplying Gemini and Groq API keys. AI usage must go through company keys.
- **Decision:**
  - Gemini handles vision and reasoning (critic, layout, brief, chat).
  - Groq handles fast, cheap text jobs (bulk tagging).
  - Every agent → provider/model mapping lives in `agents/models.yaml`.
- **Alternatives:** Claude through the Anthropic API or Bedrock. It is the closest match to the existing skill and remains a config-only switch.
- **Consequences:**
  - The skill's prompts were written for Claude, so they must be re-tested on Gemini.
  - Most Groq models are text-only, so they are never used for vision tasks.

## D-004: Agents run as a local Python sidecar (2026-11-02)
- **Decision:**
  - A Python FastAPI + Strands Agents process runs on each designer's machine and is started by the app.
  - It imports `Design skill/skill/*` as a library rather than copying it.
  - Shared state stays in the existing Supabase (registry, brand packs).
- **Alternatives:** A central hosted agent server. Deferred: it needs auth, hosting and Chromium on a server.
- **Consequences:**
  - Each machine needs Python, Chromium (Playwright) and the embedding model, as the skill does today.
  - API keys sit on each machine until a company gateway exists (Phase 5).

## D-005: Designs reach the artboard as layered SVG from the existing HTML pipeline (2026-11-02)
- **Context:**
  - Models are bad at absolute-coordinate layout.
  - The skill's spec → Jinja HTML → Chromium pipeline already produces on-brand layouts and runs `auto_checks`.
  - Its SVG export, however, is flattened.
- **Decision:**
  - Add `layered_svg()`. It walks the rendered DOM with Playwright and emits named groups with live `<text>`/`<tspan>` in brand fonts.
  - The bridge places that SVG on an artboard.
  - Chat edits after placement use fine-grained bridge tools.
- **Alternatives:** Have agents draw shape by shape through editor commands. Rejected: slow, fragile, and poor layout quality.
- **Consequences:** The fidelity of VectorCraft's SVG import (groups, ids → layer names, live text) becomes a key risk, checked in Phase 1.

## D-006: Claude-subscription usage only through Claude Code ("Mode B", optional) (2026-11-02)
- **Context:** The user asked whether the app could use designers' Claude plans without an API key.
- **Decision:**
  - We will not build claude.ai subscription login into our app. As understood, Anthropic doesn't allow third-party apps to use consumer-plan login or limits; **confirm against current terms before Phase 6.**
  - The allowed route is optional: expose our bridge as an MCP server and let designers drive the app from Claude Code on their own plan.
- **Consequences:** Mode A (our Strands agents on company API keys) is the product. Mode B is a later convenience.

## D-008: Bridge design: reuse the app's control request path, not its port (2026-11-02)
- **Context:** The Phase 0 study (`research/vectorcraft-mcp.md`) found that the MCP server and the TCP control port both end up in one place. Requests become `ControlRequest`s on an mpsc channel, which the egui UI thread drains each frame into `control::handle` → `app.run` → `Session::execute`, with panic guard and path confinement.
- **Decision:**
  - `crates/studio-bridge` is our own authenticated HTTP listener, at layer L6. Wiring it into the app binary connects it to that same `ControlRequest` channel, so no ui-egui changes are needed.
  - Each AI turn is wrapped in `edit.undoGroup.begin/end`, which gives one undo step and rolls back on error.
  - Designs are placed with `file.place`, inside one outer named group.
- **Alternatives:**
  - Talk to the upstream `--control` port directly. Rejected: it has no authentication.
  - Call `Session` from a second thread. Rejected: the document is owned by the UI thread.
- **Consequences:**
  - There is very little Rust: one crate, plus a few lines in `apps/vectorcraft/src/main.rs`.
  - Bridge calls wait for the UI thread to draw a frame, so heavy renders delay them.
  - Our crate must pass `cargo xtask ci`: fmt, clippy, layering, no-panic rules, and the vendor-name check on string literals.

## D-009: Clients are local packs shared as files, with no library and no approval (2026-11-02)
- **Context:** Designers must be able to create clients on their own machine and share them with colleagues.
- **Decision:**
  - Each client is a **local brand pack** in the app's data folder.
  - A designer creates one with the in-app **New client** wizard, providing a few past infographics, a design-language file (`DESIGN.md`) and a rulebook (`critic_rules.md`), plus fonts, logo/icons and any guideline PDF.
  - The Brand Onboarder agent can draft `DESIGN.md` and the rulebook from the past work, and the designer reviews and edits them.
  - Sharing is by **exporting one `.91pack` file** and sending it over Slack or Drive. The receiver clicks **Import**.
  - There is **no shared library and no approval step**.
  - Packs **include a few sample past infographics** (capped) so the receiver's agents can use them as style references.
- **Alternatives:** The skill's existing Supabase library with admin approval (`brands_sync.py submit/approve/pull`). Rejected by the user: everything stays local.
- **Consequences:**
  - `brands_sync.py` is not used by the app. The skill keeps it for the terminal workflow.
  - Imported packs come from outside the app, so the importer must validate them (SECURITY T10).
  - Two designers can hold different versions of the same client. The pack manifest carries a version and an author, and import asks whether to replace or keep both.
  - Client font files travel inside packs, so designers must make sure the font licences allow sharing within the team.
  - **Open:** does the duplicate-check registry (Supabase) stay shared across designers, or become local too? See PHASE-0 open issues.

## D-010: The app is standalone; the design skill is a reference, not a dependency (2026-11-02)
- **Context:** The user clarified that 91 Studio is a different product. The `Design skill/` was shared to show the approach that gives good output, not as code or infrastructure to reuse.
- **Decision:**
  - The app **does not import, call or modify** anything in `Design skill/`, and does not use the skill's Supabase.
  - We **port the ideas** into our own code in `studio/agents/`:
    - the brand-pack format (`DESIGN.md` tokens, `critic_rules.md`)
    - the layout families and HTML templates approach
    - the `auto_checks`
    - the critic loop with max 3 rounds
    - the two approval gates
    - the duplicate-topic check (local embeddings)
- **Supersedes:**
  - The "imports `Design skill/skill/*` as a library" part of D-004.
  - The "existing skill pipeline" wording in D-005. The *approach* (spec → HTML → Chromium → layered SVG) stays, but it's our own implementation.
- **Consequences:**
  - More code to write in Phase 3. In exchange there's no coupling: the skill keeps working unchanged for the terminal workflow.
  - **Everything is local** (with D-009): the duplicate-topic registry becomes a local SQLite database with local embeddings, so it only catches repeats on the same machine. **Confirmed by the user, 2026-11-02.**

## D-011: One GitHub repo with the editor vendored in; the user makes all commits (2026-11-02)
- **Context:** The user created https://github.com/Kawaii-Inosuke/91Ninjas-Design-System.git and will make every commit and push personally. Claude only provides the commands.
- **Decision:**
  - The repo root is `Design system/`.
  - `Design skill/` is git-ignored (D-010) and never committed.
  - The editor is **vendored**: the VectorCraft clone's own `.git` is deleted, and its code is committed as normal files. The upstream pin is recorded in `UPSTREAM.md`.
- **Alternatives:** A git submodule pointing to a GitHub fork of VectorCraft. Rejected: it needs a second repo, and the extra steps aren't worth it for a solo builder.
- **Consequences:**
  - Clones get the full editor source.
  - Upstream updates are pulled in by hand: download the new upstream version and diff it against ours (`UPSTREAM.md`).
  - Our edits to upstream files must carry `// 91studio:` comments so they can be found later.

## D-012: Product principle: the AI is the product, the editor is the workbench (2026-11-02)
- **Decision:**
  - The main reason the app exists is the **AI that creates on-brand designs** for designers.
  - The editor exists so designers get the **Illustrator feel and tools** to refine the AI's output.
- **Consequences:**
  - Engineering effort is prioritised on AI output quality: briefs, layouts, critic, brand fidelity.
  - Editor changes are kept to the minimum the AI needs (bridge, AI panel, RGB defaults, rebrand).
  - We don't build new drawing tools unless designers ask for them.

## D-013: How HTML becomes layered SVG (2026-11-02)
- **Context:** Designs are rendered as HTML (D-005). The editor needs named groups and live text (PHASE-0 findings).
- **Decision:**
  - Walk the rendered DOM in Chromium, in CSS paint order.
  - Turn `::before`/`::after` into real elements first, because the templates draw bullets, connectors and rails with them.
  - Measure text per character to get the exact line breaks and baselines, then emit one `<text>` per text block, one `<tspan>` per line, and nested runs for style changes.
  - Layer names follow `data-layer` → id → class → readable tag name (heading, list, item, icon/graphic). Repeated names are numbered (`step-1…3`).
  - Centred and right-aligned blocks keep `text-anchor`, so edits stay centred.
  - **Our own templates (Phase 3) will put `data-layer` on every meaningful element**, so the agents get stable names.
- **Alternatives:**
  - The skill's PDF → SVG export: exact geometry, but flattened, unnamed paths.
  - Having agents draw directly with editor commands (rejected in D-005).
- **Consequences:**
  - The output matches the browser render to ≤ 0.03% on 13 real designs.
  - Unsupported CSS (shadows, filters, transforms, url backgrounds, clipping) produces warnings, so templates should avoid it.

## D-014: Placement procedure for AI designs (2026-11-02)
- **Decision:** Every AI design is placed in four steps:
  1. `file.new {width, height, units: "Pixels", colorMode: "rgb"}`
  2. `file.place {name: "design-<slug>"}`
  3. Read the `background` layer's bounds.
  4. `object.move` the design by minus those bounds, so it sits exactly on the artboard.

  All four run inside one undo group once the bridge exists.
- **Why:**
  - The desktop app defaults new documents to CMYK.
  - `file.place` centres the design's *bounds*, and the editor over-measures multi-line centred text (to be fixed in our fork in Phase 2; the reference move stays as a safety net).

## D-015: A thin Rust bridge; the logic lives in Python (2026-11-02)
- **Context:** The team has no Rust experience. The bridge must be secure and must make AI turns undoable.
- **Decision:**
  - The Rust crate does only what has to happen inside the app:
    - HTTP, the token, and Origin/Host checks;
    - an **allow-list** (art editing, inspect, select, new document, place from inline data, undo; never files, scripts or plug-ins, and never a parameter naming a path);
    - an **atomic batch** that wraps calls in `edit.undoGroup.begin/end`, cancels on the first failure, and supports `$N` references.
  - Everything else (the placement procedure, name→id mapping, turns) is Python in `studio_agents/bridge.py`.
  - The bridge is enabled by `STUDIO_BRIDGE=1` until Phase 4.
- **Alternatives:** One Rust endpoint per tool (`place_svg`, `set_text`, …) as sketched in the research. Rejected: more Rust to maintain, with no gain in safety.
- **Consequences:**
  - New agent tools need no Rust change if their commands are in an allowed family.
  - The allow-list must be widened deliberately, with a test, when a needed command is refused.

## D-016: Minimal upstream engine fixes are allowed (2026-11-02)
- **Context:** `file.place` mis-measured centred type, which made designs land off-centre.
- **Decision:**
  - Fixes in upstream crates are allowed when the agents need them.
  - Each fix is kept minimal, marked `// 91studio:`, covered by a regression test, and listed in `UPSTREAM.md`.
  - Worth offering upstream as a PR later. That would be outward-facing, so it needs the user's go-ahead.
- **Consequences:** Upstream merges must re-apply or drop each listed fix.

## D-017: Design runs are driven in code; agents do the thinking (2026-11-02)
- **Context:** The plan (Phase 0) said to use a Strands `GraphBuilder` graph with interrupts for the approvals. After reading the real Strands 1.63 API, a fixed sequence with long, untimed pauses for the designer is simpler as our own state machine.
- **Decision:**
  - `studio_agents/runs.py` runs the steps in order: brief → duplicate check → layout → [outline approval] → render ⇄ critic (≤ 3 rounds, best kept) → layered SVG → place → [final approval] → export → history.
  - Approvals are pauses in the run, which waits with no time limit.
  - Each thinking step is one Strands agent call with a validated, structured answer (`models.ask`).
  - The Canvas Editor is a Strands agent with two tools (`inspect_design`, `apply_edits`), and each request is one undo step.
- **Alternatives:** Strands Graph + interrupts. Not chosen: harder to test and resume, with no benefit for a fixed sequence.
- **Consequences:**
  - Every path is tested with a scripted model (no API cost).
  - Runs live in memory while the sidecar runs (each is also written to `runs/<id>/run.json`); resuming after a restart is not supported yet.

## D-018: Local embeddings through fastembed (ONNX), not PyTorch (2026-11-02)
- **Decision:** The duplicate check uses `all-MiniLM-L6-v2` (the skill's model) through `fastembed`'s ONNX runtime. The model is 87 MB, downloaded once, and then works offline.
- **Why:** The same vectors without PyTorch, about 800 MB less per designer install.

## D-019: Our own templates and an icon bundle (2026-11-02)
- **Decision:**
  - Seven layout families (`vertical_list`, `horizontal_timeline`, `comparison_cards`, `card_list`, `cell_table`, `column_grid`, `custom`) in our own Jinja templates.
  - Every meaningful element has a `data-layer` name, so the agents get stable names (`item-2`, `heading-2`, `marker-2`).
  - The templates use only CSS that layered SVG converts.
  - Icons come from the official Tabler package 3.50.0 (MIT, sha256 pinned in `scripts/build_icons.py`), bundled as one 2 MB JSON file with search tags.
  - The skill's `brand.css` files are not used; a client may add its own `assets/brand.css`.
- **Safety:**
  - Agent-written markup and CSS are sanitised.
  - The render browser is blocked from the network.
  - Templates get the icon helper per render, never as a Jinja global. A global caused a real bug: the first brand's settings stuck for the life of the process.

## D-020: Similar designs are shown, never blocked (2026-11-02)
- **Context:** The user said designers often ask for several versions of one topic, so "don't repeat a topic" must not stop them.
- **Decision:**
  - The history check never blocks.
  - When anything similar exists (similarity ≥ 0.65), the run pauses on a `similar_exists` gate. It shows each match with its status (approved, in progress, past work), its layout, and **where its files are**, and asks *generate again?* (generate / abandon).
  - Visually similar approved designs are only mentioned when a design is approved; no confirmation is needed.
  - Recently used layouts become a *preference* for the layout agent: a designer asking for "another timeline" gets one.
- **Supersedes:** the blocking parts of D-010 (local registry) and the PRD's "No repeats" requirement and metric.
- **Consequences:** The `0.80 block` threshold and the `dedup_warn` and `visual_duplicate` gates are removed.

## D-021: The editor starts the agents; the AI panel talks to them through a host service (2026-11-02)
- **Context:** Designers should open one app. The editor's UI crate must stay free of networking: it is also built for the web, and its panels "only draw and ask".
- **Decision:**
  - **Launch:** at start-up the desktop app finds `studio/agents` (`STUDIO_AGENTS_DIR`, else next to its install) and `uv`. It starts the sidecar on a free loopback port with a fresh random token, and turns the bridge on. When the window closes, it stops the sidecar. The sidecar also exits by itself if the app is gone (`STUDIO_PARENT_PID`), so a crash or kill leaves nothing running. `STUDIO_AGENTS=0` turns all of this off.
  - **Panel:** `ui-egui/src/panels/studio_ai.rs` ("91 Studio AI", icon `wand-sparkles`, Window menu and icon column) only draws and polls.
  - **Service:** I/O goes through a `StudioAgents` trait (`ui-egui/src/studio.rs`) that the app implements (`apps/vectorcraft/src/studio_agents.rs`). It is a std-only HTTP client on background threads, with an SSE event reader and no new dependencies. Without it (web, tests) the panel says the agents aren't available.
  - **Endpoint:** the sidecar gains `POST /api/runs/json` for the panel.
- **Consequences:**
  - Designer machines need `uv` and the agents' Python environment until Phase 5 packages them.
  - The panel works with any backend that implements the trait, so it can be tested with a fake one.

## D-022: Rebrand what designers see as "91 Studio"; keep internal names (2026-11-02)
- **Context:**
  - VectorCraft's licence requires forks to remove the ArtCraft name, wordmark and logos (`docs/brand/`).
  - The user hasn't chosen a name or logo yet, and said to go on.
- **Decision:**
  - **Name:** product name **"91 Studio"**, one constant (`ui-egui/src/native_menu.rs::APP_NAME`) used for the window title, app id, app bar, Mac app menu, About, and Quit/Hide.
  - **Removed:** the welcome screen's community links, the Discord button and items, the ArtCraft and getartcraft links, the `docs/brand/` logos, and the README logos.
  - **Credit kept:** "built on VectorCraft (MIT OR Apache-2.0)" in About, Help and NOTICE.
  - **Icon:** a placeholder (`assets/91studio/`: a blue tile with "91"), to be replaced by the 91Ninjas logo.
  - **Update check off:** the startup update check looked for *upstream* VectorCraft releases, so it is turned off.
  - **Internal names unchanged:** crate names, the `.vectorcraft` file format and extension, the preferences folder and log names. Renaming them would break files and thousands of tests and add nothing for designers.
- **Consequences:**
  - The theme's orange accent colour is upstream's look, not a trademark. It can change when there's a 91Ninjas palette.
  - Translations of the renamed menu items fall back to English until translated.

## D-023: The user's choices for name, logo and the AI entry point (2026-11-02)
- **Context:** The user supplied the 91Ninjas logo and asked for the "91Ninjas Studio" name, an AI button in place of Discord, and a good "AI …" name for the panel.
- **Decision** (refines D-022):
  - **Name:** product name **"91Ninjas Studio"** (`APP_NAME`).
  - **App icon:** the **91 mark** from the logo, redrawn as vector shapes measured from the supplied image so it is crisp at every size. It is white on a rounded tile in the brand ink `#062324`.
  - **Welcome screen:** the **91NINJAS wordmark** above "Welcome to 91Ninjas Studio", tinted with the theme's text colour.
  - **App bar:** an **"AI" button** (wand icon) where Discord was. It opens the panel.
  - **Panel name:** **"AI Design Assistant"**, listed alphabetically in the Window menu.
- **Licensing:** the 91Ninjas name, mark and wordmark are 91Ninjas trademarks, used by their owner, and recorded as not open source in `ASSETS.md`.

## D-024: The AI panel is a chat; clients are added from it (2026-11-03)
- **Context:** The user found the form-style panel poor, couldn't add a client, and asked for a chatbot inspired by a reference design.
- **Decision:**
  - **Header:** the client chip (initials in the client's primary colour) and a client menu (switch client, *Import client pack…*, *Add client from a brand folder…*), plus a connection dot.
  - **Thread:** designer bubbles on the right; assistant bubbles on the left, with the brief, versions with critic findings and previews, the placement note, and results. Progress lines are small notes, and a spinner shows the current step.
  - **Approvals:** cards in the thread.
  - **Welcome:** a greeting plus suggestion chips (from my text, as a timeline, as a comparison, import pack, add folder).
  - **Message box:** **one** message box with a send button (Enter sends, Shift+Enter breaks the line). A message starts a design when idle, becomes change notes at the outline approval, and becomes an artboard edit once the design is placed.
- **How it's built:**
  - File dialogs go through the editor's `picks` module (off the UI thread on Linux).
  - The sidecar gained `POST /api/clients/from-folder` and `POST /api/clients/import-path`.
- **Also:**
  - FCC is installed locally from its brand folder (`packs.create_from_folder`: 20 past designs, Inter fonts installed for the editor). Its `brand.css` is left out, because it targets other templates.
  - Layout entries like `vertical_list/icon` and the skill's `three_column_grid` now map onto our families. The extra shapes (pyramid, hub_and_spoke…) go to `custom`.

## D-025: The 91Ninjas look: teal-black UI with a mint accent (2026-11-03)
- **Context:** The user shared two mockups: a teal-black UI with a mint accent, the wordmark in the app bar, and a large welcome screen with a "Design with AI" card.
- **Decision:**
  - **Theme greys:** the Modern scheme's dark and Charcoal greys are tinted toward the brand ink `#062324`.
  - **Mint accent:** a new **Mint** accent (`#7ee8c8` on dark, `#0f8a6e` on light) is the default, in both the UI (`Accent::Mint`) and the engine (`uiAccent: "mint"`). The other accents stay selectable.
  - **Lettering on accent fills:** a new token, `on_accent`, colours text and marks on accent fills: dark ink on mint, white otherwise. It replaces the hard-coded white on primary buttons, checkboxes, radio dots, the place badge and the chat panel.
  - **App bar:** the **91NINJAS wordmark** replaces the square mark.
  - **Welcome screen:** a two-colour title, a tagline, large pill **New File** / **Open** buttons, and a **Design with AI** card with an **Open AI Assistant →** button.
- **Not built:** the mockups' account avatar, cloud, Share and Export buttons (nothing behind them yet) and the decorative wave.
- **This machine:** the saved accent was switched from coral to mint in `~/.config/vectorcraft/ui.json`. A backup is at `ui.json.before-mint`.
- **Checked:** 2,884 ui-egui tests pass (3 updated for the wordmark and card); 71 engine prefs tests pass; clippy is clean; checked live in a screenshot.

## D-007: Every phase ends with a phase report and a docs update (2026-11-02)
- **Decision:**
  - At the end of each phase, write `phases/PHASE-N.md` and update the PRD, TRD, ARCHITECTURE and SECURITY docs.
  - Log each new decision here.
