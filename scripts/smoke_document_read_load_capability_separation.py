"""Focused smoke for temporary document reads versus persistent document loads."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = {
    "AGENT_ACCESS_MODE": "full_access",
    "ENABLE_WORKSPACE_ISOLATION": "true",
    "MCP_ENABLED": "false",
    "EMBEDDING_ENABLED": "false",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_MODEL": "deepseek-chat",
    "LLM_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
}
_OLD_ENV = {key: os.environ.get(key) for key in (*_ENV, "WORKSPACE_ROOT")}
os.environ.update(_ENV)

import core.loop as loop_module
from core.initial_agent_turn import (
    capability_for_tool_spec,
)
from core.initial_tool_surface import (
    build_initial_tool_surface,
    resolve_permission_tool_schemas,
)
from core.memory import Memory
from core.workspace_runtime import get_document_dir
from providers.mock import MockProvider, assistant_message
from tools import document_tools
from tools.file_tools import read_document as real_read_document
from tools.file_tools import read_file as real_read_file
from tools.registry import get_unified_tool_schemas, get_unified_tool_specs


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _message(
    content: str = "",
    calls: list[Any] | None = None,
) -> SimpleNamespace:
    return assistant_message(content, calls or [])


def _schema_names(schemas: Any) -> list[str]:
    return [
        str(schema.get("function", {}).get("name") or "")
        for schema in schemas or []
        if isinstance(schema, dict)
    ]


def _run_loop(
    responses: list[Any],
    *,
    request: str,
    tools: dict[str, Callable[..., dict[str, Any]]],
    access_mode: str = "full_access",
    project_id: str,
    max_steps: int = 5,
) -> tuple[str, MockProvider, dict[str, Any], list[str]]:
    previous_mode = os.environ.get("AGENT_ACCESS_MODE")
    previous_settings = loop_module.settings
    os.environ["AGENT_ACCESS_MODE"] = access_mode
    loop_module.settings = replace(
        previous_settings,
        agent_access_mode=access_mode,
    )
    llm = MockProvider(responses=responses)
    loop = loop_module.AgentLoop(llm, Memory(), max_steps=max_steps)
    captured: dict[str, Any] = {}
    executed: list[str] = []

    def finish(self: Any, trace: Any, state: Any, answer: str) -> str:
        captured.update(
            trace=trace,
            state=state,
            answer=answer,
            metrics=self._runtime_metrics,
        )
        return answer

    loop._finish_with_trace = MethodType(finish, loop)
    for name, implementation in tools.items():
        def counted(
            *args: Any,
            __name: str = name,
            __implementation: Callable[..., dict[str, Any]] = implementation,
            **kwargs: Any,
        ) -> dict[str, Any]:
            executed.append(__name)
            return __implementation(*args, **kwargs)

        loop.tools[name] = counted
    try:
        answer = loop.run(
            request,
            user_id="document-separation",
            project_id=project_id,
        )
    finally:
        if previous_mode is None:
            os.environ.pop("AGENT_ACCESS_MODE", None)
        else:
            os.environ["AGENT_ACCESS_MODE"] = previous_mode
        loop_module.settings = previous_settings
    return answer, llm, captured, executed


def _event_data(captured: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [
        dict(event.data or {})
        for event in captured["trace"].events
        if event.event_type == event_type
    ]


def _make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["name", "value"])
    sheet.append(["alpha", 1])
    sheet.append(["beta", 2])
    workbook.save(path)


def test_tool_specs_and_capability_resolver() -> None:
    specs = get_unified_tool_specs()
    schemas = get_unified_tool_schemas()
    read_spec = specs["read_document"]
    assert read_spec.kind == "file_read"
    assert read_spec.side_effect is False
    assert read_spec.risk == "read_only"
    assert read_spec.reads_files is True
    assert read_spec.path_policy == "file_read"
    assert "file_read" in read_spec.capabilities
    assert "document_read" not in read_spec.capabilities
    assert "document_load" not in read_spec.capabilities
    assert capability_for_tool_spec(
        read_spec,
        tool_name="read_document",
    ) == "file_read"

    for name in (
        "load_document",
        "load_documents_from_directory",
        "rebuild_chunks_for_document",
    ):
        spec = specs[name]
        assert spec.kind == "file_read"
        assert spec.side_effect is True
        assert spec.risk == "internal_state"
        assert spec.reads_files is True
        assert spec.path_policy == "file_read"
        assert {"document_load", "state_mutation"} <= set(spec.capabilities)
        assert "file_read" not in spec.capabilities
        assert capability_for_tool_spec(spec, tool_name=name) == "document_load"

    read_only = build_initial_tool_surface(
        specs,
        schemas,
        access_mode="read_only",
    )
    full_access = build_initial_tool_surface(
        specs,
        schemas,
        access_mode="full_access",
    )
    assert list(read_only.tool_names) == _schema_names(
        resolve_permission_tool_schemas(
            specs, schemas, access_mode="read_only"
        )
    )
    assert list(full_access.tool_names) == _schema_names(
        resolve_permission_tool_schemas(
            specs, schemas, access_mode="full_access"
        )
    )
    assert "browser_click_and_extract" in full_access.tool_names
    assert {"read_file", "read_document"} <= set(read_only.tool_names)
    assert {
        "load_document",
        "load_documents_from_directory",
    }.isdisjoint(read_only.tool_names)
    assert {
        "read_file",
        "read_document",
        "load_document",
        "load_documents_from_directory",
    } <= set(full_access.tool_names)


def test_read_document_loop_and_read_only(root: Path) -> None:
    document = root / "temporary-read.xlsx"
    _make_xlsx(document)
    store_counts: list[tuple[int, int]] = []

    def read_without_store_mutation(path: str, **kwargs: Any) -> dict[str, Any]:
        store = document_tools._store()
        before = int(store.list_documents()["data"]["documents_count"])
        result = real_read_document(path, **kwargs)
        after = int(store.list_documents()["data"]["documents_count"])
        store_counts.append((before, after))
        return result

    final_prose = "已临时读取并总结该工作簿。"
    answer, llm, captured, executed = _run_loop(
        [
            _message(
                "",
                [_call("read-document", "read_document", {"path": str(document)})],
            ),
            _message(final_prose),
        ],
        request="临时读取这个文档并总结。",
        tools={"read_document": read_without_store_mutation},
        project_id="temporary-read",
    )
    assert answer == final_prose
    assert executed == ["read_document"]
    assert len(llm.calls) == 2
    assert store_counts == [(0, 0)]
    assert captured["state"].metadata["observed_capabilities"] == ["file_read"]
    assert (
        captured["state"].metadata["capability_witnesses"]["file_read"][
            "tool_name"
        ]
        == "read_document"
    )
    assert not captured["state"].document_loaded
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    metrics = captured["metrics"].summary()
    assert metrics["tool_call_count"] == 1
    assert metrics["real_tool_execution_count"] == 1
    assert metrics["exact_tool_loop_stop_count"] == 0
    assert not any(
        str(call["options"].stage) == "final_answer" for call in llm.calls
    )
    assert not _event_data(captured, "max_steps_reached")

    read_only_answer, read_only_llm, _, read_only_executed = _run_loop(
        [
            _message(
                "",
                [
                    _call(
                        "read-only-document",
                        "read_document",
                        {"path": str(document)},
                    )
                ],
            ),
            _message("只读模式下完成了临时读取。"),
        ],
        request="在只读模式读取文档。",
        tools={"read_document": real_read_document},
        access_mode="read_only",
        project_id="read-only",
    )
    assert read_only_answer
    assert read_only_executed == ["read_document"]
    continuation_names = set(_schema_names(read_only_llm.calls[1]["tools"]))
    assert {
        "load_document",
        "load_documents_from_directory",
    }.isdisjoint(continuation_names)


def test_document_load_loop(root: Path) -> None:
    document = root / "persistent-load.xlsx"
    _make_xlsx(document)
    load_evidence: dict[str, Any] = {}

    def load_into_store(path: str, **kwargs: Any) -> dict[str, Any]:
        result = document_tools.load_document(path, **kwargs)
        load_evidence["result"] = result
        store = document_tools._store()
        load_evidence["documents"] = store.list_documents()
        document_id = str(result.get("data", {}).get("document_id") or "")
        load_evidence["chunks"] = store.list_chunks(document_id)
        return result

    final_prose = "文档已导入当前 workspace 的本地知识库。"
    answer, llm, captured, executed = _run_loop(
        [
            _message(
                "",
                [
                    _call(
                        "load-document",
                        "load_document",
                        {"path": str(document), "create_chunks": True},
                    )
                ],
            ),
            _message(final_prose),
        ],
        request="将该文档导入当前 workspace 的本地知识库。",
        tools={"load_document": load_into_store},
        project_id="persistent-load",
    )
    assert answer == final_prose
    assert executed == ["load_document"]
    assert len(llm.calls) == 2
    assert load_evidence["result"]["success"] is True
    assert (
        load_evidence["documents"]["data"]["documents_count"]
        == 1
    )
    assert load_evidence["chunks"]["data"]["chunks_count"] >= 1
    assert captured["state"].document_loaded is True
    assert captured["state"].chunk_count >= 1
    assert captured["state"].metadata["observed_capabilities"] == [
        "document_load"
    ]
    assert "file_read" not in captured["state"].metadata[
        "satisfied_capabilities"
    ]
    assert not any(name == "read_document" for name in executed)
    assert captured["state"].metadata["direct_agent_prose_adopted"] is True
    load_metrics = captured["metrics"].summary()
    assert load_metrics["tool_call_count"] == 1
    assert load_metrics["real_tool_execution_count"] == 1
    assert load_metrics["exact_tool_loop_stop_count"] == 0
    assert not any(
        str(call["options"].stage) == "final_answer" for call in llm.calls
    )


def test_recovery_and_document_failures(root: Path) -> None:
    valid = root / "recoverable.xlsx"
    _make_xlsx(valid)
    answer, llm, captured, executed = _run_loop(
        [
            _message(
                "",
                [_call("read-text", "read_file", {"path": str(valid)})],
            ),
            _message(
                "",
                [_call("read-structured", "read_document", {"path": str(valid)})],
            ),
            _message("已改用结构化文档读取完成。"),
        ],
        request="读取该表格。",
        tools={
            "read_file": real_read_file,
            "read_document": real_read_document,
        },
        project_id="reader-recovery",
    )
    assert answer
    assert executed == ["read_file", "read_document"]
    assert {"read_file", "read_document"} <= set(
        _schema_names(llm.calls[1]["tools"])
    )
    surfaces = [
        event.data
        for event in captured["trace"].events
        if event.event_type == "continuation_tool_surface_resolved"
    ]
    assert surfaces
    assert all(
        item.get("source") == "registry_availability_permission"
        for item in surfaces
    )
    assert not {
        "load_document",
        "load_documents_from_directory",
    } & set(executed)

    for suffix, expected_code, prose in (
        (
            ".xlsx",
            "document_xlsx_invalid_or_corrupt",
            "该工作簿已损坏，无法读取。",
        ),
        (
            ".xls",
            "document_legacy_format_unsupported",
            "旧格式不受支持，请先转换为 .xlsx。",
        ),
    ):
        document = root / f"broken{suffix}"
        document.write_bytes(b"not-a-valid-document")
        answer, llm, captured, executed = _run_loop(
            [
                _message(
                    "",
                    [
                        _call(
                            f"read-{suffix}",
                            "read_document",
                            {"path": str(document)},
                        )
                    ],
                ),
                _message(prose),
            ],
            request="读取该文档。",
            tools={"read_document": real_read_document},
            project_id=f"failure-{suffix[1:]}",
        )
        assert answer == prose
        assert executed == ["read_document"]
        observations = captured["state"].metadata["completion_observations"]
        assert observations[-1]["error_code"] == expected_code
        assert len(llm.calls) == 2
        assert _schema_names(llm.calls[-1]["tools"]) == _schema_names(
            llm.calls[0]["tools"]
        )
        assert not {
            "load_document",
            "load_documents_from_directory",
        } & set(executed)


def test_schema_descriptions() -> None:
    schemas = {
        schema["function"]["name"]: schema["function"]["description"]
        for schema in get_unified_tool_schemas()
    }
    read_description = schemas["read_document"]
    assert "PPTX" not in read_description
    assert "slides" not in read_description.lower()
    assert all(
        name in read_description
        for name in ("XLSX", "DOCX", "PDF")
    )
    assert "Temporarily" in read_description
    assert "without importing" in read_description
    load_description = schemas["load_document"]
    assert "knowledge base" in load_description
    assert "DocumentStore" in load_description
    assert "chunks" in load_description


def main() -> None:
    old_cwd = Path.cwd()
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            os.chdir(root)
            os.environ["WORKSPACE_ROOT"] = str(root / "workspace_store")
            test_tool_specs_and_capability_resolver()
            test_read_document_loop_and_read_only(root)
            test_document_load_loop(root)
            test_recovery_and_document_failures(root)
            test_schema_descriptions()
    finally:
        os.chdir(old_cwd)
        for key, value in _OLD_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("Document read/load capability separation smoke passed.")


if __name__ == "__main__":
    main()
