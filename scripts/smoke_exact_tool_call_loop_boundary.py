"""Smoke coverage for the Assistant-response-scoped ToolCall loop boundary."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.exact_tool_call_loop_guard import (
    EXACT_TOOL_CALL_LOOP_THRESHOLD,
    ExactToolCallLoopState,
    _javascript_array_index,
    _ordered_json,
    resolve_exact_tool_call_loop,
)


def _state() -> ExactToolCallLoopState:
    return ExactToolCallLoopState()


def _resolve(
    state: ExactToolCallLoopState,
    tool: str,
    arguments: dict[str, object],
):
    return resolve_exact_tool_call_loop(
        response_state=state,
        canonical_tool=tool,
        arguments=arguments,
        execution_scope="user/project",
    )


def test_threshold_for_identical_ordered_input() -> None:
    state = _state()
    decisions = [
        _resolve(state, "load_document", {"path": "a", "create_chunks": True})
        for _ in range(3)
    ]
    first, second, third = decisions
    assert first.action == "allow" and first.consecutive_count == 1
    assert second.action == "allow" and second.consecutive_count == 2
    assert third.action == "permission" and third.consecutive_count == 3
    assert third.permission == "doom_loop"
    assert third.threshold == EXACT_TOOL_CALL_LOOP_THRESHOLD == 3


def test_argument_key_order_is_not_canonicalized() -> None:
    state = _state()
    decisions = [
        _resolve(state, "sandbox_exec", {"command": "printf order", "timeout": 5}),
        _resolve(state, "sandbox_exec", {"timeout": 5, "command": "printf order"}),
        _resolve(state, "sandbox_exec", {"command": "printf order", "timeout": 5}),
    ]
    assert [item.consecutive_count for item in decisions] == [1, 1, 1]
    assert [item.action for item in decisions] == ["allow", "allow", "allow"]


def test_array_index_keys_use_javascript_property_order() -> None:
    state = _state()
    first_input = {"fields": {"2": "b", "1": "a"}}
    second_input = {"fields": {"1": "a", "2": "b"}}
    decisions = [
        _resolve(state, "sandbox_exec", first_input),
        _resolve(state, "sandbox_exec", second_input),
        _resolve(state, "sandbox_exec", first_input),
    ]
    assert [item.consecutive_count for item in decisions] == [1, 2, 3]
    assert decisions[-1].action == "permission"
    assert len({item.arguments_fingerprint for item in decisions}) == 1


def test_array_index_order_is_numeric_not_lexical() -> None:
    value = {"10": "ten", "2": "two", "1": "one"}
    assert _ordered_json(value) == '{"1":"one","2":"two","10":"ten"}'


def test_numeric_looking_non_indexes_keep_insertion_order() -> None:
    value = {
        "01": "leading-zero",
        "1.0": "decimal",
        "1e0": "exponent",
        "-1": "negative",
        "4294967295": "uint32-max",
    }
    assert _ordered_json(value) == (
        '{"01":"leading-zero","1.0":"decimal","1e0":"exponent",'
        '"-1":"negative","4294967295":"uint32-max"}'
    )


def test_array_index_upper_boundary() -> None:
    value = {
        "4294967295": "not-index",
        "4294967294": "last-index",
        "1": "first-index",
    }
    assert _ordered_json(value) == (
        '{"1":"first-index","4294967294":"last-index",'
        '"4294967295":"not-index"}'
    )


def test_long_numeric_key_is_a_stable_string_property() -> None:
    long_key = "9" * 5000
    arguments = {"fields": {long_key: "value"}}
    assert _javascript_array_index(long_key) is None

    state = _state()
    decisions = [
        _resolve(state, "sandbox_exec", arguments)
        for _ in range(3)
    ]
    assert [item.consecutive_count for item in decisions] == [1, 2, 3]
    assert decisions[-1].action == "permission"
    assert len({item.arguments_fingerprint for item in decisions}) == 1


def test_ordinary_string_property_order_remains_significant() -> None:
    state = _state()
    decisions = [
        _resolve(state, "sandbox_exec", {"b": 2, "a": 1}),
        _resolve(state, "sandbox_exec", {"a": 1, "b": 2}),
        _resolve(state, "sandbox_exec", {"b": 2, "a": 1}),
    ]
    assert [item.consecutive_count for item in decisions] == [1, 1, 1]
    assert [item.action for item in decisions] == ["allow", "allow", "allow"]


def test_array_index_ordering_is_recursive_and_arrays_stay_ordered() -> None:
    value = {
        "outer": {
            "items": [
                {"10": "ten", "1": "one"},
                {"2": "two", "1": "one"},
            ]
        }
    }
    assert _ordered_json(value) == (
        '{"outer":{"items":[{"1":"one","10":"ten"},'
        '{"1":"one","2":"two"}]}}'
    )


def test_javascript_number_equality() -> None:
    state = _state()
    decisions = [
        _resolve(state, "sandbox_exec", {"command": "printf numeric", "timeout": 5.0}),
        _resolve(state, "sandbox_exec", {"command": "printf numeric", "timeout": 5}),
        _resolve(state, "sandbox_exec", {"command": "printf numeric", "timeout": 5.0}),
    ]
    assert [item.consecutive_count for item in decisions] == [1, 2, 3]
    assert [item.action for item in decisions] == ["allow", "allow", "permission"]
    assert len({item.arguments_fingerprint for item in decisions}) == 1


def test_nested_javascript_number_equality() -> None:
    state = _state()
    decisions = [
        _resolve(state, "sandbox_exec", {"config": {"timeout": 5.0}}),
        _resolve(state, "sandbox_exec", {"config": {"timeout": 5}}),
        _resolve(state, "sandbox_exec", {"config": {"timeout": 5.0}}),
    ]
    assert [item.consecutive_count for item in decisions] == [1, 2, 3]
    assert decisions[-1].action == "permission"
    assert len({item.arguments_fingerprint for item in decisions}) == 1


def test_lone_surrogate_is_stable() -> None:
    state = _state()
    decisions = [
        _resolve(state, "sandbox_exec", {"command": "printf surrogate", "value": "\ud800"})
        for _ in range(3)
    ]
    assert [item.consecutive_count for item in decisions] == [1, 2, 3]
    assert decisions[-1].action == "permission"
    assert len({item.arguments_fingerprint for item in decisions}) == 1


def test_streak_resets() -> None:
    changed_arguments = _state()
    _resolve(changed_arguments, "load_document", {"path": "a"})
    decision = _resolve(changed_arguments, "load_document", {"path": "b"})
    assert decision.consecutive_count == 1

    changed_tool = _state()
    _resolve(changed_tool, "read_file", {"path": "a"})
    decision = _resolve(changed_tool, "read_document", {"path": "a"})
    assert decision.consecutive_count == 1
    canonical_namespace = _state()
    _resolve(canonical_namespace, "write_file", {"path": "a"})
    decision = _resolve(
        canonical_namespace,
        "mcp_filesystem.write_file",
        {"path": "a"},
    )
    assert decision.consecutive_count == 1

def test_result_independent_within_response() -> None:
    for _simulated_result in (
        "success",
        "failed",
        "recoverable",
        "idempotent_replay",
    ):
        state = _state()
        counts = []
        for _tool_call in range(3):
            decision = _resolve(
                state,
                "local.load_document",
                {"path": "a"},
            )
            counts.append(decision.consecutive_count)
        assert counts == [1, 2, 3]
        assert decision.action == "permission"


def test_new_assistant_response_does_not_accumulate() -> None:
    decisions = [
        _resolve(_state(), "local.load_document", {"path": "a"})
        for _assistant_response in range(3)
    ]
    assert [item.consecutive_count for item in decisions] == [1, 1, 1]
    assert [item.action for item in decisions] == ["allow", "allow", "allow"]


def test_read_only_tool_boundary() -> None:
    state = _state()
    decisions = [
        _resolve(state, "read_file", {"path": "same.txt"})
        for _ in range(3)
    ]
    assert [item.action for item in decisions] == ["allow", "allow", "permission"]


def main() -> None:
    test_threshold_for_identical_ordered_input()
    test_argument_key_order_is_not_canonicalized()
    test_array_index_keys_use_javascript_property_order()
    test_array_index_order_is_numeric_not_lexical()
    test_numeric_looking_non_indexes_keep_insertion_order()
    test_array_index_upper_boundary()
    test_long_numeric_key_is_a_stable_string_property()
    test_ordinary_string_property_order_remains_significant()
    test_array_index_ordering_is_recursive_and_arrays_stay_ordered()
    test_javascript_number_equality()
    test_nested_javascript_number_equality()
    test_lone_surrogate_is_stable()
    test_streak_resets()
    test_result_independent_within_response()
    test_new_assistant_response_does_not_accumulate()
    test_read_only_tool_boundary()
    print("smoke_exact_tool_call_loop_boundary ok")


if __name__ == "__main__":
    main()
