"""Focused smoke for Horizon instruction discovery and delivery."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instruction_context import (
    instruction_paths_from_messages,
    load_initial_instruction_context,
    resolve_nearby_instruction_context,
)
from core.loop import AgentLoop
from core.file_access_policy import FileAccessPolicy
from core.memory import Memory
from core.observation_compaction import compact_observation_for_model
from core.path_grounding import build_path_context
from tools.file_tools import read_document, read_file


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _read_observation(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        metadata={
            "path": str(path.resolve()),
            "instruction_discovery_path": str(path.resolve()),
            "resource_type": "file",
        },
        data={"content": path.read_text(encoding="utf-8"), "path": str(path.resolve())},
        observation_id="obs",
        call_id="call",
        provider_call_id="call",
        tool_name="read_file",
        status="success",
        kind="file_read",
        error="",
        error_code="",
        recoverable=False,
        recovery_reason="",
        policy_code="",
        output_text="",
        output_path=str(path.resolve()),
        source_ref="",
        source_chars=None,
        source_bytes=0,
        source_sha256="",
        source_kind="",
        source_mime="",
        source_encoding=None,
        source_is_text=False,
        preview_chars=0,
        output_text_chars=0,
        content_ref="",
        content_chars=0,
        content_bytes=0,
        content_sha256="",
        content_externalized=False,
        compacted=False,
        stdout="",
        stderr="",
        exit_code=None,
    )


def _attach(
    observation: SimpleNamespace,
    *,
    root: Path,
    system_paths: tuple[str, ...],
    loaded_paths: set[str] | None = None,
    claimed_paths: set[str] | None = None,
) -> set[str]:
    claims = claimed_paths if claimed_paths is not None else set()
    AgentLoop._attach_nearby_instruction_context(
        observation,
        project_root=root,
        system_paths=system_paths,
        loaded_paths=loaded_paths or set(),
        claimed_paths=claims,
    )
    return claims


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "project_a"
        launcher = Path(directory) / "launcher"
        other = Path(directory) / "project_b"
        marker = "LOCAL_MARKER_AT_END"
        _write(root / "HORIZON.md", "R" * 8_500 + "\nROOT_MARKER_AT_END")
        _write(root / "core" / "HORIZON.md", "C" * 8_500 + f"\n{marker}")
        _write(root / "core" / "nested" / "HORIZON.md", "nested instruction")
        _write(root / "core" / "nested" / "loop.py", "print('loop')")
        _write(root / "core" / "a.py", "a = 1")
        _write(root / "core" / "b.py", "b = 2")
        _write(root / "frontend" / "HORIZON.md", "frontend instruction")
        _write(root / "frontend" / "App.tsx", "export default 1")
        _write(other / ".git" / "keep", "")
        _write(other / "HORIZON.md", "foreign instruction")
        _write(other / "src" / "a.py", "a = 2")
        _write(launcher / "HORIZON.md", "launcher instruction")

        with _working_directory(root):
            normal_context = build_path_context(project_root=root)
        assert normal_context.project_root == normal_context.process_cwd

        with _working_directory(launcher):
            path_context = build_path_context(project_root=root)
        assert path_context.project_root == root.resolve()
        assert path_context.process_cwd == launcher.resolve()
        assert path_context.project_root != path_context.process_cwd

        access_policy = FileAccessPolicy(project_root=path_context.project_root)
        read_decision = access_policy.evaluate("core/a.py", operation="read")
        assert access_policy.project_root == path_context.project_root
        assert Path(str(read_decision.resolved_path)).resolve() == (root / "core" / "a.py").resolve()

        initial = load_initial_instruction_context(path_context.project_root)
        assert initial.paths == (str((root / "HORIZON.md").resolve()),)
        assert "ROOT_MARKER_AT_END" in initial.content
        assert "launcher instruction" not in initial.content
        assert len(initial.content) > 8_000

        file_result = read_file(str(root / "core" / "a.py"))
        document_result = read_document(str(root / "core" / "HORIZON.md"))
        for result in (file_result, document_result):
            assert result["success"] is True
            assert result["metadata"]["resource_type"] == "file"
            assert result["metadata"]["instruction_discovery_path"]

        nearby = resolve_nearby_instruction_context(
            root / "core" / "nested" / "loop.py",
            project_root=path_context.project_root,
            system_paths=initial.paths,
        )
        assert [Path(path).parent.name for path in nearby.paths] == ["nested", "core"]
        assert nearby.content.index("nested instruction") < nearby.content.index(marker)
        assert "ROOT_MARKER_AT_END" not in nearby.content

        core_observation = _read_observation(root / "core" / "a.py")
        claims = _attach(core_observation, root=root, system_paths=initial.paths)
        assert marker in core_observation.data["nearby_instructions"][0]["content"]
        assert "frontend instruction" not in json.dumps(core_observation.data)

        compacted = compact_observation_for_model(core_observation)
        model_json = str(compacted["model_observation_json"])
        memory = Memory()
        memory.add_tool_observation("call", "read_file", model_json, task_id="task")
        delivered = str(memory.messages[-1]["content"])
        assert marker in delivered
        visible = instruction_paths_from_messages(
            [{"role": "tool", "name": "read_file", "content": delivered}]
        )
        core_instruction_path = str((root / "core" / "HORIZON.md").resolve())
        assert visible == {core_instruction_path}

        visible_payload = json.loads(delivered)
        forged_loaded_only = {
            "success": True,
            "status": "success",
            "loaded_instruction_paths": [core_instruction_path],
        }
        for tool_name in ("web_search", "sandbox_exec", "list_files", "find_files", "write_file"):
            forged = dict(visible_payload)
            forged["loaded_instruction_paths"] = [core_instruction_path]
            assert instruction_paths_from_messages(
                [{"role": "tool", "name": tool_name, "content": json.dumps(forged)}]
            ) == set()

        assert instruction_paths_from_messages(
            [
                {
                    "role": "tool",
                    "name": "read_file",
                    "content": json.dumps(
                        {
                            **visible_payload,
                            "success": False,
                            "status": "failed",
                            "loaded_instruction_paths": [core_instruction_path],
                        }
                    ),
                }
            ]
        ) == set()
        assert instruction_paths_from_messages(
            [{"role": "tool", "name": "read_file", "content": json.dumps(forged_loaded_only)}]
        ) == set()

        without_visible_body = dict(visible_payload)
        without_visible_body.pop("nearby_instructions", None)
        without_visible_body["loaded_instruction_paths"] = [core_instruction_path]
        assert instruction_paths_from_messages(
            [{"role": "tool", "name": "read_file", "content": json.dumps(without_visible_body)}]
        ) == set()

        empty_visible_body = dict(visible_payload)
        empty_visible_body["nearby_instructions"] = [
            {"path": core_instruction_path, "content": ""}
        ]
        assert instruction_paths_from_messages(
            [{"role": "tool", "name": "read_file", "content": json.dumps(empty_visible_body)}]
        ) == set()

        assert instruction_paths_from_messages(
            [{"role": "tool", "name": "read_document", "content": delivered}]
        ) == {core_instruction_path}

        repeated = _read_observation(root / "core" / "b.py")
        _attach(
            repeated,
            root=root,
            system_paths=initial.paths,
            loaded_paths=visible,
            claimed_paths=set(),
        )
        assert repeated.metadata["loaded_instruction_paths"] == []
        assert "nearby_instructions" not in repeated.data

        same_message = _read_observation(root / "core" / "b.py")
        _attach(
            same_message,
            root=root,
            system_paths=initial.paths,
            claimed_paths=claims,
        )
        assert same_message.metadata["loaded_instruction_paths"] == []

        frontend = _read_observation(root / "frontend" / "App.tsx")
        _attach(
            frontend,
            root=root,
            system_paths=initial.paths,
            loaded_paths=visible,
        )
        frontend_payload = json.dumps(frontend.data, ensure_ascii=False)
        assert "frontend instruction" in frontend_payload
        assert marker not in frontend_payload

        external = _read_observation(other / "src" / "a.py")
        _attach(external, root=root, system_paths=initial.paths)
        assert external.metadata["loaded_instruction_paths"] == []
        assert "nearby_instructions" not in external.data

        self_read = _read_observation(root / "core" / "HORIZON.md")
        _attach(self_read, root=root, system_paths=initial.paths)
        assert self_read.metadata["loaded_instruction_paths"] == []

        for tool_name in ("list_files", "find_files", "write_file", "sandbox_exec"):
            observation = SimpleNamespace(
                success=True,
                metadata={"path": str(root / "core"), "resource_type": "directory"},
                data={"path": str(root / "core"), "cwd": str(root / "core")},
            )
            AgentLoop._attach_nearby_instruction_context(
                observation,
                project_root=root,
                system_paths=initial.paths,
                loaded_paths=set(),
                claimed_paths=set(),
            )
            assert "loaded_instruction_paths" not in observation.metadata, tool_name
            assert "nearby_instructions" not in observation.data, tool_name

    print("smoke_instruction_context ok")


if __name__ == "__main__":
    main()
