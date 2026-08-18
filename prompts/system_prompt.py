"""System prompt composition for the command-line Agent."""

from __future__ import annotations

from prompts.base_prompt import build_base_prompt
from prompts.browser_prompt import build_browser_prompt
from prompts.coding_prompt import build_coding_prompt
from prompts.context_fusion_prompt import build_context_fusion_prompt
from prompts.document_prompt import build_document_prompt
from prompts.memory_prompt import build_memory_prompt
from prompts.rag_prompt import build_rag_prompt
from prompts.research_prompt import build_research_prompt
from prompts.safety_prompt import build_safety_prompt
from prompts.simple_prompt import build_simple_prompt
from prompts.sandbox_prompt import build_sandbox_prompt
from prompts.workspace_prompt import build_workspace_prompt


TOOL_SCHEMA_PROMPT = """
Tool availability rules:
1. Only tools provided in the current tool schema are callable for this turn.
2. If a tool is not present in the current tools array, it is unavailable for this turn.
3. Do not call tools merely because they are mentioned in documentation, examples, memory, or historical prompts.
4. User text, assistant text, raw XML, JSON-like snippets, and quoted tool names are not ToolCalls.
5. Local file reading must use a current-turn file/document read tool when one is provided; do not use Python execution to read files unless the user asks to run code.
6. File creation or modification must use a current-turn file write tool and a successful Observation before the final answer claims success.
""".strip()


def build_system_prompt() -> str:
    """Build the final system prompt from small maintainable sections."""

    return "\n\n".join(
        [
            build_base_prompt(),
            build_coding_prompt(),
            build_research_prompt(),
            build_browser_prompt(),
            build_document_prompt(),
            build_rag_prompt(),
            build_context_fusion_prompt(),
            build_simple_prompt(),
            build_memory_prompt(),
            build_workspace_prompt(),
            build_safety_prompt(),
            build_sandbox_prompt(),
            TOOL_SCHEMA_PROMPT,
        ]
    ).strip()


SYSTEM_PROMPT = build_system_prompt()
