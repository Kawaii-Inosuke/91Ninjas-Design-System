# Phase 6: Claude connector (moved up; D-029, D-032, D-033)

*Date: 2026-11-03.*

## Why
- **The chat panel lost the thread.** "Another one with icons" was taken as new content and refused, and "add it back" after Ctrl+Z was refused.
- **A design seemed to replace an earlier one.** That was a separate bug: the AI's steps went to whatever tab was in front, so switching tabs during a design redirected them. One approval exported an empty Untitled-1.
- **The user's decision:** work only with Claude, on each designer's own Claude plan, and remove Gemini and Groq.

## What was built
| Part | What |
|---|---|
| Documents by uid (D-032) | Editor `studio.documents` / `studio.newDocument` / `studio.inDocument`; the bridge's `document` field; Python `Bridge.in_document`. A design's steps land in its own document; the designer's tab stays in front. |
| Connector (D-033) | `studio_agents/mcp_server.py`: 15 MCP tools (brand, similar check, render with preview image, place own document / beside, remove, look at the editor, edit, approve, list, new clients), plus Claude's instructions (`studio_agents/claude/`). |
| Designs (D-033) | `designs.py`: versions and placements per design on disk; "is it still there?" after the designer's undo; one undo step per edit or removal. |
| Panel (D-033) | Tabs **Claude** (status, connect with Copy buttons, live activity), **Clients** (packs, folders, new client request for Claude), **History**. |
| Removed (D-033) | Gemini, Groq, Strands, `.env` keys, the run pipeline, the agents' prompts, the onboarder's AI draft step, the test web page. |

## How the original problems are solved
- **Follow-ups:** Claude keeps the conversation, and its instructions say what follow-ups mean: "another one with icons" is a new version of the same content; "add it back" means `look_at_editor`, then `place_design`, or redo.
- **New work never replaces old work:** a new piece of content is a new design in its own document. Every call names its document.
- **Approval:** files are exported from the design's own document, so they're never an empty Untitled-1 again.

## Checked
- Python: 55 tests (13 new), including the connector's tools returning images, and a fake editor that models documents, undo and closed tabs.
- Rust: 2,885 ui-egui tests, 12 bridge tests (1 new), a new control test; clippy.
- Live: the connector started over stdio (as Claude starts it) against the real editor. It rendered, placed in its own document, placed a version beside it, saw both, removed one version in one step, and changed the other's title (screenshot checked).

## Next
- The user connects Claude Code (`claude mcp add …` from the panel) and tries real content: the first real-world test of Claude's designs.
- Tune `claude/workflow.md` and `spec.md` from those conversations.
- 5e installer (needs ~10 GB free); the update flow (ask before updating) is planned there.
