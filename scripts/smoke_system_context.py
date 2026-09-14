"""Focused S6 smoke for structured System Context reconciliation."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.system_context import (
    DuplicateContextKeyError,
    ReplacementBlocked,
    ReplacementReady,
    SystemContext,
    SystemContextInitializationBlocked,
    SystemContextSnapshot,
    SystemContextSource,
    UNAVAILABLE,
    Unchanged,
    Updated,
)
from core.system_context_registry import SystemContextRegistry, SystemContextRegistryEntry
from core.horizon_system_context import (
    _decode_date_value,
    _decode_environment_value,
    _decode_instruction_value,
    _decode_memory_reference_guidance_value,
    _decode_persistent_instruction_value,
    build_horizon_system_context_registry,
)
from core.session import SessionInfo


def source(key, state, *, removal=True, decode=lambda value: value):
    return SystemContextSource(
        key=key,
        load=lambda: state[0],
        baseline=lambda value: f"baseline:{key}:{value}",
        update=lambda old, new: f"update:{key}:{old}->{new}",
        removed=(lambda value: f"removed:{key}:{value}") if removal else None,
        decode=decode,
    )


def rejected(decoder, value) -> None:
    try:
        decoder(value)
    except (TypeError, ValueError):
        return
    raise AssertionError(f"decoder accepted invalid value: {value!r}")


def test_production_decoders() -> None:
    environment = {
        "directory": "/project",
        "workspace_id": "u/p",
        "project_id": "p",
        "is_git_repo": True,
        "platform": "Darwin",
    }
    decoded_environment = _decode_environment_value(environment)
    assert decoded_environment == environment and decoded_environment is not environment
    rejected(_decode_environment_value, {**environment, "directory": 123})
    rejected(_decode_environment_value, {**environment, "is_git_repo": "true"})

    assert _decode_date_value("2026-09-14") == "2026-09-14"
    rejected(_decode_date_value, 20260914)
    rejected(_decode_date_value, "not-a-date")

    instructions = [{"path": "/project/HORIZON.md", "content": ""}]
    decoded_instructions = _decode_instruction_value(instructions)
    assert decoded_instructions == instructions and decoded_instructions is not instructions
    for invalid in (
        "old text",
        [123],
        [{"path": 123, "content": "abc"}],
        [{"path": "/tmp/HORIZON.md", "content": 123}],
        [{"content": "abc"}],
        [{"path": "relative/HORIZON.md", "content": "abc"}],
    ):
        rejected(_decode_instruction_value, invalid)

    assert _decode_persistent_instruction_value({"text": "keep"}) == {"text": "keep"}
    rejected(_decode_persistent_instruction_value, {"text": 1})
    rejected(_decode_persistent_instruction_value, {})
    rejected(_decode_persistent_instruction_value, "text string")
    rejected(_decode_persistent_instruction_value, [])

    reference = {
        "available_types": ["stable_fact"],
        "counts": {"stable_fact": 1},
        "text": "guidance",
    }
    decoded_reference = _decode_memory_reference_guidance_value(reference)
    assert decoded_reference == reference and decoded_reference is not reference
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "available_types": ["stable_fact", 1]},
    )
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "available_types": "stable_fact"},
    )
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "counts": []},
    )
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "counts": {"stable_fact": "1"}},
    )
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "counts": {"stable_fact": True}},
    )
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "counts": {"stable_fact": -1}},
    )
    rejected(
        _decode_memory_reference_guidance_value,
        {**reference, "text": 1},
    )


def main() -> None:
    test_production_decoders()
    a = ["A"]
    b = ["B"]
    context = SystemContext((source("test/a", a), source("test/z", b)))
    generation = context.initialize()
    assert generation.baseline == "baseline:test/a:A\n\nbaseline:test/z:B"
    assert list(generation.snapshot.sources) == ["test/a", "test/z"]
    assert isinstance(context.reconcile(generation.snapshot), Unchanged)

    a[0] = "A2"
    changed = context.reconcile(generation.snapshot)
    assert isinstance(changed, Updated) and "A->A2" in changed.text

    new = ["N"]
    added = SystemContext((*context.sources, source("test/new", new))).reconcile(changed.snapshot)
    assert isinstance(added, Updated) and "baseline:test/new:N" in added.text
    removed = SystemContext((source("test/a", a), source("test/new", new))).reconcile(added.snapshot)
    assert isinstance(removed, Updated) and "removed:test/z:B" in removed.text

    a[0] = UNAVAILABLE
    preserved = SystemContext((source("test/a", a), source("test/new", new))).reconcile(removed.snapshot)
    assert isinstance(preserved, Unchanged)
    assert preserved.snapshot.sources["test/a"] == removed.snapshot.sources["test/a"]

    try:
        SystemContext((source("test/a", [UNAVAILABLE]),)).initialize()
    except SystemContextInitializationBlocked:
        pass
    else:
        raise AssertionError("initial unavailable source did not block")

    incompatible = SystemContext(
        (source("test/a", ["ok"], decode=lambda _value: (_ for _ in ()).throw(ValueError())),)
    ).reconcile(SystemContextSnapshot({"test/a": {"value": "old", "removed": "gone"}}))
    assert isinstance(incompatible, ReplacementReady)
    blocked_state = [UNAVAILABLE]
    blocked = SystemContext((source("test/a", blocked_state, decode=lambda _: 1),)).replace(
        SystemContextSnapshot({"test/a": {"value": "old"}})
    )
    assert isinstance(blocked, ReplacementBlocked)

    try:
        SystemContext((source("test/a", [1]), source("test/a", [2])))
    except DuplicateContextKeyError:
        pass
    else:
        raise AssertionError("duplicate source key accepted")

    registry = SystemContextRegistry()
    registry.register(SystemContextRegistryEntry("test/b", lambda: SystemContext((source("test/b", b),))))
    registry.register(SystemContextRegistryEntry("test/a", lambda: SystemContext((source("test/a", ["A"]),))))
    assert [item.key for item in registry.load().sources] == ["test/a", "test/b"]
    try:
        registry.register(SystemContextRegistryEntry("test/a", lambda: SystemContext()))
    except DuplicateContextKeyError:
        pass
    else:
        raise AssertionError("duplicate registry key accepted")

    class Memory:
        success = True
        instruction = "Persistent instructions:\n- keep this"
        counts = {"stable_fact": 1, "project_summary": 0, "task_history": 0}

        def load_all(self):
            return {"success": self.success}

        def format_instruction_context(self, max_chars=None):
            return self.instruction

        def get_memory_reference_counts(self):
            return dict(self.counts)

        def format_reference_guidance(self, max_chars=None):
            return "reference guidance" if any(self.counts.values()) else ""

    with TemporaryDirectory() as raw:
        root = Path(raw)
        horizon = root / "HORIZON.md"
        horizon.write_text("alpha", encoding="utf-8")
        session = SessionInfo(
            id="ses", user_id="u", project_id="p", workspace_id="u/p",
            directory=str(root), title="", time_created=1, time_updated=1,
        )
        memory = Memory()
        builtins = build_horizon_system_context_registry(session, memory)
        first = builtins.load().initialize()
        instruction = first.snapshot.sources["horizon/instructions"]["value"]
        assert instruction == [{"path": str(horizon.resolve()), "content": "alpha"}]
        assert "Persistent instructions" in first.baseline
        assert "reference guidance" in first.baseline

        memory.counts["task_history"] = 2
        reference_changed = builtins.load().reconcile(first.snapshot)
        assert isinstance(reference_changed, Updated)
        assert "memory reference guidance replaces" in reference_changed.text
        assert isinstance(
            builtins.load().reconcile(reference_changed.snapshot), Unchanged
        )

        with patch("pathlib.Path.read_text", side_effect=OSError("temporary")):
            unavailable = builtins.load().reconcile(reference_changed.snapshot)
        assert isinstance(unavailable, Unchanged)
        assert unavailable.snapshot.sources["horizon/instructions"] == reference_changed.snapshot.sources["horizon/instructions"]

        horizon.unlink()
        gone = builtins.load().reconcile(reference_changed.snapshot)
        assert isinstance(gone, Updated)
        assert "project instructions no longer apply" in gone.text

        memory.success = False
        try:
            builtins.load().initialize()
        except SystemContextInitializationBlocked as exc:
            assert "horizon/persistent-instructions" in exc.keys
            assert "horizon/memory-reference-guidance" in exc.keys
        else:
            raise AssertionError("persistent memory failure did not block initialization")
    print("smoke_system_context ok")


if __name__ == "__main__":
    main()
