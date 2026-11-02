# PRD: 91 Studio (AI Design Studio)

*Status: draft v0.1 · Owner: Shrikant · Last updated: 2026-11-02*

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
One desktop app that is an Illustrator-class editor with an AI side panel. In that panel a team of agents turns *client + content* into an on-brand design on the artboard. Every element (title, cards, icons, text) is a separate, editable layer.

## Core flows (v1)
1. **New design.**
   - The designer picks a client, pastes text or drops an image, and adds an optional instruction.
   - Agents write a brief and run the duplicate check. A warn result is shown; a block result stops the run.
   - Agents propose an outline. **Approval gate 1: approve, request changes, or abandon.**
   - Agents render the design, critique it (at most 3 rounds), and place it on a new artboard.
2. **Hand edits.** The designer edits with normal tools (pen, type, colors, transform). Nothing is locked.
3. **Chat edits.** For example "make the title 20% bigger" or "swap icon 3 for a shield". The agents change only the named layers, and **each AI turn is one undo step**.
4. **Re-critique.** The designer asks the critic to check the *current* artboard, including their own edits, against the brand rules.
5. **Final approval (gate 2).** The design is exported (SVG/PNG/PDF) from the editor and recorded in the local history, so the designer-edited version is the one recorded.
6. **New client (local).** A **New client** wizard asks for:
   - a few past infographics (required)
   - a design-language file (`DESIGN.md`)
   - a rulebook (`critic_rules.md`)
   - fonts, logo and icons
   - an optional guideline PDF

   If the design language or rulebook is missing, the Brand Onboarder agent drafts it from the past work, and the designer reviews and edits it. The client is usable immediately on that machine. There is no approval step (D-009).
7. **Share a client.** **Export** a client as one `.91pack` file, which includes a few sample past infographics. Send it over Slack or Drive. The receiver clicks **Import** in their app, which validates the pack, previews it, and installs it. If the client already exists, the receiver chooses to replace it or keep both.

## Requirements
- **Brand fidelity.** Use only the brand pack's colors, fonts and rules, and run the existing `auto_checks` plus the critic.
- **No repeats.** A local duplicate-topic check using the same thresholds that work in the skill (block ≥ 0.80, warn ≥ 0.65, phash ≤ 8).
- **Designer stays in control.** Nothing is logged or exported without approval, and the agents never overwrite manual edits silently.
- **Swappable AI.** Gemini and Groq at launch. Claude (API or Bedrock) can be added by config.
- **Runs locally.** The editor and agents run on the designer's machine. All data is local. The only network traffic is to the AI providers (D-009, D-010).

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
- Zero duplicate-topic finals logged

## Open questions
- Exact Gemini and Groq model IDs and per-design cost (decided in Phase 3, recorded in `AI-MODELS.md`).
- Do designers need a fallback to the skill-only workflow during rollout? (Assumed yes.)
