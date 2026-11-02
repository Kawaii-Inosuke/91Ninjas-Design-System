# AI models

*Last updated: 2026-11-02 (Phase 0). Decision: D-003. Exact model IDs are pinned in Phase 3.*

## Model map (draft)
| Agent | Provider | Tier | Needs vision? | Why |
|---|---|---|---|---|
| Critic | Gemini | Pro | **yes** (preview PNG) | Strongest judgement on visual rule checks |
| Layout Designer | Gemini | Pro | no | Long brand context and structured `spec.json` output |
| Brief Writer | Gemini | Flash | sometimes (image input) | Cheap, fast and good with structured output |
| Art Director (chat) | Gemini | Flash | no | Low latency in the panel |
| Canvas Editor | Gemini | Flash | optional | Small, precise edit plans |
| Brand Onboarder: tagging past work | Groq | an open model, e.g. Llama or Qwen | no | Very fast and cheap for bulk text |
| Similar-design search | local | `all-MiniLM-L6-v2` | no | Already in the skill, free and offline |

All of this is configured in `agents/models.yaml`. Code never hard-codes a model ID.

## How each provider plugs into Strands
- **Gemini:** Strands' built-in **Google** model provider. Confirm the class name, import path and pip extra from the Strands docs when Phase 3 starts.
- **Groq:** no native Strands provider. It works through Groq's OpenAI-compatible endpoint:
  ```python
  from strands.models.openai import OpenAIModel
  OpenAIModel(client_args={"api_key": GROQ_API_KEY, "base_url": "https://api.groq.com/openai/v1"},
              model_id="<groq model id>")
  ```
  It can also go through `LiteLLMModel` with a `groq/<model>` ID. Check this against the docs before relying on it.
- **Claude (later, config only):** `strands.models.anthropic.AnthropicModel` (Anthropic API) or `BedrockModel`.

## Keys
- `GEMINI_API_KEY` and `GROQ_API_KEY` go in `studio/agents/.env`. That file is gitignored and never pasted into chat.
- Phase 5 adds a company gateway so keys are no longer stored on designer machines.

## Using Claude without an API key
See D-006. A custom app can't use a designer's claude.ai subscription limits; as understood, that is allowed only in Anthropic's own apps. The allowed route is **Mode B**: designers run Claude Code and point it at our bridge exposed as an MCP server.

## Risks
- **Prompt quality.** The skill was developed on Claude, so the Gemini output must be compared against the skill's results on the same FCC content (Phase 3 eval).
- **Rate limits.** Groq free and dev tiers have tight per-minute limits. Throttle bulk onboarding jobs.
- **Cost.** Log tokens per run and per agent (Strands `AgentResult.metrics`) and report cost per design.
