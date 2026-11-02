# AI models

*Last updated: 2026-11-03. Decisions: D-029, D-033 (D-003 and D-027 are history).*

## Claude, through the designer's own Claude plan
91Ninjas Studio has **no AI model and no API keys of its own**. Designers use **Claude Code** (Linux, Mac,
Windows) or **Claude Desktop** (Mac, Windows) with their own Claude Pro or Max plan. Claude connects to the app
through the **91Ninjas Studio connector**, an MCP server on the designer's computer (`studio_agents/mcp_server.py`).
Claude holds the conversation and does the thinking: the brief, the outline, the layout, checking the render
against the client's rules, and the edits. The connector's tools do the rest without any model:

| Work | Done by |
|---|---|
| Brief, outline, layout, copy, critique, edits, new clients' DESIGN.md and rules | Claude (in its own conversation) |
| Brand packs, the spec guide, the rules | the connector (`get_brand`) |
| The similar-design check | local embeddings (`all-MiniLM-L6-v2`, offline) |
| Rendering in the brand, machine checks, layered SVG | local Chromium (Playwright) |
| Placing, editing, approving in the editor | the Studio Bridge (D-008, D-032) |
| Measuring past work for a new client | local image analysis (`onboard.measure`) |

**Cost to 91Ninjas per design:** none. Usage counts against each designer's Claude plan limits.

**Allowed use:** our app never signs in with a claude.ai account. Claude's own apps use the connector as a local
tool, which is how MCP servers are meant to be used.

## Connecting Claude
The AI panel's **Claude** tab shows the exact command and config, with Copy buttons. For Claude Code:
```bash
claude mcp add --scope user 91ninjas-studio -- uv --directory "<repo>/studio/agents" run python -m studio_agents.mcp_server
```
For Claude Desktop: add the `mcpServers` entry the panel shows (Settings › Developer › Edit Config) and restart Claude.

## History
- Phases 3–5 used Gemini and Groq through Strands Agents (D-003, D-027), with key rotation and fallbacks.
- They were removed in D-033: the panel's fixed pipeline couldn't follow a conversation, and the free keys ran out daily.
