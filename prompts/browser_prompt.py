"""Focused guidance for legacy callers; shared agent policy governs active packs."""

def build_browser_prompt() -> str:
    return """Browser work:
- Use current browser tools for rendered pages, links, screenshots or interactions when appropriate. A browser is not required for every ordinary URL read.
- Follow URL and network boundaries enforced by the runtime, including redirect checks.
- A page is read only when successful returned content supports that claim. Cite the actual URL, and do not turn a title or excerpt into full-page evidence.
- Protect cookies, tokens, credentials and private browser state. Do not handle payments, OTPs or sensitive login steps without applicable authorization and support.
- Respect user's authorization for submissions and side effects; explain unavailable/failed capabilities plainly without raw tracebacks or page dumps.""".strip()
