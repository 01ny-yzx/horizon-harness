# Agent prompt design references

The Horizon policy is original wording adapted to Horizon's actual tools and permissions. The following official repository sources were inspected on 2026-11-02; they are design references, not copied product identities or claims of feature parity.

## OpenCode

Pinned revision: `67139d6827af3a337c80472c33f9cb7c6c2ce500`.

- [System prompt assembly](https://github.com/anomalyco/opencode/blob/67139d6827af3a337c80472c33f9cb7c6c2ce500/packages/core/src/session/system-prompt.ts): stable base instructions plus guidance conditional on available tools.
- [Base instructions](https://github.com/anomalyco/opencode/blob/67139d6827af3a337c80472c33f9cb7c6c2ce500/packages/core/src/session/runner/prompt/system.txt): concise communication and respect for existing user changes.
- [Web search tool](https://github.com/anomalyco/opencode/blob/67139d6827af3a337c80472c33f9cb7c6c2ce500/packages/core/src/tool/plugin/websearch.ts): describe retrieval in the tool contract, including current information.

## Hermes Agent

Pinned revision: `fbf6c6c91d8a9ce3008e05c4b51ca60ad05bb85f`.

- [Prompt builder](https://github.com/NousResearch/hermes-agent/blob/fbf6c6c91d8a9ce3008e05c4b51ca60ad05bb85f/agent/prompt_builder.py): match response depth to the request, report useful outcomes, ground current facts in tools, reconsider unhelpfully narrow retrieval results.
- [System prompt layering](https://github.com/NousResearch/hermes-agent/blob/fbf6c6c91d8a9ce3008e05c4b51ca60ad05bb85f/agent/system_prompt.py): separate stable policy from runtime context and condition guidance on capabilities.
- [Web tools](https://github.com/NousResearch/hermes-agent/blob/fbf6c6c91d8a9ce3008e05c4b51ca60ad05bb85f/tools/web_tools.py): distinguish source discovery/snippets from actual page extraction.

Horizon does not inherit these products' identities, tool names, skills, model assumptions or unavailable capabilities. Its runtime modes and deterministic permission checks remain authoritative.

## Claude Code boundary

The public `anthropics/claude-code` repository and official output-style documentation were also checked. The repository's `CLAUDE.md` is repository guidance; it was not treated as the complete runtime system prompt. No third-party prompt dump was used as an official source.

## Horizon implementation and validation

`prompts/agent_prompt.py` supplies shared identity, communication, research, evidence and authorization rules. `core/prompt_pack.py` composes these into execution and final-answer stages; tool-disabled stages retain their existing constraints. The web-search tool description and per-request tool guidance reflect actual available schemas. This does not add a keyword-based routing layer.

`/search` configures the existing Tavily integration using private local storage. Configuration tests cover persistence, authenticated API access, masked input, damaged-file recovery and credential protection. A local fake-provider integration exercises search → page fetch → final answer through the real Agent loop.

These tests establish prompt assembly and execution wiring. They do not establish real-model adherence or validate a live search credential. No paid model or search requests are needed for this suite.

## Launch-directory handling (2026-11-03)

OpenCode’s [default CLI handler](https://github.com/anomalyco/opencode/blob/67139d6827af3a337c80472c33f9cb7c6c2ce500/packages/cli/src/commands/handlers/default.ts#L19-L21) switches cwd only when an explicit directory is requested. Horizon follows the same default launch-cwd / explicit-directory behavior. Its threaded local API additionally uses a ContextVar during Session execution so opening an older Session cannot change another Session’s working directory. Application source imports and private data locations remain separate.

## Sparse contextual emoji (2026-11-03)

Inspected the actual OpenCode base system prompt at pinned commit
`3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3`:
https://github.com/anomalyco/opencode/blob/3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3/packages/core/src/session/runner/prompt/system.txt

It specifies GitHub-flavored Markdown and clear, concise communication without unnecessary jargon. This pinned base prompt has no explicit emoji directive. Horizon's optional, sparse contextual emoji rule is the user's requested adaptation, not a claim that OpenCode mandates emoji. The shared policy applies to initial, continuation, simple and final responder packs; code/commands/data remain untouched. Offline policy tests verify composition, not a real model's stylistic compliance.
