"""Deterministic risk detection for generic execution payloads."""

from __future__ import annotations

import ast
import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


GIT_MUTATION_SUBCOMMANDS = {
    "add",
    "commit",
    "restore",
    "reset",
    "clean",
    "push",
    "tag",
    "checkout",
    "switch",
    "merge",
    "rebase",
    "cherry-pick",
    "revert",
    "rm",
    "mv",
}
PYTHON_SHELL_EXEC_FUNCTIONS = {
    "os.system",
    "os.popen",
}
PYTHON_SUBPROCESS_EXEC_FUNCTIONS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_call",
    "subprocess.check_output",
}


@dataclass(frozen=True)
class ExecutionPayloadRisk:
    risk: str
    source: str
    embedded_tool_risk_family: str
    embedded_tool_risk_operation: str
    embedded_git_subcommands: tuple[str, ...] = ()
    payload_fields: tuple[str, ...] = ()
    generic_execution_cannot_bypass_git_mutation: bool = True

    def to_metadata(self) -> dict[str, Any]:
        return {
            "execution_payload_risk_checked": True,
            "execution_payload_risk": self.risk,
            "execution_payload_risk_source": self.source,
            "embedded_tool_risk_family": self.embedded_tool_risk_family,
            "embedded_tool_risk_operation": self.embedded_tool_risk_operation,
            "embedded_git_subcommands": list(self.embedded_git_subcommands),
            "execution_payload_fields": list(self.payload_fields),
            "generic_execution_cannot_bypass_git_mutation": self.generic_execution_cannot_bypass_git_mutation,
        }


def detect_execution_payload_risk(
    *,
    arguments: Mapping[str, Any],
    raw_argument_values: Mapping[str, Any] | None = None,
    raw_arguments_text: str = "",
    check_raw_text: bool = False,
) -> ExecutionPayloadRisk | None:
    payloads = _execution_payloads(arguments)
    if raw_argument_values:
        payloads.extend(_execution_payloads(raw_argument_values))
    if check_raw_text and raw_arguments_text:
        parsed_raw = _parse_raw_argument_values(raw_arguments_text)
        if parsed_raw:
            payloads.extend(_execution_payloads(parsed_raw))
        else:
            payloads.append(("raw_arguments_text", "script", raw_arguments_text))

    for field_name, source, value in payloads:
        subcommands = _git_mutation_subcommands_from_payload(value, source)
        if subcommands:
            return ExecutionPayloadRisk(
                risk="git_mutation",
                source=source,
                embedded_tool_risk_family="git",
                embedded_tool_risk_operation="git_mutation",
                embedded_git_subcommands=tuple(sorted(subcommands)),
                payload_fields=(field_name,),
            )
    return None


def _parse_raw_argument_values(raw_arguments: str) -> dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _execution_payloads(arguments: Mapping[str, Any]) -> list[tuple[str, str, Any]]:
    payloads: list[tuple[str, str, Any]] = []
    for field_name in ("command", "cmd", "shell_command"):
        value = arguments.get(field_name)
        if value not in (None, ""):
            payloads.append((field_name, "shell_command", value))
    for field_name in ("code", "python_code"):
        value = arguments.get(field_name)
        if value not in (None, ""):
            payloads.append((field_name, "python_code", value))
    value = arguments.get("script")
    if value not in (None, ""):
        payloads.append(("script", "script", value))
    for field_name in ("args", "argv"):
        value = arguments.get(field_name)
        if value not in (None, ""):
            payloads.append((field_name, "argv", value))
    return payloads


def _git_mutation_subcommands_from_payload(value: Any, source: str) -> set[str]:
    if isinstance(value, (list, tuple)):
        return _git_mutation_subcommands_from_tokens([str(item) for item in value])
    text = str(value)
    if source == "python_code":
        return _git_mutation_subcommands_from_python(text)
    if source == "script":
        return _git_mutation_subcommands_from_shell(text) | _git_mutation_subcommands_from_python(text)
    return _git_mutation_subcommands_from_shell(text)


def _git_mutation_subcommands_from_shell(command: str) -> set[str]:
    subcommands: set[str] = set()
    for segment in _split_shell_segments(command):
        tokens = _shlex_tokens(segment)
        if not tokens:
            continue
        subcommands.update(_git_mutation_subcommands_from_tokens(tokens))
        if tokens and tokens[0] in {"sh", "bash", "zsh"} and "-c" in tokens:
            index = tokens.index("-c")
            if index + 1 < len(tokens):
                subcommands.update(_git_mutation_subcommands_from_shell(tokens[index + 1]))
        if tokens and tokens[0] in {"python", "python3"} and "-c" in tokens:
            index = tokens.index("-c")
            if index + 1 < len(tokens):
                subcommands.update(_git_mutation_subcommands_from_python(tokens[index + 1]))
    return subcommands


def _split_shell_segments(command: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"&&|\|\||;|\n|\|", command) if segment.strip()]


def _shlex_tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _git_mutation_subcommands_from_tokens(tokens: list[str]) -> set[str]:
    if len(tokens) < 2 or tokens[0] != "git":
        return set()
    subcommand = tokens[1].strip().lower()
    return {subcommand} if subcommand in GIT_MUTATION_SUBCOMMANDS else set()


def _git_mutation_subcommands_from_python(code: str) -> set[str]:
    subcommands: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _git_mutation_subcommands_from_shell(code)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _python_call_name(node.func)
        if func_name in PYTHON_SHELL_EXEC_FUNCTIONS:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                subcommands.update(_git_mutation_subcommands_from_shell(first.value))
        elif func_name in PYTHON_SUBPROCESS_EXEC_FUNCTIONS:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                subcommands.update(_git_mutation_subcommands_from_shell(first.value))
            elif isinstance(first, (ast.List, ast.Tuple)):
                values: list[str] = []
                for item in first.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        values.append(item.value)
                subcommands.update(_git_mutation_subcommands_from_tokens(values))
    return subcommands


def _python_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _python_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""
