"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_research_prompt() -> str:
    return """Research work:
- Research real-world guides, recommendations and changing facts with currently available search/read tools even without an explicit request to browse.
- Discover sources with web_search; read relevant known pages with fetch_url or browser tools. Check recency and prefer authoritative sources.
- Follow offline/document-only constraints. If search is unavailable or fails, explain the limit rather than silently replacing evidence with model knowledge.
- Cite supporting URLs and distinguish findings from inference; do not cite unread pages or invent source details.
- Research can be part of a larger task. The task label must not prevent needed reading, code work or user-requested outputs.""".strip()
