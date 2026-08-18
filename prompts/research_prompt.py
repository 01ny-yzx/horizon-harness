"""Prompt rules for research tasks."""

from __future__ import annotations


def build_research_prompt() -> str:
    """Return research workflow prompt rules."""

    return """
Research 任务规则：
1. 根据用户请求和当前 Tool Surface 自主决定是否调用 web_search、fetch_url 或 Browser Tool。
2. URL 和搜索 Query 都是工具参数，不是 Runtime 路由规则。
3. 工具成功或失败后，基于真实 ToolObservation 决定继续调用工具还是回答。
4. 不要声称执行了没有成功完成的工具调用。
5. 用户对资料范围、时效和呈现方式的要求保留在原始对话中，由模型理解和执行。

Browser Tools 补充规则：
1. 当 Browser capability 位于当前 Tool Surface 时，可根据任务和已有 Observation 使用 rendered-page、click、link extraction、screenshot 等能力。
2. Browser 输出必须标明 URL；不要声称读取了网页，除非工具成功返回内容。
""".strip()
