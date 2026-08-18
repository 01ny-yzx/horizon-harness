"""Focused closure smoke for the remaining runtime text-rule cleanup."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.memory as memory_module
from prompts.browser_prompt import build_browser_prompt
from prompts.research_prompt import build_research_prompt


def test_neutral_browser_and_research_prompts() -> None:
    browser = build_browser_prompt().lower()
    research = build_research_prompt().lower()
    assert "browser fallback" not in browser
    assert "research fallback" not in browser
    assert "fetch_url" not in browser
    assert "browser fallback" not in research
    assert "rendered-page" in research
    assert "scoped schemas" in browser


def test_file_output_text_heuristics_are_removed() -> None:
    for name in (
        "_task_ids_with_transient_messages",
        "_looks_like_file_output_message",
        "_looks_like_file_output_request",
    ):
        assert not hasattr(memory_module.Memory, name), name
        assert not hasattr(memory_module, name), name


def test_intent_routing_private_url_text_rules_are_removed() -> None:
    for relative_path in (
        "core/intent_policy.py",
        "core/capability_routing.py",
        "core/intent_failure_recovery.py",
    ):
        assert not (ROOT / relative_path).exists(), relative_path


def test_source_authority_text_rules_are_removed() -> None:
    markers = (
        "source_grounded_only",
        "needs_official_docs",
        "source_grounded_required",
        "source_enhanced_but_degradable",
        "prefer_official_docs",
    )
    for root_name in ("core", "tools", "workflows"):
        for path in (ROOT / root_name).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in markers:
                assert marker not in source, (str(path.relative_to(ROOT)), marker)


def main() -> None:
    test_neutral_browser_and_research_prompts()
    test_file_output_text_heuristics_are_removed()
    test_intent_routing_private_url_text_rules_are_removed()
    test_source_authority_text_rules_are_removed()
    print("Text rule cleanup closure smoke passed.")


if __name__ == "__main__":
    main()
