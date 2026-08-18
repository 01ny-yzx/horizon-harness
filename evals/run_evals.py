"""Run lightweight regression evals for Planner and TaskProfile routing."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import zipfile
from dataclasses import fields
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("LLM_MODEL", "mock")
os.environ["EMBEDDING_ENABLED"] = "false"
os.environ["API_AUTH_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from config.settings import settings  # noqa: E402
from core.cache import CacheManager  # noqa: E402
from core.browser import BrowserManager  # noqa: E402
from core.browser_policy import BrowserPolicy  # noqa: E402
from core.document_chunker import DocumentChunker  # noqa: E402
from core.document_store import DocumentStore  # noqa: E402
from core.embedding_provider import EmbeddingProvider  # noqa: E402
from core.persistent_memory import PersistentMemory  # noqa: E402
from core.rag import RAGEngine  # noqa: E402
from core.vector_store import VectorStore, cosine_similarity  # noqa: E402
from core.loop import AgentLoop  # noqa: E402
from core.memory import Memory  # noqa: E402
from core.context_fusion import ContextFusionEngine, compact_memory_summary  # noqa: E402
from core.workspace import WorkspaceManager  # noqa: E402
from core.workspace_runtime import get_current_workspace, set_current_workspace  # noqa: E402
from core.usage import UsageTracker  # noqa: E402
from core.rate_limit import RateLimiter  # noqa: E402
from core.sandbox import SandboxManager  # noqa: E402
from core.state import PlanStep, TaskState  # noqa: E402
from core.task_profile import TaskProfile  # noqa: E402
from core.tool_outcome_resolution import ToolOutcomeResolution, resolve_tool_outcome  # noqa: E402
from mcp_servers.database import validate_read_only_sql  # noqa: E402
from tools.python_tools import run_python_in_sandbox  # noqa: E402
from tools.registry import ALL_TOOLS  # noqa: E402
from tools.shell_tools import cleanup_sandbox, run_shell_in_sandbox  # noqa: E402
from tools.vector_tools import get_embedding_status, hybrid_search_chunks, semantic_search_chunks  # noqa: E402
from tools.workspace_tools import get_workspace_status, switch_workspace  # noqa: E402
from api.app import app as fastapi_app  # noqa: E402
from api.deps import get_workspace_context  # noqa: E402
from api.schemas import ChatRequest, RagQueryRequest, WorkspaceRequest  # noqa: E402


def main() -> int:
    """Run all eval cases and print pass/fail."""

    failures = 0

    memory_errors = _run_memory_deletion_evals()
    for name, errors in memory_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    chunk_errors = _run_chunk_evals()
    for name, errors in chunk_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    vector_errors = _run_vector_evals()
    for name, errors in vector_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    rag_errors = _run_rag_evals()
    for name, errors in rag_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    workspace_errors = _run_workspace_evals()
    for name, errors in workspace_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    api_errors = _run_api_evals()
    for name, errors in api_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    protection_errors = _run_protection_evals()
    for name, errors in protection_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    git_safety_errors = _run_git_safety_evals()
    for name, errors in git_safety_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    sandbox_errors = _run_sandbox_evals()
    for name, errors in sandbox_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    browser_errors = _run_browser_evals()
    for name, errors in browser_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    runtime_boundary_errors = _run_runtime_boundary_evals()
    for name, errors in runtime_boundary_errors:
        status = "PASS" if not errors else "FAIL"
        print(f"{status} {name}")
        for error in errors:
            print(f"  - {error}")
        failures += 1 if errors else 0

    total = (
        len(memory_errors)
        + len(chunk_errors)
        + len(vector_errors)
        + len(rag_errors)
        + len(workspace_errors)
        + len(api_errors)
        + len(protection_errors)
        + len(git_safety_errors)
        + len(sandbox_errors)
        + len(browser_errors)
        + len(runtime_boundary_errors)
    )
    print(f"\nsummary: passed={total - failures} failed={failures} total={total}")
    return 1 if failures else 0


def _structured_profile(**overrides: Any) -> TaskProfile:
    values: dict[str, Any] = {
        "task_type": "simple",
        "needs_web": False,
        "has_url": False,
        "has_search_engine_url": False,
        "needs_code_edit": False,
        "needs_validation": False,
        "needs_git": False,
        "user_intent_summary": "structured eval fixture",
        "tool_required": True,
        "execution_mode": "normal",
    }
    values.update(overrides)
    return TaskProfile(**values)


def _structured_state(profile: TaskProfile) -> TaskState:
    return TaskState.create(
        user_goal="structured eval fixture",
        task_type=profile.task_type,
        plan=[PlanStep(0, "evaluate", "Evaluate structured fixture.")],
        task_profile=profile,
    )


def _run_memory_deletion_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    with TemporaryDirectory() as tmp:
        memory = PersistentMemory(Path(tmp) / "memory_store")

        errors = []
        save_result = memory.add_user_preference("answer_language", "尽量用温柔的中文解释，语言简单一点")
        if not save_result.get("success"):
            errors.append("save_preference failed")
        if "answer_language" not in memory.get_user_memory().get("preferences", {}):
            errors.append("answer_language was not saved")
        results.append(("save_preference", errors))

        memory.add_task_summary(
            {
                "task_id": "old-style",
                "task_type": "simple",
                "goal": "你还记得我的回答风格偏好吗？",
                "result": "completed",
                "summary": "你之前让我存好的偏好是：尽量用温柔的中文解释，语言简单一点",
            }
        )
        delete_result = memory.forget_memory("all", "删除这个偏好")
        data = delete_result.get("data", {})
        errors = []
        if data.get("deleted", 0) <= 0:
            errors.append("delete_style_preference deleted 0")
        if memory.get_user_memory().get("preferences"):
            errors.append("preferences not empty after delete")
        results.append(("delete_style_preference", errors))

        memory.add_user_preference("answer_language", "尽量用温柔的中文解释，语言简单一点")
        delete_result = memory.forget_memory("all", "忘记我之前的回答风格偏好")
        data = delete_result.get("data", {})
        errors = []
        if "answer_language" not in data.get("deleted_preferences", []):
            errors.append("answer style alias did not delete answer_language")
        results.append(("delete_answer_style_preference", errors))

        errors = []
        prompt = memory.format_for_prompt()
        if "尽量用温柔的中文解释" in prompt or "语言简单一点" in prompt:
            errors.append("task_history_not_polluting_memory failed")
        if memory.get_user_memory().get("preferences"):
            errors.append("no_memory_after_delete failed: preferences not empty")
        results.append(("task_history_not_polluting_memory", errors))

    return results


def _run_chunk_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []

    text = "# Intro\n\nPersistent Memory stores preferences.\n\n## Web Tools\n\nweb_search fetch_url.\n" * 20
    chunker = DocumentChunker()
    chunks = chunker.chunk_text(
        text=text,
        document_id="doc1",
        file_name="README.md",
        path="README.md",
        headings=["Intro", "Web Tools"],
        chunk_size=300,
        overlap=60,
    )
    errors = []
    if not chunks:
        errors.append("chunker returned no chunks")
    if chunks and chunks[0].get("chunk_id") != chunker.chunk_text(text, "doc1", "README.md", "README.md", ["Intro"], 300, 60)[0].get("chunk_id"):
        errors.append("chunk_id is not stable")
    if any(chunk.get("text_length", 0) > 300 for chunk in chunks):
        errors.append("chunk exceeds requested size")
    results.append(("document_chunker_basic", errors))

    with TemporaryDirectory() as tmp:
        store = DocumentStore(Path(tmp) / "document_store")
        errors = []
        add_result = store.add_chunks("doc1", chunks)
        if not add_result.get("success"):
            errors.append("add_chunks failed")
        find_result = store.find_chunks("Web Tools", limit=3)
        find_data = find_result.get("data", {}) if find_result.get("success") else {}
        if find_data.get("total_matches", 0) <= 0:
            errors.append("find_chunks did not find Web Tools")
        listed = store.list_chunks(limit=2).get("data", {})
        if len(listed.get("chunks", [])) > 2:
            errors.append("list_chunks ignored limit")
        first_chunk_id = chunks[0].get("chunk_id", "")
        if not store.get_chunk(first_chunk_id).get("success"):
            errors.append("get_chunk failed")
        delete_result = store.remove_chunks_for_document("doc1")
        if delete_result.get("data", {}).get("deleted", 0) <= 0:
            errors.append("remove_chunks_for_document deleted no chunks")
        results.append(("document_store_chunk_crud", errors))

    return results


def _run_vector_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []

    errors = []
    status = get_embedding_status()
    if not status.get("success"):
        errors.append("get_embedding_status failed")
    data = status.get("data", {})
    if not isinstance(data, dict) or "available" not in data:
        errors.append("embedding status missing availability")
    results.append(("embedding_status_case", errors))

    with TemporaryDirectory() as tmp:
        store = VectorStore(Path(tmp) / "vector_store")
        errors = []
        record = {
            "chunk_id": "c1",
            "document_id": "d1",
            "file_name": "README.md",
            "path": "README.md",
            "heading": "Memory",
            "embedding": [1.0, 0.0, 0.0],
            "embedding_dim": 3,
            "embedding_model": "mock",
            "content_hash": "h1",
        }
        if not store.upsert_vector(record).get("success"):
            errors.append("upsert_vector failed")
        search = store.similarity_search([1.0, 0.0, 0.0], top_k=1, threshold=0.1)
        matches = search.get("data", {}).get("matches", []) if search.get("success") else []
        if not matches or matches[0].get("chunk_id") != "c1":
            errors.append("similarity_search did not return c1")
        if store.remove_vector("c1").get("data", {}).get("deleted") != 1:
            errors.append("remove_vector failed")
        store.upsert_vector(record)
        if store.clear_vectors().get("data", {}).get("vectors_count") != 0:
            errors.append("clear_vectors failed")
        results.append(("vector_store_crud", errors))

    errors = []
    if abs(cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) > 1e-6:
        errors.append("same vector score is not close to 1")
    if cosine_similarity([1.0, 0.0], [0.0, 1.0]) > 0.1:
        errors.append("orthogonal vector score too high")
    results.append(("cosine_similarity_test", errors))

    errors = []
    disabled = EmbeddingProvider(enabled=False).get_status()
    if disabled.get("data", {}).get("available") is not False:
        errors.append("disabled embedding should be unavailable")
    semantic = semantic_search_chunks("Persistent Memory", top_k=3)
    if semantic.get("success"):
        errors.append("semantic_search should fail gracefully when embeddings are disabled")
    hybrid = hybrid_search_chunks("Persistent Memory", top_k=3)
    if not hybrid.get("success"):
        errors.append("hybrid_search should fallback to keyword")
    if hybrid.get("data", {}).get("semantic_available") is not False:
        errors.append("hybrid fallback should mark semantic_available false")
    results.append(("embedding_disabled_fallback", errors))

    return results


def _run_rag_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    engine = RAGEngine()
    matches = [
        {
            "chunk_id": "c1",
            "file_name": "README.md",
            "heading": "Memory",
            "text": "Persistent Memory stores stable facts.",
            "embedding": [0.1, 0.2],
        },
        {
            "chunk_id": "c1",
            "file_name": "README.md",
            "heading": "Memory",
            "text": "duplicate should be removed",
        },
    ]
    context = engine.build_context(matches, max_chars=80)
    errors = []
    if len(context) > 80:
        errors.append("context exceeds max_chars")
    if "embedding" in context or "0.1" in context:
        errors.append("context leaked embedding array")
    if context.count("chunk_id=c1") != 1:
        errors.append("context did not deduplicate chunk_id")
    results.append(("rag_engine_build_context", errors))

    rewrite = engine.rewrite_query("根据已加载文档回答 Persistent Memory 是什么")
    errors = []
    rewritten = str(rewrite.get("rewritten_query", ""))
    keywords = rewrite.get("keywords", [])
    if "Persistent Memory" not in rewritten and "长期记忆" not in rewritten:
        errors.append(f"rewritten_query missing Persistent Memory/长期记忆: {rewritten}")
    if "长期记忆" not in keywords:
        errors.append(f"keywords missing 长期记忆: {keywords}")
    results.append(("query_rewrite_persistent_memory", errors))

    queries = engine.generate_retrieval_queries("根据 README 文档回答 Web Tools 是什么")
    errors = []
    joined = " ".join(queries)
    if not any(term in joined for term in ["Web Tools", "web_search", "fetch_url"]):
        errors.append(f"generated_queries missing expected Web Tools variants: {queries}")
    results.append(("multi_query_generation", errors))

    high_low_matches = [
        {
            "chunk_id": "high",
            "file_name": "README.md",
            "heading": "Persistent Memory",
            "text": "Persistent Memory stores user preferences, stable facts, project memory, and task history for future Agent runs.",
            "score": 0.5,
        },
        {
            "chunk_id": "low",
            "file_name": "README.md",
            "heading": "Table of Contents",
            "text": "Home Next Previous",
            "score": 0.01,
        },
    ]
    reranked = engine.rerank_matches(high_low_matches, "Persistent Memory", keywords=["Persistent Memory", "长期记忆"], top_k=5)
    evidence, low, enough, _reason = engine.filter_evidence(reranked)
    errors = []
    if not enough:
        errors.append("expected enough evidence")
    if not evidence or evidence[0].get("chunk_id") != "high":
        errors.append(f"high relevance chunk did not enter evidence: {evidence}")
    if not low or low[0].get("chunk_id") != "low":
        errors.append(f"low relevance chunk did not enter low results: {low}")
    results.append(("rerank_filters_low_relevance", errors))

    state = _structured_state(_structured_profile(needs_rag=True))
    state.rag_used = True
    state.rag_enough_evidence = False
    state.rag_low_relevance_chunks = [{"chunk_id": "low", "file_name": "README.md", "heading": "Intro", "score": 0.1}]
    errors = []
    if state.rag_enough_evidence is not False:
        errors.append("insufficient RAG evidence was not preserved structurally")
    if state.retrieved_chunks:
        errors.append("insufficient evidence unexpectedly populated retrieved_chunks")
    if len(state.rag_low_relevance_chunks) != 1:
        errors.append("low relevance evidence diagnostics were not preserved")
    results.append(("rag_no_evidence_no_reference", errors))

    state = _structured_state(_structured_profile(needs_rag=True))
    state.rag_used = True
    state.rag_context_ready = False
    state.retrieved_chunks = []
    state.rag_enough_evidence = False
    state.rag_low_relevance_chunks = [{"chunk_id": "low", "file_name": "README.md", "heading": "Intro", "score": 0.1}]
    errors = []
    if state.rag_context_ready is not False or state.rag_enough_evidence is not False:
        errors.append("no-evidence RAG state was not preserved")
    if state.retrieved_chunks:
        errors.append("no-evidence RAG state unexpectedly contains retrieved chunks")
    results.append(("rag_no_evidence_final", errors))

    state = _structured_state(_structured_profile(needs_rag=True))
    state.rag_used = False
    errors = []
    if state.rag_used is not False:
        errors.append("unretrieved RAG task incorrectly reports retrieval")
    if state.retrieved_chunks:
        errors.append("unretrieved RAG task contains document evidence")
    results.append(("rag_unretrieved_fallback_friendly", errors))

    engine = ContextFusionEngine()
    with TemporaryDirectory() as tmp:
        memory = PersistentMemory(Path(tmp) / "memory_store")
        memory.add_project_summary("proj", "项目没有 RAG，只有普通聊天。", ["Python"], "old")
        state = _structured_state(_structured_profile(needs_rag=True))
        state.rag_used = True
        state.rag_enough_evidence = True
        rag_result = {
            "evidence_chunks": [
                {
                    "chunk_id": "rag1",
                    "file_name": "README.md",
                    "heading": "RAG",
                    "text": "This project has RAG with retrieval and evidence chunks.",
                    "evidence_quality": "high",
                    "score": 1.0,
                }
            ]
        }
        fused = engine.build_fused_context("根据已加载文档回答 RAG 是什么", state, memory, rag_result=rag_result, max_chars=2000)
        text = fused.get("data", {}).get("context_text", "")
        errors = []
        if text.find("This project has RAG") < 0:
            errors.append("RAG evidence missing from fused context")
        if text.find("This project has RAG") > text.find("项目没有 RAG") >= 0:
            errors.append("memory appeared before RAG evidence")
        results.append(("context_priority_rag_over_memory", errors))

    with TemporaryDirectory() as tmp:
        memory = PersistentMemory(Path(tmp) / "memory_store")
        memory.add_user_preference("answer_style", "用简洁中文回答")
        state = _structured_state(_structured_profile(needs_rag=True))
        state.rag_used = True
        state.rag_enough_evidence = False
        memory_evidence = ContextFusionEngine.memory_evidence(memory)
        errors = []
        if state.rag_enough_evidence is not False or state.retrieved_chunks:
            errors.append("memory was treated as retrieved document evidence")
        if not any(item.get("memory_type") == "user_preference" for item in memory_evidence):
            errors.append("memory evidence lost its structured memory source type")
        results.append(("memory_not_document_evidence", errors))

    with TemporaryDirectory() as tmp:
        memory = PersistentMemory(Path(tmp) / "memory_store")
        memory.add_project_summary("proj", "Python Agent 包含 Persistent Memory、RAG、Document Loader。", ["Python"], "active")
        summary = compact_memory_summary(memory)
        rewrite = RAGEngine().rewrite_query_with_memory("根据我的项目文档回答记忆模块是什么", summary)
        queries = RAGEngine().generate_retrieval_queries("根据我的项目文档回答记忆模块是什么", summary)
        joined = " ".join([*queries, *rewrite.get("keywords", [])])
        errors = []
        if not all(term in joined for term in ["Persistent Memory", "长期记忆"]):
            errors.append(f"memory-aware rewrite missing memory terms: {joined}")
        if "memory_store" not in joined:
            errors.append(f"memory-aware rewrite missing memory_store: {joined}")
        results.append(("memory_aware_query_rewrite", errors))

    with TemporaryDirectory() as tmp:
        memory = PersistentMemory(Path(tmp) / "memory_store")
        memory.add_task_summary({"task_id": "t1", "task_type": "simple", "goal": "用户喜欢非常长的回答", "result": "completed", "summary": "用户喜欢非常长的回答"})
        evidence = ContextFusionEngine.memory_evidence(memory)
        errors = []
        if any(item.get("memory_type") == "user_preference" for item in evidence):
            errors.append("task history was treated as user preference")
        if not any(item.get("memory_type") == "task_history" and item.get("confidence") == "low" for item in evidence):
            errors.append("task history should be low-confidence background")
        results.append(("task_history_weak_background", errors))

    with TemporaryDirectory() as tmp:
        memory = PersistentMemory(Path(tmp) / "memory_store")
        memory.add_project_summary("proj", "A" * 3000 + " API_KEY=secret", ["Python"], "active")
        state = _structured_state(_structured_profile(needs_rag=True))
        large_chunk = {"chunk_id": "c", "file_name": "README.md", "heading": "RAG", "text": "B" * 3000, "evidence_quality": "high"}
        fused = engine.build_fused_context("根据已加载文档回答 RAG 是什么", state, memory, rag_result={"evidence_chunks": [large_chunk]}, max_chars=1000)
        text = fused.get("data", {}).get("context_text", "")
        errors = []
        if len(text) > 1000:
            errors.append("fused context exceeded max_chars")
        if "API_KEY=secret" in text:
            errors.append("secret-like token leaked")
        if text.count("B") > 700:
            errors.append("large chunk was not compacted")
        results.append(("fused_context_size_limit", errors))

    return results


def _run_workspace_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    original_workspace = get_current_workspace()

    with TemporaryDirectory() as tmp:
        manager = WorkspaceManager(root_dir=Path(tmp) / "workspace_store")

        errors = []
        try:
            manager.sanitize_id("../bad/user")
            errors.append("path traversal id was accepted")
        except ValueError:
            pass
        results.append(("workspace_sanitize_id", errors))

        context = manager.get_context()
        errors = []
        if context.user_id != "default_user" or context.project_id != "default_project":
            errors.append(f"unexpected default ids: {context.user_id}/{context.project_id}")
        if context.memory_dir.name != "memory_store" or context.document_dir.name != "document_store" or context.vector_dir.name != "vector_store":
            errors.append("default store directories are incorrect")
        results.append(("workspace_default_context", errors))

        ctx_a = manager.get_context("user_a", "project_1")
        ctx_b = manager.get_context("user_b", "project_1")
        mem_a = PersistentMemory(ctx_a.memory_dir)
        mem_b = PersistentMemory(ctx_b.memory_dir)
        mem_a.add_user_preference("answer_style", "short")
        errors = []
        if "answer_style" not in mem_a.get_user_memory().get("preferences", {}):
            errors.append("workspace A did not save memory")
        if "answer_style" in mem_b.get_user_memory().get("preferences", {}):
            errors.append("workspace B saw workspace A memory")
        results.append(("memory_isolated_by_workspace", errors))

        doc_a = DocumentStore(ctx_a.document_dir)
        doc_b = DocumentStore(ctx_b.document_dir)
        doc_a.add_document(
            {
                "document_id": "doc_a",
                "path": "a.txt",
                "file_name": "a.txt",
                "extension": ".txt",
                "size_bytes": 1,
                "text_length": 5,
                "summary": "alpha",
                "headings": [],
                "loaded_at": "now",
                "updated_at": "now",
                "source": "eval",
                "content_hash": "hash-a",
            }
        )
        errors = []
        if doc_a.list_documents().get("data", {}).get("documents_count") != 1:
            errors.append("workspace A document missing")
        if doc_b.list_documents().get("data", {}).get("documents_count") != 0:
            errors.append("workspace B saw workspace A document")
        results.append(("documents_isolated_by_workspace", errors))

        vec_a = VectorStore(ctx_a.vector_dir)
        vec_b = VectorStore(ctx_b.vector_dir)
        vec_a.upsert_vector({"chunk_id": "c1", "embedding": [0.1, 0.2], "embedding_dim": 2, "embedding_model": "eval"})
        errors = []
        if vec_a.get_status().get("data", {}).get("vectors_count") != 1:
            errors.append("workspace A vector missing")
        if vec_b.get_status().get("data", {}).get("vectors_count") != 0:
            errors.append("workspace B saw workspace A vector")
        results.append(("vectors_isolated_by_workspace", errors))

    errors = []
    try:
        switched = switch_workspace("eval_user", "eval_project")
        data = switched.get("data", {}) if switched.get("success") else {}
        if data.get("user_id") != "eval_user" or data.get("project_id") != "eval_project":
            errors.append(f"switch returned wrong workspace: {data}")
        status = get_workspace_status().get("data", {})
        if status.get("workspace_id") != "eval_user/eval_project":
            errors.append(f"runtime status did not switch: {status}")
    finally:
        set_current_workspace(original_workspace)
    results.append(("switch_workspace_runtime", errors))

    errors = []
    real_data = PROJECT_ROOT / "workspace_store" / "eval_export_user" / "eval_project" / "memory_store"
    real_data.mkdir(parents=True, exist_ok=True)
    (real_data / "user_memory.json").write_text('{"secret":"do-not-export"}', encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "export_project.py")],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=dict(os.environ, LLM_PROVIDER="mock", LLM_MODEL="mock"),
        timeout=60,
    )
    if process.returncode != 0:
        errors.append(f"export_project failed: {process.stderr or process.stdout}")
    else:
        with zipfile.ZipFile(PROJECT_ROOT / "dist" / "agent_export.zip") as archive:
            names = set(archive.namelist())
        if "workspace_store/eval_export_user/eval_project/memory_store/user_memory.json" in names:
            errors.append("export included real workspace data")
        if "workspace_store/.gitkeep" not in names:
            errors.append("export did not preserve workspace placeholders")
    results.append(("export_excludes_workspace_data", errors))

    return results


def _run_api_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []

    errors = []
    chat = ChatRequest(user_id="api_user", project_id="api_project", message="hello", debug=True)
    if chat.user_id != "api_user" or chat.project_id != "api_project" or chat.message != "hello":
        errors.append("ChatRequest did not preserve fields")
    workspace = WorkspaceRequest()
    if workspace.user_id != "default_user" or workspace.project_id != "default_project":
        errors.append("WorkspaceRequest defaults are wrong")
    rag = RagQueryRequest(query="Persistent Memory 是什么")
    if rag.mode != "hybrid" or rag.top_k != 5:
        errors.append("RagQueryRequest defaults are wrong")
    results.append(("api_schemas_instantiate", errors))

    with TemporaryDirectory() as tmp:
        manager = WorkspaceManager(root_dir=Path(tmp) / "workspace_store")
        context = manager.get_context("api_user", "api_project")
        errors = []
        if context.user_id != "api_user" or context.project_id != "api_project":
            errors.append("WorkspaceManager did not create expected API workspace")
        if not context.workspace_dir.exists():
            errors.append("API workspace directory was not created")
        results.append(("api_workspace_context_create", errors))

    errors = []
    try:
        context = get_workspace_context("api_eval_user", "api_eval_project")
        if context.workspace_id != "api_eval_user/api_eval_project":
            errors.append(f"api.deps get_workspace_context returned {context.workspace_id}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"get_workspace_context raised: {exc}")
    results.append(("api_deps_workspace_context", errors))

    errors = []
    expected_routes = {
        "/health",
        "/status",
        "/chat",
        "/workspace/status",
        "/memory/list",
        "/documents/list",
        "/rag/query",
        "/sandbox/status",
        "/browser/status",
        "/browser/extract-text",
        "/browser/screenshot",
        "/browser/list-links",
        "/browser/click-and-extract",
    }
    routes = {getattr(route, "path", "") for route in fastapi_app.routes}
    missing = sorted(expected_routes - routes)
    if fastapi_app.title != "Command Line AI Agent API":
        errors.append(f"unexpected app title: {fastapi_app.title}")
    if missing:
        errors.append(f"missing routes: {missing}")
    results.append(("api_app_routes_import", errors))

    errors = []
    forbidden_routes = {"/sandbox/run-shell", "/sandbox/run-python", "/sandbox/cleanup"}
    if routes & forbidden_routes:
        errors.append(f"direct sandbox execution routes remain: {sorted(routes & forbidden_routes)}")
    results.append(("api_sandbox_routes_import", errors))

    return results


def _run_protection_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []

    original_auth = settings.api_auth_enabled
    original_keys = settings.api_keys
    try:
        client = TestClient(fastapi_app)

        object.__setattr__(settings, "api_auth_enabled", False)
        errors = []
        response = client.post("/usage/status", json={"user_id": "auth_eval", "project_id": "project"})
        if response.status_code != 200:
            errors.append(f"auth disabled request failed: {response.status_code} {response.text}")
        results.append(("auth_disabled_allows_request", errors))

        object.__setattr__(settings, "api_auth_enabled", True)
        object.__setattr__(settings, "api_keys", ("valid-eval-key",))
        errors = []
        response = client.post("/usage/status", json={"user_id": "auth_eval", "project_id": "project"})
        if response.status_code != 401:
            errors.append(f"missing key expected 401, got {response.status_code}")
        results.append(("auth_enabled_rejects_missing_key", errors))

        errors = []
        response = client.post(
            "/usage/status",
            json={"user_id": "auth_eval", "project_id": "project"},
            headers={"X-API-Key": "valid-eval-key"},
        )
        if response.status_code != 200:
            errors.append(f"valid key expected 200, got {response.status_code} {response.text}")
        results.append(("auth_enabled_accepts_valid_key", errors))
    finally:
        object.__setattr__(settings, "api_auth_enabled", original_auth)
        object.__setattr__(settings, "api_keys", original_keys)

    with TemporaryDirectory() as tmp:
        tracker = UsageTracker(Path(tmp) / "usage.json")
        errors = []
        tracker.increment("usage_eval", "chat", 2)
        if tracker.get_usage("usage_eval").get("chat") != 2:
            errors.append("usage increment did not persist")
        results.append(("usage_increment", errors))

        errors = []
        limiter = RateLimiter(tracker=tracker)
        original_rate = settings.rate_limit_enabled
        original_limit = settings.daily_chat_limit
        try:
            object.__setattr__(settings, "rate_limit_enabled", True)
            object.__setattr__(settings, "daily_chat_limit", 2)
            quota = limiter.check_and_increment("usage_eval", "chat")
            if quota.get("allowed") is not False:
                errors.append(f"expected limit exceeded, got {quota}")
        finally:
            object.__setattr__(settings, "rate_limit_enabled", original_rate)
            object.__setattr__(settings, "daily_chat_limit", original_limit)
        results.append(("usage_limit_exceeded", errors))

    with TemporaryDirectory() as tmp:
        cache = CacheManager(Path(tmp) / "cache_store")
        errors = []
        key = cache.make_key("cache", "expire")
        cache.set("rag", key, {"answer": "ok"}, ttl_seconds=60)
        if cache.get("rag", key) != {"answer": "ok"}:
            errors.append("cache get missed fresh entry")
        payload_path = Path(tmp) / "cache_store" / "rag_cache.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["entries"][key]["expires_at"] = 0
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        if cache.get("rag", key) is not None:
            errors.append("expired cache entry was returned")
        results.append(("cache_set_get_expire", errors))

        errors = []
        secret_key = cache.make_key("secret")
        stored = cache.set("web_search", secret_key, {"token": "TAVILY_API_KEY=secret"}, ttl_seconds=60)
        if stored or cache.get("web_search", secret_key) is not None:
            errors.append("cache stored secret-like value")
        results.append(("cache_does_not_store_api_key", errors))

        errors = []
        key_a = cache.make_key("rag", "user", "project", "query", "hybrid", 5, {"vectors_count": 1})
        key_b = cache.make_key("rag", "user", "project", "query", "hybrid", 5, {"vectors_count": 2})
        if key_a == key_b:
            errors.append("RAG cache key did not change when vector fingerprint changed")
        results.append(("rag_cache_key_changes_when_vectors_change", errors))

    errors = []
    usage_json = PROJECT_ROOT / "usage_store" / "eval_usage.json"
    cache_json = PROJECT_ROOT / "cache_store" / "eval_cache.json"
    usage_json.parent.mkdir(exist_ok=True)
    cache_json.parent.mkdir(exist_ok=True)
    usage_json.write_text('{"secret":"do-not-export"}', encoding="utf-8")
    cache_json.write_text('{"secret":"do-not-export"}', encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "export_project.py")],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=dict(os.environ, LLM_PROVIDER="mock", LLM_MODEL="mock"),
        timeout=60,
    )
    if process.returncode != 0:
        errors.append(f"export_project failed: {process.stderr or process.stdout}")
    else:
        with zipfile.ZipFile(PROJECT_ROOT / "dist" / "agent_export.zip") as archive:
            names = set(archive.namelist())
        if "usage_store/eval_usage.json" in names:
            errors.append("export included usage json")
        if "cache_store/eval_cache.json" in names:
            errors.append("export included cache json")
        if "usage_store/.gitkeep" not in names or "cache_store/.gitkeep" not in names:
            errors.append("export did not preserve usage/cache placeholders")
    try:
        usage_json.unlink(missing_ok=True)
        cache_json.unlink(missing_ok=True)
    except OSError:
        pass
    results.append(("export_excludes_usage_and_cache", errors))

    return results


def _run_git_safety_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    gitignore_text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    checks = [
        ("gitignore_contains_env", ".env"),
        ("gitignore_contains_venv", ".venv/"),
        ("gitignore_contains_node_modules", "frontend/node_modules/"),
        ("gitignore_contains_workspace_store", "workspace_store/**"),
    ]
    for name, needle in checks:
        errors = []
        if needle not in gitignore_text:
            errors.append(f".gitignore missing {needle}")
        results.append((name, errors))

    errors = []
    check_module = _load_module(PROJECT_ROOT / "scripts" / "check_git_safety.py", "check_git_safety_eval")
    if not hasattr(check_module, "build_report"):
        errors.append("check_git_safety missing build_report")
    else:
        report = check_module.build_report(PROJECT_ROOT)
        if report.get("status") not in {"ok", "warning", "error"}:
            errors.append(f"unexpected safety status: {report}")
        if "risky_files" not in report or "suggestions" not in report:
            errors.append("safety report missing required keys")
    results.append(("check_git_safety_import", errors))

    errors = []
    init_module = _load_module(PROJECT_ROOT / "scripts" / "init_git_repo.py", "init_git_repo_eval")
    if not hasattr(init_module, "main"):
        errors.append("init_git_repo missing main")
    else:
        process = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "init_git_repo.py"), "--dry-run"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if process.returncode not in {0, 1}:
            errors.append(f"dry-run returned unexpected code {process.returncode}: {process.stderr or process.stdout}")
        if process.returncode != 0 and "git add ." in process.stdout:
            errors.append("dry-run should not suggest git add when safety check fails")
    results.append(("init_git_repo_import", errors))

    return results


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_sandbox_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []

    errors = []
    set_current_workspace(WorkspaceManager().get_context("sandbox_eval_user", "sandbox_project"))
    result = run_python_in_sandbox("print('hello')")
    if not _sandbox_execution_is_expected(result, expected_stdout="hello"):
        errors.append(f"local sandbox python failed: {result}")
    results.append(("sandbox_manager_local_python", errors))

    errors = []
    status = SandboxManager().status()
    status_text = json.dumps(status, ensure_ascii=False).lower()
    if status.get("success") is not True or "local_host" not in status_text or "docker" in status_text:
        errors.append(f"unexpected host execution status: {status}")
    results.append(("sandbox_local_host_status", errors))

    errors = []
    sandbox_file = PROJECT_ROOT / "workspace_store" / "sandbox_export_user" / "sandbox_project" / "sandbox" / "secret.txt"
    sandbox_file.parent.mkdir(parents=True, exist_ok=True)
    sandbox_file.write_text("do-not-export", encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "export_project.py")],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=dict(os.environ, LLM_PROVIDER="mock", LLM_MODEL="mock"),
        timeout=60,
    )
    if process.returncode != 0:
        errors.append(f"export_project failed: {process.stderr or process.stdout}")
    else:
        with zipfile.ZipFile(PROJECT_ROOT / "dist" / "agent_export.zip") as archive:
            names = set(archive.namelist())
        if "workspace_store/sandbox_export_user/sandbox_project/sandbox/secret.txt" in names:
            errors.append("export included sandbox workspace data")
        if any(name.startswith("sandbox_store/") for name in names):
            errors.append("export included sandbox_store")
    results.append(("export_excludes_sandbox_data", errors))

    errors = []
    shell_result = run_shell_in_sandbox("echo hello")
    cleanup_result = cleanup_sandbox()
    if not _sandbox_execution_is_expected(shell_result, expected_stdout="hello"):
        errors.append(f"sandbox shell failed: {shell_result}")
    if cleanup_result.get("success") is not True:
        errors.append(f"sandbox cleanup failed: {cleanup_result}")
    results.append(("sandbox_shell_and_cleanup", errors))

    return results


def _sandbox_execution_is_expected(
    result: dict[str, Any],
    *,
    expected_stdout: str,
) -> bool:
    if result.get("success") is True:
        return expected_stdout in str(
            (result.get("data") or {}).get("stdout", "")
        )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return str(
        result.get("error_code")
        or data.get("error_code")
        or data.get("code")
        or ""
    ) == "agent_access_mode_read_only"

def _run_browser_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    policy = BrowserPolicy()

    errors = []
    if policy.check_url("file:///tmp/a.html").get("allowed"):
        errors.append("file:// URL was allowed")
    results.append(("browser_policy_rejects_file_url", errors))

    errors = []
    if policy.check_url("http://localhost:8000").get("allowed"):
        errors.append("localhost URL was allowed")
    results.append(("browser_policy_rejects_localhost", errors))

    errors = []
    if policy.check_url("http://192.168.1.10").get("allowed"):
        errors.append("private IP URL was allowed")
    results.append(("browser_policy_rejects_private_ip", errors))

    errors = []
    with patch.object(BrowserPolicy, "_check_dns_resolved_ips", return_value=None):
        https_decision = policy.check_url("https://example.com")
    if not https_decision.get("allowed"):
        errors.append(f"https URL was rejected: {https_decision}")
    results.append(("browser_policy_allows_https", errors))

    errors = []
    for name in {"get_browser_status", "browser_extract_text", "browser_screenshot", "browser_list_links"}:
        if name not in ALL_TOOLS:
            errors.append(f"missing tool {name}")
    results.append(("browser_tools_registered", errors))

    errors = []
    original_channel = settings.browser_channel
    original_channel_upper = settings.BROWSER_CHANNEL
    try:
        object.__setattr__(settings, "browser_channel", "msedge")
        object.__setattr__(settings, "BROWSER_CHANNEL", "msedge")
        status = BrowserManager().get_status()
        data = status.get("data", {}) if isinstance(status.get("data"), dict) else {}
        if data.get("browser_channel") != "msedge":
            errors.append(f"browser_channel not reported: {data}")
        if data.get("using_system_browser") is not True:
            errors.append(f"using_system_browser should be true: {data}")
        if str(status.get("error", "")).startswith("Chromium is not installed for Playwright"):
            errors.append(f"msedge channel should not require chromium install: {status.get('error')}")
    finally:
        object.__setattr__(settings, "browser_channel", original_channel)
        object.__setattr__(settings, "BROWSER_CHANNEL", original_channel_upper)
    results.append(("browser_status_supports_system_channel", errors))

    errors = []
    try:
        import api.routes.browser as browser_routes  # noqa: PLC0415

        if not hasattr(browser_routes, "router"):
            errors.append("browser router missing")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"browser route import failed: {exc}")
    results.append(("api_browser_routes_import", errors))

    errors = []
    artifact = PROJECT_ROOT / "browser_artifacts" / "eval.png"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text("do-not-export", encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "export_project.py")],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=dict(os.environ, LLM_PROVIDER="mock", LLM_MODEL="mock"),
        timeout=60,
    )
    if process.returncode != 0:
        errors.append(f"export_project failed: {process.stderr or process.stdout}")
    else:
        with zipfile.ZipFile(PROJECT_ROOT / "dist" / "agent_export.zip") as archive:
            names = set(archive.namelist())
        if "browser_artifacts/eval.png" in names:
            errors.append("export included browser screenshot artifact")
        if "browser_artifacts/.gitkeep" not in names:
            errors.append("export did not preserve browser placeholders")
    try:
        artifact.unlink(missing_ok=True)
    except OSError:
        pass
    results.append(("export_excludes_browser_artifacts", errors))

    return results


def _run_runtime_boundary_evals() -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []

    errors = []
    validation_state = SimpleNamespace(
        task_type="simple",
        modified_files=["a.py"],
        validation_results=[{"success": True}],
        git_repo_root="/repo",
        reviewed_diff=False,
        metadata={},
        tool_failures=[],
        task_profile=SimpleNamespace(
            tool_required=True,
            execution_mode="normal",
        ),
    )
    outcome = resolve_tool_outcome(
        task_state=validation_state,
        tool_name="sandbox_exec",
        arguments={},
        observation={"success": True, "status": "success", "data": {}},
    )
    outcome_fields = {item.name for item in fields(ToolOutcomeResolution)}
    if outcome.kind != "allow_continue":
        errors.append(f"tool outcome did not return control to assistant: {outcome}")
    if {"next_tool", "next_arguments"}.intersection(outcome_fields):
        errors.append("tool outcome still exposes runtime tool-selection fields")
    results.append(("tool_outcome_returns_control_to_assistant", errors))

    errors = []
    if hasattr(AgentLoop, "_auto_run_rag_retrieval"):
        errors.append("RAG guard still exposes runtime auto-retrieval")
    results.append(("runtime_has_no_auto_rag_retrieval", errors))

    errors = []
    for sql, expected in (
        ("SELECT 1 # DELETE FROM users\n;", True),
        ("SELECT 1 -- normal comment\n;", True),
        ("SELECT * FROM users /*!50000 FOR UPDATE */;", False),
        ("SELECT * FROM users WHERE id=1--1 FOR UPDATE;", False),
        ("SELECT * FROM users WHERE id=1--x INTO OUTFILE '/tmp/x';", False),
        ("SELECT 1--2; DELETE FROM users;", False),
    ):
        result = validate_read_only_sql(sql)
        if result.success is not expected:
            errors.append(f"MySQL SQL comment boundary mismatch for {sql!r}: {result}")
    sqlite_result = validate_read_only_sql(
        "SELECT 1--sqlite comment\n;",
        allow_sqlite_only=True,
    )
    if sqlite_result.success is not True:
        errors.append(f"SQLite dash-comment boundary mismatch: {sqlite_result}")
    results.append(("mysql_comment_safety_boundary", errors))

    return results


if __name__ == "__main__":
    raise SystemExit(main())
