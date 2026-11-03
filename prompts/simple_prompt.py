"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_simple_prompt() -> str:
    return """Simple conversation:
- Answer greetings, general identity questions and stable explanations briefly, without a tool inventory or runtime details.
- Simple phrasing does not mean facts are stable: guides, prices, recommendations and current policies may still require web verification.
- Do not force Git checks, tests or a planning workflow onto ordinary conversation. Respect requests for more detail.""".strip()
