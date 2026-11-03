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

## D-026: AI panel layout after the user's mockup: tabs, hero and action rows (2026-11-03)
- **Context:** The user shared a mockup of the AI panel: tabs, a large "Design with AI" empty state, action rows, and a composer with icons and a mint send button.
- **Decision:**
  - **Tabs:** **Chat · Clients · History**, underlined in the accent.
  - **Chat when empty:** a mint icon tile, "Design with AI", a line for the selected client, and **action rows** (icon, label, chevron) for the things the agents really do: *Infographic from my text*, *As a timeline*, *As a comparison*, *Add a client*.
  - **Composer:** "Describe what you want, or paste your content…", an **image button** to attach a picture of the content (sent as `image_path`, so the Brief Writer reads it), and a mint **send square**.
  - **Clients tab:** the installed clients (a click picks one), import a `.91pack`, or add one from a brand folder.
  - **History tab:** the client's designs, newest first (new endpoint `GET /api/history?client=`), with **Open SVG/PNG/PDF** buttons that open the files in the editor.
- **Left out:** the mockup's *Recolor*, *Remove background* and *Convert to vector* rows. The agents can't do these yet, and fake rows would mislead.
- **Checked:** 2,884 ui-egui tests and 60 Python tests pass; all three tabs checked live in screenshots.

## D-027: Key rotation, fallbacks and fast failure for the AI models (2026-11-03)
- **Context:** The first real runs on the user's keys showed three things:
  - The `.env` was in `studio/`, not `studio/agents/`.
  - The Gemini key has no quota for Pro, Flash was overloaded ("503 high demand"), and the guessed Groq model didn't exist.
  - Strands' default retry (up to 6 tries, waits up to 240 s) made one step take 7 minutes.
- **Decision:**
  - **Keys:** read from `studio/agents/.env` and `studio/.env`. Several keys per provider are allowed (`GEMINI_API_KEY`, `_2`…`_9`, or a comma-separated `GEMINI_API_KEYS`; the same for Groq).
  - **Rotation:**
    - Every model call goes through `models.with_rotation`. A key, quota, rate-limit, outage or unknown-model error moves to the next key, then to the role's `fallbacks` in `models.yaml`.
    - Fallbacks: Gemini Pro → Flash → 2.5 Flash → Flash Lite → Groq gpt-oss-120b (text-only steps).
    - A rate-limited key rests for 60 s **per model**.
    - A "busy" error gets one retry after 3 s.
    - The key that worked is tried first next time.
    - A bad *answer* is not rotated.
  - **Fail fast:** Strands' retry is set to 1 attempt, the Google and OpenAI client retries are off, and every call has a 90 s timeout.
  - **Critic without vision:** if no model that can see images is up, the critic judges from the spec and the machine checks, and every finding is marked "checked without seeing the image".
  - **Model names:** pinned from what the keys actually list: `gemini-pro-latest` and `gemini-flash-latest` (aliases), Groq `openai/gpt-oss-120b`.
  - **Visible to the designer:** rotations appear in the chat as quiet notes.
- **Also fixed:** the font auto-check failed on brand fonts whose unused weights never load, which made the critic loop run to its limit. It now passes when any face loaded.
- **Result:** a full real FCC design (brief → layout → render → critic → place → export) completed on the user's keys, with Gemini Flash Lite carrying it while the other models were unavailable. The outline was ready in 36 s, down from 446 s.

## D-028: The editor's layout follows the 91Ninjas mockup (2026-11-03)
- **Context:** The user's mockup shows the Illustrator-style layout: a second bar (Control bar) with Stroke, Opacity and Style; three open panel columns (Layers · Color · Stroke, Properties, AI Assistant); Share and Export at the top right; and a mint document tab. The app showed one tabbed panel column and a strip of icons, no Control bar, and a search box and workspace switcher at the top right.
- **Decision:**
  - **New built-in workspace "91Ninjas"**, listed first:
    - The Control bar, rulers and the two-column toolbar are on. The floating Contextual Task Bar and the hint line are off.
    - Three open columns, from the right:
      - AI Design Assistant;
      - Properties · Appearance · Libraries;
      - Layers · Artboards · Asset Export over Color · Swatches · Gradient over Stroke · Transparency.
    - The other panels stay in Window.
    - Every profile switches to it once (`studio_workspace_adopted`). A designer who picks another workspace keeps it, and Window › Workspace › Reset brings it back.
  - **Top bar:**
    - **Export** (mint) opens Export As PNG / SVG / As… / for Screens.
    - **Share** opens Package… / Save a Copy… (handing a design to another designer).
    - Both are existing File commands; nothing new exports.
    - The search icon and the workspace switcher left the bar: the command palette keeps its shortcut, and workspaces stay under Window › Workspace.
    - A narrow window drops Share first.
  - **AI button:** at the Control bar's right end, outlined in mint. It is in the top bar only while the Control bar is hidden, and only shows when the app runs the agents.
  - **Document tab:** the active tab is mint with dark lettering.
  - **AI panel:** while the agents start (about 10 s), it asks again quietly every 1.5 s for up to a minute, instead of showing "the agents aren't running". It matters now because the panel is always open at start.
  - **Not built:** the mockup's cloud icon and profile picture. The app has no accounts or cloud, so they would do nothing.
- **Consequences:**
  - Upstream tests changed only where they named the old bar (`titlebar.rs` widget count and Share/Export, the `workspaces.rs` default test).
  - New test `studio_workspace_is_adopted_once_with_three_open_columns`.
  - 2,885 ui-egui tests and clippy pass. Checked live with screenshots at 1920×958.

## D-029: Claude through a connector is the main AI; cost tracking dropped (2026-11-03)
- **Context:** The user wants designers to use their own Claude subscriptions. Gemini gave decent results, but the free key runs out daily and is often overloaded. The chat panel also lost the thread of the conversation: after Ctrl+Z, "add it back" was taken as new content and refused. The panel is a fixed pipeline that files each message as new content, outline notes or an edit; it has no memory of the chat and doesn't see the editor's undo.
- **Decision:**
  - Order of work: **5c Variants, 5d Brand Onboarder, then the Claude connector** (Phase 6, Mode B), ahead of the installer.
  - **5b (usage and cost tracking for our API keys) is dropped**: with Claude, designers use their own plans.
  - The Gemini panel stays as it is and keeps working.
  - New steps (variants, onboarding) are built as parts both the panel and Claude can call, so nothing is built twice.
  - The connector will give Claude the conversation-level tools the panel lacks: look at the artboard, and place a saved version again ("add it back").
- **Not allowed (unchanged, D-003):** our app signing in with a claude.ai subscription. Claude Desktop or Claude Code drives our app through an MCP server, which is the supported way.

## D-030: Variants: 2–3 versions with different layouts, side by side (2026-11-03)
- **Decision:**
  - **Asking:** a run takes `variants` (1–3). In the panel, use "Versions 1 2 3" in the message box, or the "Three versions to choose from" row.
  - **Layouts:** each version's outline must use a layout family the others don't. The layout agent is told which families are taken, and asked once more if it repeats one (the repeat is kept if the second answer fails).
  - **Outline approval:** one approval covers all the outlines. Notes apply to every version.
  - **Drawing and checking:** each version is drawn and checked by the critic on its own.
  - **Placement:** in the editor, version A opens a document named after the design on artboard "Version A". B and C get artboards to its right ("Version B", "Version C", 80 pt apart), with the art aligned to each artboard.
  - **New `pick` gate:** the panel shows each version's preview, layout, title and remaining issues, with **Keep A/B/C** buttons. Typing the letter also works.
  - **After the pick:** the versions not kept leave the document (their layers and artboards) **in one undo step**; their files stay in the run folder. The run then continues as a single design (final approval, edits by chat).
  - **Without the editor:** the versions are still offered as files.
- **Also:** `run.json` is now written whole and then swapped in (a test showed a half-written file when the process stopped mid-write).
- **Checked:**
  - 4 new run tests, 74 Python tests and 1 new panel test pass (2,886 ui-egui tests, clippy).
  - **Live with the AI:** outlines for 2 versions came back with different families (timeline, list). The rest of that run failed because the free Gemini key's daily quota and Groq's rate limit were used up.
  - **Live in the editor:** scripted answers stood in for the AI. 3 versions landed side by side, picking B removed A and C in one step, and "Version B" remained.

## D-031: Brand Onboarder: a new client drafted from past work (2026-11-03)
- **Context:** Adding a client meant writing DESIGN.md and a rulebook by hand. Designers have the client's past infographics, and sometimes a guidelines document.
- **Decision:** `studio_agents/onboard.py` works in three steps:
  1. **Measure, without AI:**
     - **Files:** reads a folder and its subfolders: up to 30 past designs (SVG, PNG, JPEG, WebP), font files, and `.md`/`.txt` guidelines.
     - **Rendering:** SVGs are drawn with the offline Chromium.
     - **Colours:** colour coverage is measured on small copies, and then snapped to the exact colours the SVGs declare. Edge smoothing would otherwise invent in-between colours. Pure white and black are always candidates, since page backgrounds are often not declared.
     - **Other facts:** corner radii (above 0), canvas sizes (`width`/`height` or `viewBox`), and the fonts named in the files.
     - **Previews:** up to 6 preview images.
  2. **Draft, with AI** (the `onboarder` role, vision): the AI fills **fixed slots**:
     - the colour roles the templates read (primary, accent, ink, surface, tint, cell, line, on-primary, optional header-bar, plus other colours);
     - the title, heading, body, label and marker type styles;
     - radii, spacing, canvas width and layouts;
     - a style guide and 5–40 rules.

     Colours are checked and normalised after the answer, because not every model honours patterns in a schema. The measured numbers are given as facts; guideline documents and designer notes are passed as data, not instructions.
  3. **Review and save, by the designer:** the draft is written to `drafts/<id>/DESIGN.md` and `critic_rules.md`. The designer can edit them (**Edit the draft files** opens the folder, then **Reload**), and **Save client** runs `packs.create_client` with the past designs, fonts and documents.
- **Panel:** Clients → **New client from past work…**: name, folder, optional notes → progress → review card (swatches, fonts, layouts, number of rules, problems) → Save / Edit / Reload / Discard.
- **API:** `POST /api/onboard`, `GET /api/onboard/{id}`, `POST /api/onboard/{id}/save`, `DELETE /api/onboard/{id}`.
- **For the Claude connector:** measuring and saving are separate from drafting, so Claude can draft from the same facts.
- **Text in outlined SVGs:** the font is judged from the images and the guide says so.
- **Checked:**
  - 8 onboarder tests (with a scripted model), including a drafted client rendering a real design. 1 new panel test. 2,887 ui-egui and 72 Python tests, clippy.
  - Measuring FCC's real past work gave white 53.5%, #F2F2F2 34%, the FCC blues and #383838 for text, 1920 px wide.
  - The form was checked on screen. **Not yet live with the AI:** the free Gemini key's daily quota was used up.

## D-032: Every AI action names its document (2026-11-03)
- **Context:** The user switched tabs while a design was being made. The AI's later steps landed in the tab in front: a new design seemed to replace an old one, and an approval exported an empty Untitled-1 as the final files. Cause: the bridge acted on whichever document was active, and each step was a separate request.
- **Decision:**
  - **Editor:** three new control methods:
    - `studio.documents` lists the open documents with their stable `uid`;
    - `studio.newDocument` creates one and returns its uid;
    - `studio.inDocument {uid, method, params}` runs one call in that document within a single request (switch to it, run, switch back), so the designer's tab stays in front and can't be redirected.
  - **Bridge:** `/v1/call` and `/v1/batch` take an optional `document` (uid). Every step of a batch, its undo-group begin and end included, then goes through `studio.inDocument`. Agents can't call `studio.inDocument` themselves.
  - **Python:** `Bridge.in_document(uid)`; `new_document` returns the uid and aims the bridge at it; `Placed.document`. Each design remembers the document of each placed version.
- **Checked:** a Rust bridge test, an editor test (the rectangle lands in the AI's document while the designer's stays in front), Python tests against a fake editor, and live through the connector.

## D-033: Claude is the only AI; Gemini and Groq removed (2026-11-03)
- **Context:** The chat panel misread follow-ups ("another one with icons" was taken as new content; "add it back" after Ctrl+Z was refused). It was a fixed pipeline that filed each message as new content, outline notes or an edit, with no memory of the conversation and no view of the editor. The user chose to work only with Claude, through the designers' own Claude plans (D-029).
- **Decision:**
  - **The connector** (`studio_agents/mcp_server.py`, an MCP server over stdio, MCP Python SDK 2.x) is how Claude Desktop or Claude Code drives the app. Claude does the thinking (brief, outline, layout, critique, edits) inside its own conversation. The tools need no model:

    | Tool | Does |
    |---|---|
    | `list_clients`, `get_brand` | brand, layouts, spec guide and rules |
    | `check_similar` | the similar-topic check (never blocks) |
    | `start_design`, `render_design` | render a version; returns the preview image and machine checks |
    | `place_design` | its own document, or `beside` for versions |
    | `remove_versions` | one undo step |
    | `look_at_editor` | is each version still there after Ctrl+Z, deletion or a closed tab; layers; preview |
    | `edit_design` | one undo step |
    | `approve_design`, `abandon_design`, `list_designs` | finish or drop a design; find earlier ones |
    | `search_icons` | icon names |
    | `measure_past_work`, `save_new_client` | new clients |

    Claude gets the 91Ninjas workflow as the server's instructions (`claude/workflow.md`): one piece of content is one design in its own document, follow-ups refer to the work in progress, look before editing, and approve only when the designer says so.
  - **`designs.py`** (no AI) keeps each design's versions and placements in `designs/<id>/design.json`.
  - **Removed:**
    - `models.py`, `models.yaml`, `steps.py`, `runs.py`, `editor_agent.py`, the prompts, and the test page;
    - the Strands, google-genai, openai and dotenv dependencies (the environment shrank by about 80 MB);
    - the `.env` keys are no longer read.

    The onboarder's AI draft step went with them; measuring stays and Claude drafts.
  - **The editor's panel** became Claude's companion, with three tabs:
    - **Claude:** status; how to connect Claude (the Claude Code command and the Claude Desktop config, with Copy buttons; paths filled in by `/api/connect`); a live feed of what Claude is doing, read from `activity.jsonl`, which the connector writes.
    - **Clients:** list, import, brand folder, and "New client from past work", which prepares the request to Claude.
    - **History.**
  - **The local service** (`server.py`) serves only clients, packs, history, designs, activity and connect: no runs.
  - **The connector's environment:** Claude starts it with a minimal environment. Without `XDG_RUNTIME_DIR` it looks for the bridge file in `/run/user/<uid>/91studio`.
- **Consequences:**
  - Designers need Claude Code (Linux, Mac, Windows) or Claude Desktop (Mac, Windows) on a Pro or Max plan. Nothing is charged to 91Ninjas per design.
  - The Gemini-era steps (5b dropped; 5c and 5d were built on Gemini) live on as connector tools and in Claude's instructions: versions side by side (D-030) and new clients from past work (D-031).
- **Checked:** 55 Python tests (13 new for designs, the connector, the server and onboarding), 2,885 ui-egui tests, clippy. Live: the connector started over stdio (as Claude starts it) rendered, placed in its own document, placed a version beside it, looked, removed one version in one step, and edited the other, in the real editor.

## D-034: The panel knows when Claude is connected (2026-11-03)
- **Context:** After connecting Claude Code, the panel still showed the setup steps, so it looked as if nothing had happened.
- **Decision:**
  - `/api/connect` reports `connected`: the Claude apps whose config names the `91ninjas-studio` server. It reads Claude Code's `~/.claude.json` (user or project scope) and Claude Desktop's `claude_desktop_config.json` (Linux, Mac and Windows paths), read only.
  - The Claude tab then shows "✓ Claude Code is connected" with what to ask, keeps the setup behind "Connect another Claude app", and checks again every 5 s until something is connected.
- **State:**
  - **Built and tested:** the local service part (1 test).
  - **Not yet built or tested:** the panel part. It's written, but was interrupted before the build (see PHASE-6 › End of day).

## D-035: Distribution for the internal team: free, unsigned, public releases repo (2026-11-03)
- **Context:** Designers use Windows and Mac and have no GitHub accounts. The app is for the internal 91Ninjas team only. Nothing is to be paid (no code-signing certificates, no Apple Developer account, no Microsoft Store) and there will be no open-source licence change (so not SignPath).
- **Decision:**
  - **Installers:** Windows `Setup.exe` (setup wizard, Start menu entry, desktop icon, uninstall in Apps) and Mac `.dmg` (drag to Applications; Launchpad, Spotlight, Dock). Each bundles the editor, the connector, Python and the renderer. Designers install only Claude Desktop besides.
  - **Built by GitHub Actions** on GitHub's free Windows and Mac machines when a version is tagged (nothing built on the developer's laptop).
  - **Hosted on a public GitHub repo used only for releases** (installers + a latest-version file). The source code stays in the private repo. Designers download without an account.
  - **Unsigned:** the **first install** shows the system warning once. Windows: "Windows protected your PC" → *More info → Run anyway*. Mac: *System Settings → Privacy & Security → Open Anyway*. The Mac app is ad-hoc ("self") signed (free) so new Macs don't call it "damaged". The download page explains the one-time click with screenshots.
  - **Updates:** the app checks the releases repo and **asks first** (Update now / Remind me later / Install when I close the app). It downloads and installs updates itself; files the app downloads aren't marked "from the internet", so **updates show no warning**. Clients, designs and settings are kept.
- **Not chosen:** Microsoft Trusted Signing, OV/EV certificates, Microsoft Store, Apple notarization (cost), SignPath Foundation (needs open-source licensing).

## D-036: New designs go into the file in front; Claude follows that file (2026-11-04)
- **Context:** The user wants a new infographic to join the same file, not replace the earlier one or open somewhere else, until they open a new file. They also want Claude's work tied to the file they're in.
- **Decision:**
  - **Placing:** `place_design` puts a design into the file in front.
    - It goes onto the file's only artboard if that artboard is empty (resized to the design), else onto a new artboard to the right of the others, named after the design.
    - A new file only when no file is open, the designer asks (`new_file`), or the file in front is CMYK with art in it. An empty CMYK file is switched to RGB, since brand colours are RGB.
    - `beside` still puts versions next to each other.
  - **Following the file:** new tool `current_file` returns the file in front and the designs placed in it. Claude's instructions make it the context of each request ("it", "this", "the last one"), say a new empty file is a fresh start, and say never to remove or replace an earlier design unless asked.
  - **The limit:** Claude's chat is Claude's own window, so it can't be split per file. Claude's *work* follows the file; `/clear` starts a fresh chat.
- **Checked:**
  - Tests: designs collect in one file; a new empty file is used and resized; CMYK handling; current_file.
  - Live: two designs landed side by side in one file.

## D-037: Shared team clients through a private GitHub repo, with a team key (2026-11-04)
- **Context:** Clients should be shared automatically, not on Drive. Designers have no GitHub accounts. Every change must be shown (added and changed, old and new) and approved before it installs. The manager can reject a change, and then everyone goes back automatically. Designs stay local.
- **Decision:**
  - **`team.py`:** the app talks to the GitHub REST API itself (git data endpoints, one commit per share, standard library only) with one **team key**: a fine-grained token for that repo only, contents read and write.
    - **Joining:** each laptop joins once with the repo, the key and the designer's name. The key is stored owner-only. The manager creates the team, and `team.json` lists the managers.
    - **Repo layout:** `clients/<id>/` holds DESIGN.md, critic_rules.md, past/, assets/fonts, assets/icons, source/, plus `meta.json` (version, by, when, message, the previous commit, reverted).
    - **Share:** a new version. The panel offers it for clients not yet shared, and Claude offers it after saving a client.
    - **Check** (at start, every 3 min, and on demand) shows, for each new or changed client, who changed it and what: lines added and removed in the design language and the rules, and files added and removed. **Install** or **Not now**; "not now" stays quiet until a newer version.
    - **Manager's Reject for everyone:** a new version holding the files as they were before the change, marked `reverted`. It installs everywhere without asking, and the panel says so.
  - **Installing** goes through `packs.create_client` (validated and sanitised like a pack).
- **Limits:**
  - Anyone with the key can share. Manager status was by name in `team.json`; since D-041 it needs the manager password.
  - Rotate the key if a laptop is lost.
  - Client material lives in a private GitHub repo.
- **Checked:** tests with three laptops and a fake repo (join, share, new, update with the exact diff, reject reverts everyone, not now, refusals), the GitHub client's requests, and the API.

## D-038: The endless canvas and frames (2026-11-04)
- **Context:** The user wants a Figma-like workspace: an endless canvas with frames anywhere.
- **Found:**
  - The canvas already extends 4 million points each way (`MAX_COORD`). The view scrolls freely past the art (checked live: 240,000 points down).
  - Art can sit outside artboards.
- **Decision:**
  - Frames are the editor's artboards, drawn anywhere with the Artboard tool (Shift+O).
  - The 91Ninjas workspace now uses the **Advanced toolbar**, which has every tool, the Artboard tool included. Adding it to the Basic toolbar would have shifted upstream toolbar positions that about 15 tests depend on.
  - Not done: renaming artboards to "frames" across the UI, and nesting or clipping like Figma frames.

## D-039: One-click Connect Claude (2026-11-04)
- **Decision:** the Claude tab has **Connect Claude Code** and **Set up Claude Desktop**.
  - **Claude Code:** runs `claude mcp add --scope user …`, replacing an older entry of ours.
  - **Claude Desktop:** adds our entry to its config (Linux, Mac and Windows paths) and keeps everything else in it, plus a copy of the old file.
  - The copy-and-paste commands stay, for doing it by hand.

## D-040: Installers and updates (2026-11-04)
- **Decision** (following D-035):
  - **Workflow:** `.github/workflows/studio-release.yml` builds, on GitHub's machines when a `v*` tag is pushed (or by hand):
    - `vectorcraft-<v>-windows-x64.msi`;
    - `vectorcraft-<v>-macos-arm64.dmg` and `vectorcraft-<v>-macos-x86_64.dmg` (both built on the Apple-chip machine).

    It then publishes them with `SHA256SUMS.txt` and the download notes to the **public releases repo** (`RELEASES_REPO` variable, `RELEASES_TOKEN` secret). The asset names keep the `vectorcraft` stem, which the updater matches.
  - **Windows** (`studio/packaging/windows/91ninjas-studio.wxs`, `package.ps1`):
    - a per-user MSI in `%LOCALAPPDATA%\Programs\91Ninjas Studio`, so no admin rights, and in-app updates need none either;
    - a setup wizard with a ticked **Create a desktop shortcut** box, a **Start menu** entry, an uninstall entry, and "Start 91Ninjas Studio" at the end;
    - the 91 icon in the .exe and the installer, a new UpgradeCode, and the `SOFTWARE\91Ninjas Studio\InstallDir` registry value the updater finds.
  - **Mac** (`studio/packaging/macos/package.sh`): "91Ninjas Studio.app" (bundle `com.91ninjas.studio`, the 91 icon), ad-hoc signed (free), in a DMG with an Applications link.
  - **Bundled:** the editor, `uv` 0.13.1, and the agents (source and lock file).
  - **First start:** the app runs `uv run --frozen --no-dev` with the Python environment in `~/.local/share/91studio/agents-env`. uv fetches Python and the packages once, and the service then fetches the drawing engine (Chromium) once. The panel says "Setting up the AI tools (first start only, a few minutes)…" and waits up to 5 minutes.
  - **The connector** that Claude starts uses the same uv, flags and environment: `/api/connect` and the one-click buttons pass `UV_PROJECT_ENVIRONMENT`.
  - **Cross-platform fixes:**
    - The bridge's connection file is in `~/.local/share/91studio/bridge` on Mac and Windows, on both sides, because Claude starts the connector with a minimal environment.
    - Client fonts go to `~/Library/Fonts/91studio` on Mac, and on Windows to the per-user Fonts folder, registered under HKCU.
  - **Updates:** upstream's updater (craft-update) is pointed at the releases repo: name "91Ninjas Studio", the 91 icon, the mint accent.
    - Only release builds (`STUDIO_RELEASES_REPO` set at build time) check, at start and with Help › Check for Updates.
    - The prompt offers **Update and restart / Download / Later**, so it asks first. The app downloads and installs the update itself, and updates show no system warning.
    - Not built: "Install when I close the app".
- **Checked:**
  - Live on Linux: an installed layout (the app with `agents/` and `uv` beside it) found them, built its environment in the data folder, became ready, and gave Claude Desktop a config with the bundled uv and environment.
  - Tests: the connect commands for an installed app.
  - **Not yet run:** the Windows and Mac builds themselves. They run on GitHub, and the first tag push is their test.

## D-007: Every phase ends with a phase report and a docs update (2026-11-02)
- **Decision:**
  - At the end of each phase, write `phases/PHASE-N.md` and update the PRD, TRD, ARCHITECTURE and SECURITY docs.
  - Log each new decision here.

## D-041: Manager password for team clients (2026-11-04)
- **Context:** manager status was the name typed when joining, so anyone could type "Yashodhan" and get **Reject for everyone**.
- **Decision:** an optional **Manager password** field replaces the "I'm the manager" checkbox.
  - The first manager to join sets the password. For a new repo that creates `team.json`; for a team from before passwords, it adds the password.
  - `team.json` stores only a salted PBKDF2-SHA256 hash (200,000 iterations); the laptop keeps the hash it proved.
  - A manager is a laptop whose stored hash matches the repo's. A changed password ends old manager logins.
  - A wrong password is refused. No password means a designer, whatever the name.
- **Alternatives:** per-device signing keys (stronger against someone editing the repo with the team key). Not needed for a private repo and an internal team.
- **Consequences:** existing managers leave and join again once with the password. The name is still only a label on shares.
