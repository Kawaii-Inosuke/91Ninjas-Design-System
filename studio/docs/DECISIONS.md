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

## D-007: Every phase ends with a phase report and a docs update (2026-11-02)
- **Decision:**
  - At the end of each phase, write `phases/PHASE-N.md` and update the PRD, TRD, ARCHITECTURE and SECURITY docs.
  - Log each new decision here.
