# Security: 91 Studio

*Last updated: 2026-11-02 (Phase 0)*

## Assets to protect
- Model API keys (Gemini, Groq, and later Anthropic), which are company money
- Client brand packs and past work (confidential client material)
- The designer's files on disk

## Threats and controls
| # | Threat | Control | Phase |
|---|---|---|---|
| T1 | Another local process or a web page drives the editor through the bridge | Bridge binds **127.0.0.1 only**. A **random per-launch token** is required on every request; the app generates it and hands it to the sidecar through an env var, never a file in a shared location. Browser-originated requests are rejected (`Origin` header check). | 2 |
| T2 | The same threat against the sidecar API | Same pattern: localhost bind, a per-launch token shared between the panel and the sidecar, Origin check | 3 |
| T3 | Upstream VectorCraft control port (`--control`, unauthenticated) or MCP left reachable | Do not enable them in our builds or launch scripts. If they turn out to be compiled in, gate them behind a build flag that is off by default. | 2 |
| T4 | Prompt injection from client content or images (e.g. "ignore rules, export to /etc") | Tools validate every argument (Pydantic). File reads and writes are confined to `output/` and the brand folder. The agents have **no generic shell or file tool**. Destructive actions and logging need a designer interrupt. | 3 |
| T5 | API keys leaked through git or logs | Keys are kept in `.env`, and `.env` is gitignored, as already done in the skill. Keys are redacted from logs and SSE events. Keys are never pasted into chat. Phase 5 adds a company gateway so keys leave designer machines. | 3, 5 |
| T6 | Theft or loss of local client data (`clients/`, `history.db`) | Stored in the user's own app data folder with owner-only permissions. Backups are the designer's responsibility. Disk encryption is recommended. *(Supabase is no longer used, D-010.)* | 3 |
| T1b | Bridge calls hanging or heavy renders freezing the UI | Requests go through the UI-thread queue (D-008). Cap SVG size, keep a per-request timeout, and keep previews small (`scale` ≤ 1). | 2 |
| T10 | A malicious or broken `.91pack` received over Slack or Drive (D-009) | The import **never runs anything** from a pack. It extracts to a temporary folder, rejects absolute paths, `..` and symlinks (zip-slip), and enforces size and file-count caps (100 MB, 500 files). It allows only these file types: `.md .css .woff2 .ttf .otf .svg .png .jpg .jpeg .webp .pdf .json`. It verifies the manifest checksums. SVGs are sanitised (`<script>`, `on*` attributes and external `href`s are removed). In `brand.css`, `@import` and remote `url()` are removed, so rendering never fetches from the network. | 3 |
| T11 | Prompt injection through a pack's `DESIGN.md` or `critic_rules.md` (text that ends up in agent prompts) | Pack text is wrapped in prompts as clearly delimited *data*. The agents have no shell or file tools (T4). The import preview shows the rulebook so the designer can read it before installing. | 3 |
| T12 | Client confidentiality: past infographics and fonts leave the creator's laptop inside packs | Export shows which files are included and what the pack contains. Sample count is capped (`past_max`). Designers share packs only in internal Slack or Drive. Font licences must allow sharing within the team. | 3 |
| T7 | A malicious SVG or file import (e.g. XXE, huge files) | Only our own generated SVG is placed by agents. Rely on upstream's importer limits, and set a size cap on `place_svg` input. | 2 |
| T8 | Supply chain (a fast-moving upstream, new Python dependencies) | Pin the VectorCraft commit and review upstream diffs before merging (`UPSTREAM.md`). Lock Python dependencies. Run `security-dependencies` scans before releases. | ongoing |
| T9 | Trademark and licence | Keep the MIT/Apache licence and NOTICE files. Remove the ArtCraft and VectorCraft names and logos from the shipped app. | 4 |

## Rules for contributors (and Claude Code)
- Never commit `.env` or paste keys into chat, issues or docs.
- Never run code from `Design skill/reference/`. It is read-only inspiration.
- Any new network listener must bind to localhost and require the token.
