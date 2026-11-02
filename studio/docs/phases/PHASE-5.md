# Phase 5: ready for designers

*Status: **in progress** (2026-11-02).*

| Part | What | Status |
|---|---|---|
| 5a | Rebrand (D-022, then D-023 with the user's choices): **91Ninjas Studio**, the 91 mark as the app icon, the 91NINJAS wordmark on the welcome screen, an **AI** button in the app bar (instead of Discord), and the panel named **AI Design Assistant**. 2,882 ui-egui tests pass; 331 assets attributed; live screenshot checked. | ✅ done |
| 5a+ | **Chat panel** (D-024): client chip and menu (import pack, add from folder), message thread with previews and approval cards, suggestion chips, one message box. **FCC installed locally.** Layout-name matching fixed. Checked: 2,884 ui-egui tests, 55 Python tests (+5 live), live screenshots: send with Enter; the reply explains the missing key. | ✅ done |
| 5a++ | **91Ninjas look** (D-025): teal-black theme, mint accent (default), wordmark in the app bar, redesigned welcome screen with a Design with AI card. 2,884 ui-egui and 71 engine prefs tests pass; checked live. | ✅ done |
| 5a+++ | **AI panel after the user's mockup** (D-026): Chat / Clients / History tabs, a hero with action rows, a composer with image attach, history with Open buttons. | ✅ done |
| 5a* | **Keys working, rotation and fallbacks** (D-027). The first complete real FCC design was made on the user's keys. | ✅ done |
| 5a** | **Editor layout like the mockup** (D-028): 91Ninjas workspace (Control bar, three open panel columns, AI panel on the right), Share and Export at the top right, mint document tab, AI button in the Control bar, the AI panel waits for the agents to start. 2,885 ui-egui tests and clippy pass; checked with live screenshots. | ✅ done |
| 5b | Usage and cost tracking per run and per agent | ⏭ dropped (D-029): designers will use their own Claude plans |
| 5c | **Variants** (D-030): 2–3 versions with different layouts, side by side on their own artboards; Keep A/B/C; the others leave in one undo step. 74 Python and 2,886 ui-egui tests pass; checked live in the editor. | ✅ done |
| 5d | **Brand Onboarder** (D-031): Clients → New client from past work: measure the past designs (exact colours, radii, sizes), the AI drafts DESIGN.md and the rulebook in fixed slots, the designer reviews/edits and saves. 8 new tests; 2,887 ui-egui and 72 Python tests pass. Live AI draft still to try (Gemini quota). | ✅ done |
| 6 (moved up) | **Claude connector** (D-032, D-033): see PHASE-6.md. Gemini and Groq removed; the panel became Claude's companion. | ✅ done |
| 5e | Installer: release build plus the bundled agents environment (Linux first). Needs about 10 GB free for a release build. | |
