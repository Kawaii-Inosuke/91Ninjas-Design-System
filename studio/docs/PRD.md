# PRD: 91 Studio (AI Design Studio)

*Status: draft v0.1 · Owner: Shrikant · Last updated: 2026-11-02*

## How the AI works (D-033)
Designers work with **Claude** (Claude Code or Claude Desktop, on their own Claude plan), connected to the app by the 91Ninjas Studio connector. They talk to Claude about the design; Claude builds it on the artboard in the client's brand, and they finish it by hand or by asking. The app has no AI keys of its own.

## Product principle (D-012)
**The AI is the product.** The app exists so that AI agents can create on-brand designs for designers. **The editor is the workbench**: it gives designers the Illustrator feel and tools to refine what the AI made. When priorities conflict, AI output quality wins.

## Problem
91Ninjas designers make client infographics with the `infographic` Claude Code skill (`Design skill/`). The skill already handles the hard parts: brand packs, a duplicate-topic registry, a critic loop and approval gates. 91 Studio is a **separate product** that learns from the skill's approach because it gives good output (D-010). Two problems remain with the skill:
- **The output is hard to edit.** It is a PNG, a PDF and a *flattened* SVG. Small fixes mean redrawing the design or wrestling with it in Illustrator or Figma.
- **The AI and the design tool are separate.** Work moves terminal → files → Illustrator, so it's slow, and nothing the designer changes flows back into the record.

## Users
- **Designer (primary).** Makes on-brand assets for 91Ninjas clients and wants a good first draft fast, then full manual control.
- **Every designer** can create clients locally and share them as pack files. There is no admin role in the app (D-009).

## Goal
One desktop app that is an Illustrator-class editor, with **Claude** as the designer's AI partner (D-033). In a
conversation with Claude, *client + content* becomes an on-brand design on the artboard. Every element (title, cards,
icons, text) is a separate, editable layer.

## Core flows (v1)
1. **New design (in Claude, with the app open).**
   - The designer pastes content into Claude and names the client (or Claude asks).
   - Claude writes a brief and checks for similar designs. If one exists, the designer sees it and **where its files are**, and decides whether to make a new one. Nothing is ever blocked (D-020).
   - Claude proposes an outline (title, layout, items). **The designer approves or asks for changes.**
   - Claude renders the design, checks the preview against the client's rules (at most 3 rounds), and places it in **a new document of its own** (D-032).
2. **Versions.** "Give me 3 versions": different layouts side by side on artboards. The designer says which to keep; the others leave in one undo step (D-030).
3. **Hand edits.** The designer edits with normal tools (pen, type, colours, transform). Nothing is locked.
4. **Edits by asking.** "Make the title 20% bigger", "another one with icons", "add it back". Claude looks at the artboard first (it sees the designer's edits, undo and deletions), then changes only what was meant; **each change is one undo step**.
5. **Approval.** When the designer says it's approved, the design is exported (SVG/PNG/PDF) from its own document, edits included, and recorded in the client's history (the panel's History tab).
6. **New client (local).** Either a brand folder (DESIGN.md, critic_rules.md, past work, fonts), or **from past work with Claude**: Claude measures the client's past designs, drafts the design language and rules, shows them, and saves the client after the designer's OK (D-031, D-033). No approval step (D-009).
7. **Share a client.** **Export** a client as one `.91pack` file (with a few sample past infographics) and send it over Slack or Drive. The receiver imports it in the panel's Clients tab: validated, previewed, installed; replace or keep both if it exists.

## Requirements
- **Brand fidelity.** Use only the brand pack's colors, fonts and rules, and run the existing `auto_checks` plus the critic.
- **Know what exists.** A local similar-design check (the skill's threshold, similarity ≥ 0.65) shows existing designs with their file locations. The designer always decides; nothing is blocked (D-020).
- **Designer stays in control.** Nothing is logged or exported without approval, and the agents never overwrite manual edits silently.
- **AI: Claude only** (D-033), through the designer's own Claude Code or Claude Desktop and plan. No AI keys in the app; nothing is charged to 91Ninjas per design.
- **Runs locally.** The editor and the connector run on the designer's machine. All data is local. The only network traffic is Claude's own app talking to Anthropic (D-009, D-010, D-033).

## Out of scope (v1)
- Web or browser version (VectorCraft has a WASM build, which may come later)
- Multi-user real-time collaboration
- A central shared client library or an approval workflow (D-009)
- Image generation (raster illustrations)
- Animated output
- Auto-publishing
- Asset types other than infographics (social posts and decks come later)

## Success metrics
- Time from content to approved infographic at least 50% lower than with the skill-only workflow
- At least 80% of AI drafts reach final approval with hand edits only (no full redraw)
- Zero brand-palette or font violations in logged finals
- Designers are never surprised by an existing design: every similar one is shown before generating

## Open questions
- Which Claude plan designers need for a day of work (Pro vs Max), measured once designers use it.
- Do designers need a fallback to the skill-only workflow during rollout? (Assumed yes.)
