"""Prompt rules for simple tasks."""

from __future__ import annotations


def build_simple_prompt() -> str:
    """Return simple task prompt rules."""

    return """
Simple 任务规则：
1. 简洁回答用户问题。
2. 不强制 Git/Web/验证。
3. 如果用户只是问候、简单解释、查看当前目录或普通问题，只调用必要工具。
4. 不要把简单任务扩大成 research 或 coding 流程。
""".strip()
