"""RAG quality helpers over local chunks and vectors."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from core.document_store import DocumentStore
from core.embedding_provider import EmbeddingProvider
from core.workspace_runtime import get_document_dir
from tools.chunk_tools import search_document_chunks
from tools.vector_tools import hybrid_search_chunks, semantic_search_chunks


RagMode = Literal["semantic", "keyword", "hybrid"]

SYNONYMS: dict[str, list[str]] = {
    "persistent memory": ["Persistent Memory", "长期记忆", "持久记忆", "user_memory project_memory task_history"],
    "document loader": ["Document Loader", "文档加载", "文档读取"],
    "chunking": ["Chunking", "文档切片", "chunk"],
    "vector store": ["Vector Store", "向量存储", "vector"],
    "rag": ["RAG", "检索增强生成"],
    "web tools": ["Web Tools", "网页搜索", "web_search", "fetch_url"],
}

WRAPPER_PHRASES = (
    "根据已加载文档回答",
    "根据文档回答",
    "从已加载文档里查找",
    "从已加载文档里查",
    "从文档里找",
    "帮我查找",
    "按文档内容回答",
    "不要联网，基于本地文档",
    "不要联网, 基于本地文档",
    "基于本地文档",
    "根据资料库回答",
    "根据项目文档回答",
    "根据 README 文档回答",
    "根据 README 回答",
    "根据README回答",
    "回答",
    "是什么",
)


@dataclass
class RetrievalResult:
    """Result returned by the RAG retrieval layer."""

    query: str
    mode: str
    degraded: bool = False
    degraded_reason: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    context_text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    rewritten_query: str = ""
    keywords: list[str] = field(default_factory=list)
    generated_queries: list[str] = field(default_factory=list)
    evidence_chunks: list[dict[str, Any]] = field(default_factory=list)
    low_relevance_chunks: list[dict[str, Any]] = field(default_factory=list)
    enough_evidence: bool = False
    evidence_reason: str = ""
    top_score: float = 0.0
    rerank_applied: bool = False
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QueryRewriteResult:
    """Structured query rewrite output for RAG pipeline tracing."""

    original_query: str
    rewritten_query: str
    keywords: list[str]
    generated_queries: list[str]
    reason: str
    used_memory: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalPlan:
    """Retrieval execution plan built before running local retrieval."""

    query: str
    mode: str
    top_k: int
    generated_queries: list[str]
    use_semantic: bool
    use_keyword: bool
    use_hybrid: bool
    fallback_to_keyword: bool = True
    evidence_threshold: float = 0.35
    max_context_chars: int = 4000
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidencePackage:
    """Evidence-only context and citations for grounded RAG answers."""

    query: str
    rewritten_query: str
    evidence_chunks: list[dict[str, Any]]
    low_relevance_chunks: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    context_text: str
    enough_evidence: bool
    evidence_reason: str
    top_score: float
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def citation_ids(self) -> set[str]:
        return {str(item.get("chunk_id", "")) for item in self.citations if item.get("chunk_id")}

    def has_enough_evidence(self) -> bool:
        return bool(self.enough_evidence and self.evidence_chunks)


@dataclass
class CitationGuardResult:
    """Lightweight citation validation result."""

    valid: bool
    issues: list[str]
    missing_citations: list[str]
    invalid_citations: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LowEvidenceDecision:
    """Decision for low-evidence RAG behavior."""

    should_answer: bool
    should_fallback: bool
    reason: str
    user_message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RAGRunResult:
    """Full closed-loop RAG pipeline result."""

    success: bool
    query: str
    mode: str
    rewrite: QueryRewriteResult
    retrieval_plan: RetrievalPlan
    evidence_package: EvidencePackage
    low_evidence_decision: LowEvidenceDecision
    citation_guard: CitationGuardResult
    final_context: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "query": self.query,
            "mode": self.mode,
            "rewrite": self.rewrite.to_dict(),
            "retrieval_plan": self.retrieval_plan.to_dict(),
            "evidence_package": self.evidence_package.to_dict(),
            "low_evidence_decision": self.low_evidence_decision.to_dict(),
            "citation_guard": self.citation_guard.to_dict(),
            "final_context": self.final_context,
            "metadata": dict(self.metadata),
        }


class RAGEngine:
    """Build queries, retrieve chunks, rerank, and format grounded context."""

    def __init__(self, document_store: DocumentStore | None = None) -> None:
        self.document_store = document_store or DocumentStore(document_dir=get_document_dir())

    def build_rag_query(self, user_input: str, task_state: Any | None = None) -> str:
        """Extract a retrieval query from the user request with simple rules."""

        rewritten = self.rewrite_query(user_input).get("rewritten_query", "")
        if rewritten:
            return str(rewritten)
        if task_state is not None and getattr(task_state, "user_goal", ""):
            return str(task_state.user_goal)
        return str(user_input or "")

    def rewrite_query(self, user_input: str) -> dict[str, Any]:
        """Rewrite an instruction-like RAG request into retrieval keywords."""

        original = " ".join(str(user_input or "").split())
        core = original
        for phrase in WRAPPER_PHRASES:
            core = core.replace(phrase, " ")
        core = re.sub(r"^(请|帮我|麻烦你|你能不能|能否)\s*", "", core).strip()
        core = re.sub(r"\s+", " ", core).strip(" ：:，,。?？")

        keywords = _extract_terms(core)
        lowered = core.lower()
        for key, values in SYNONYMS.items():
            if key in lowered or any(value.lower() in lowered for value in values):
                keywords.extend(values)

        keywords = _dedupe([item for item in keywords if item])
        rewritten = " ".join(keywords[:6]) if keywords else core or original
        return {
            "original_query": original,
            "rewritten_query": rewritten,
            "keywords": keywords[:10],
            "reason": "Removed instruction wrapper and kept core concept.",
        }

    def rewrite_query_with_memory(self, user_input: str, persistent_memory_context: str | None = None) -> dict[str, Any]:
        """Rewrite a RAG query with a compact memory summary as background."""

        result = self.rewrite_query(user_input)
        memory_text = str(persistent_memory_context or "")
        additions: list[str] = []
        lowered = f"{user_input}\n{memory_text}".lower()
        if any(term in lowered for term in ["memory", "记忆", "記憶"]):
            additions.extend(["Persistent Memory", "长期记忆", "task history"])
        if "rag" in lowered:
            additions.extend(["RAG", "检索增强生成", "rag_query", "Document Loader"])
        if "agent" in lowered and "python" in lowered:
            additions.extend(["Python Agent", "TaskState", "Tool Calling"])
        keywords = _dedupe([*result.get("keywords", []), *additions])[:10]
        rewritten = " ".join(keywords[:6]) if keywords else str(result.get("rewritten_query", ""))
        return {
            **result,
            "rewritten_query": rewritten,
            "keywords": keywords,
            "memory_keywords_used": additions[:6],
            "reason": "Removed instruction wrapper and used compact project memory for query expansion.",
        }

    def rewrite_query_structured(
        self,
        user_input: str,
        persistent_memory_context: str | None = None,
    ) -> QueryRewriteResult:
        """Return a structured query rewrite while keeping dict rewrites compatible."""

        raw = self.rewrite_query_with_memory(user_input, persistent_memory_context) if persistent_memory_context else self.rewrite_query(user_input)
        generated_queries = self.generate_retrieval_queries(user_input, persistent_memory_context)
        return QueryRewriteResult(
            original_query=str(raw.get("original_query") or user_input or ""),
            rewritten_query=str(raw.get("rewritten_query") or user_input or ""),
            keywords=[str(item) for item in raw.get("keywords", []) if item],
            generated_queries=generated_queries,
            reason=str(raw.get("reason") or ""),
            used_memory=bool(persistent_memory_context),
            metadata={
                "memory_keywords_used": [str(item) for item in raw.get("memory_keywords_used", []) if item],
            },
        )

    def build_retrieval_plan(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 5,
        persistent_memory_context: str | None = None,
    ) -> RetrievalPlan:
        """Build a safe retrieval plan without executing retrieval."""

        safe_mode = mode if mode in {"semantic", "keyword", "hybrid"} else "hybrid"
        safe_top_k = _safe_limit(top_k, default=5, maximum=20)
        rewrite = self.rewrite_query_structured(query, persistent_memory_context)
        embedding_status = EmbeddingProvider().get_status().get("data", {})
        embedding_available = isinstance(embedding_status, dict) and embedding_status.get("available") is True
        return RetrievalPlan(
            query=query,
            mode=safe_mode,
            top_k=safe_top_k,
            generated_queries=list(rewrite.generated_queries),
            use_semantic=safe_mode in {"semantic", "hybrid"} and embedding_available,
            use_keyword=safe_mode in {"keyword", "hybrid"} or not embedding_available,
            use_hybrid=safe_mode == "hybrid",
            fallback_to_keyword=True,
            evidence_threshold=0.35,
            max_context_chars=4000,
            metadata={
                "embedding_available": embedding_available,
                "requested_mode": mode,
                "rewritten_query": rewrite.rewritten_query,
            },
        )

    def generate_retrieval_queries(self, user_input: str, persistent_memory_context: str | None = None) -> list[str]:
        """Generate a small set of query variants for hybrid retrieval."""

        rewrite = self.rewrite_query_with_memory(user_input, persistent_memory_context) if persistent_memory_context else self.rewrite_query(user_input)
        original_core = self.build_rag_query(user_input)
        candidates = [original_core]
        for keyword in rewrite.get("keywords", []):
            candidates.append(str(keyword))
        candidates.append(str(rewrite.get("rewritten_query", "")))
        return _dedupe([query.strip() for query in candidates if query and query.strip()])[:4]

    def retrieve(self, query: str, mode: RagMode = "hybrid", top_k: int = 5, persistent_memory_context: str | None = None) -> RetrievalResult:
        """Retrieve local chunks with quality controls."""

        safe_mode = mode if mode in {"semantic", "keyword", "hybrid"} else "hybrid"
        safe_top_k = _safe_limit(top_k, default=5, maximum=20)
        rewrite = self.rewrite_query_with_memory(query, persistent_memory_context) if persistent_memory_context else self.rewrite_query(query)
        rewritten_query = str(rewrite.get("rewritten_query", ""))
        keywords = [str(item) for item in rewrite.get("keywords", [])]
        generated_queries = self.generate_retrieval_queries(query, persistent_memory_context) if safe_mode == "hybrid" else [rewritten_query or query]
        degraded = False
        degraded_reason = ""
        raw_matches: list[dict[str, Any]] = []

        if safe_mode == "semantic":
            result = semantic_search_chunks(query=rewritten_query or query, top_k=safe_top_k)
            if not result.get("success"):
                degraded = True
                degraded_reason = str(result.get("error", "Semantic retrieval failed."))
            raw_matches = _clean_matches(result.get("data", {}).get("matches", []) if result.get("success") else [])
            _tag_matches(raw_matches, rewritten_query or query)
        elif safe_mode == "keyword":
            result = search_document_chunks(keyword=rewritten_query or query, limit=safe_top_k)
            if not result.get("success"):
                degraded_reason = str(result.get("error", ""))
            raw_matches = _clean_matches(result.get("data", {}).get("chunks", []) if result.get("success") else [])
            _tag_matches(raw_matches, rewritten_query or query)
        else:
            embedding_status = EmbeddingProvider().get_status().get("data", {})
            embedding_available = isinstance(embedding_status, dict) and embedding_status.get("available") is True
            for retrieval_query in generated_queries:
                result = hybrid_search_chunks(query=retrieval_query, top_k=safe_top_k)
                if not result.get("success"):
                    keyword = search_document_chunks(keyword=retrieval_query, limit=safe_top_k)
                    degraded = True
                    degraded_reason = str(result.get("error", "Hybrid retrieval failed; keyword fallback used."))
                    batch = _clean_matches(keyword.get("data", {}).get("chunks", []) if keyword.get("success") else [])
                else:
                    data = result.get("data", {})
                    batch = _clean_matches(data.get("matches", []) if isinstance(data, dict) else [])
                    semantic_available = bool(data.get("semantic_available")) if isinstance(data, dict) else embedding_available
                    if not semantic_available:
                        degraded = True
                        degraded_reason = str(data.get("fallback_reason", "")) if isinstance(data, dict) else ""
                _tag_matches(batch, retrieval_query)
                raw_matches.extend(batch)
            if degraded and not degraded_reason:
                degraded_reason = "Embedding unavailable; keyword results were used."

        deduped = _merge_matches(raw_matches)
        reranked = self.rerank_matches(deduped, rewritten_query or query, keywords=keywords, top_k=max(safe_top_k, 5))
        evidence_chunks, low_relevance_chunks, enough, evidence_reason = self.filter_evidence(reranked)
        context_text = self.build_context(evidence_chunks)
        citations = self.format_citations(evidence_chunks)
        top_score = max([float(item.get("rerank_score", 0.0) or 0.0) for item in reranked] or [0.0])
        diagnostics = {
            "original_query": query,
            "rewritten_query": rewritten_query,
            "generated_queries": generated_queries,
            "mode": safe_mode,
            "degraded": degraded,
            "degraded_reason": degraded_reason,
            "raw_matches_count": len(raw_matches),
            "deduped_matches_count": len(deduped),
            "evidence_count": len(evidence_chunks),
            "low_relevance_count": len(low_relevance_chunks),
            "rerank_applied": True,
            "memory_context_used": bool(persistent_memory_context),
            "memory_keywords_used": rewrite.get("memory_keywords_used", []),
        }
        return RetrievalResult(
            query=query,
            mode=safe_mode,
            degraded=degraded,
            degraded_reason=degraded_reason,
            matches=reranked,
            context_text=context_text,
            citations=citations,
            rewritten_query=rewritten_query,
            keywords=keywords,
            generated_queries=generated_queries,
            evidence_chunks=evidence_chunks,
            low_relevance_chunks=low_relevance_chunks,
            enough_evidence=enough,
            evidence_reason=evidence_reason,
            top_score=top_score,
            rerank_applied=True,
            retrieval_diagnostics=diagnostics,
        )

    def build_evidence_package(self, result: RetrievalResult) -> EvidencePackage:
        """Build an evidence-only package from retrieval output."""

        evidence_chunks = [_strip_embedding(dict(item)) for item in result.evidence_chunks if isinstance(item, dict)]
        evidence_ids = {str(item.get("chunk_id", "")) for item in evidence_chunks if item.get("chunk_id")}
        citations = [
            _strip_embedding(dict(item))
            for item in result.citations
            if isinstance(item, dict) and str(item.get("chunk_id", "")) in evidence_ids
        ]
        context_text = self.build_context(evidence_chunks)
        return EvidencePackage(
            query=result.query,
            rewritten_query=result.rewritten_query,
            evidence_chunks=evidence_chunks,
            low_relevance_chunks=[_strip_embedding(dict(item)) for item in result.low_relevance_chunks if isinstance(item, dict)],
            citations=citations,
            context_text=context_text,
            enough_evidence=bool(result.enough_evidence and evidence_chunks),
            evidence_reason=result.evidence_reason,
            top_score=result.top_score,
            diagnostics=dict(result.retrieval_diagnostics),
        )

    def guard_citations(self, answer: str, evidence_package: EvidencePackage) -> CitationGuardResult:
        """Validate citation claims with lightweight rules."""

        issues: list[str] = []
        missing_citations: list[str] = []
        invalid_citations: list[str] = []
        citation_ids = evidence_package.citation_ids()
        if not evidence_package.enough_evidence:
            issues.append("low evidence")
        answer_text = answer or ""
        claim_markers = ("根据文档", "根据资料", "引用", "来源", "证据", "based on the document", "citation", "source")
        if any(marker.lower() in answer_text.lower() for marker in claim_markers) and not citation_ids:
            missing_citations.append("answer claims document evidence but no citations are available")
        referenced_ids = set(re.findall(r"(?:chunk_id=|\[chunk_id=)([A-Za-z0-9_.:-]+)", answer_text))
        invalid_citations = sorted(chunk_id for chunk_id in referenced_ids if chunk_id not in citation_ids)
        if missing_citations:
            issues.append("missing citations")
        if invalid_citations:
            issues.append("invalid citations")
        return CitationGuardResult(
            valid=not issues,
            issues=issues,
            missing_citations=missing_citations,
            invalid_citations=invalid_citations,
            metadata={
                "citation_count": len(citation_ids),
                "referenced_chunk_ids": sorted(referenced_ids),
            },
        )

    @staticmethod
    def decide_low_evidence(evidence_package: EvidencePackage) -> LowEvidenceDecision:
        """Decide whether the RAG pipeline can answer from local evidence."""

        if evidence_package.enough_evidence and evidence_package.evidence_chunks:
            return LowEvidenceDecision(
                should_answer=True,
                should_fallback=False,
                reason="Evidence is sufficient.",
                user_message="已在已加载文档中找到足够相关的证据片段。",
                metadata={"evidence_count": len(evidence_package.evidence_chunks)},
            )
        return LowEvidenceDecision(
            should_answer=False,
            should_fallback=True,
            reason=evidence_package.evidence_reason or "Evidence is insufficient.",
            user_message="我没有在已加载文档中找到足够相关的内容，不能基于文档可靠回答。",
            metadata={"evidence_count": len(evidence_package.evidence_chunks)},
        )

    def run_pipeline(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 5,
        persistent_memory_context: str | None = None,
    ) -> RAGRunResult:
        """Run the full local RAG v1 pipeline and return traceable output."""

        rewrite = self.rewrite_query_structured(query, persistent_memory_context)
        retrieval_plan = self.build_retrieval_plan(query, mode=mode, top_k=top_k, persistent_memory_context=persistent_memory_context)
        retrieval = self.retrieve(query=query, mode=retrieval_plan.mode, top_k=retrieval_plan.top_k, persistent_memory_context=persistent_memory_context)  # type: ignore[arg-type]
        evidence_package = self.build_evidence_package(retrieval)
        low_evidence_decision = self.decide_low_evidence(evidence_package)
        citation_guard = self.guard_citations("", evidence_package)
        pipeline_status = _pipeline_status(retrieval, evidence_package)
        return RAGRunResult(
            success=True,
            query=query,
            mode=retrieval_plan.mode,
            rewrite=rewrite,
            retrieval_plan=retrieval_plan,
            evidence_package=evidence_package,
            low_evidence_decision=low_evidence_decision,
            citation_guard=citation_guard,
            final_context=evidence_package.context_text,
            metadata={
                "pipeline_status": pipeline_status,
                "degraded": retrieval.degraded,
                "degraded_reason": retrieval.degraded_reason,
                "retrieval_diagnostics": dict(retrieval.retrieval_diagnostics),
                "evidence_count": len(evidence_package.evidence_chunks),
                "low_relevance_count": len(evidence_package.low_relevance_chunks),
                "citation_count": len(evidence_package.citations),
                "enough_evidence": evidence_package.enough_evidence,
                "final_status": pipeline_status,
            },
        )

    def rerank_matches(
        self,
        matches: list[dict[str, Any]],
        query: str,
        keywords: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rule-based reranker for local chunks."""

        terms = _dedupe([*(keywords or []), *_extract_terms(query)])
        reranked = []
        for match in matches:
            item = {key: value for key, value in match.items() if key != "embedding"}
            text = _ensure_text(item, self.document_store)
            heading = str(item.get("heading", ""))
            file_name = str(item.get("file_name", ""))
            base_score = _safe_float(item.get("score"), 0.0)
            if base_score > 1.0:
                base_score = min(base_score / 5.0, 1.0)
            matched_keywords = _matched_terms(terms, " ".join([text, heading, file_name]))
            matched_queries = [str(value) for value in item.get("matched_queries", []) if value]
            score = base_score
            reason = [f"base={base_score:.2f}"]
            if heading and _matched_terms(terms, heading):
                score += 0.35
                reason.append("heading_match")
            if matched_keywords:
                score += min(0.15 * len(matched_keywords), 0.45)
                reason.append("keyword_match")
            if file_name and _matched_terms(terms, file_name):
                score += 0.15
                reason.append("file_match")
            if len(set(matched_queries)) > 1:
                score += min(0.12 * len(set(matched_queries)), 0.36)
                reason.append("multi_query")
            if _looks_low_quality(text, heading):
                score -= 0.35
                reason.append("low_quality_penalty")
            if base_score < 0.05 and not matched_keywords:
                score -= 0.25
                reason.append("weak_score_no_keyword")
            score = round(max(score, 0.0), 6)
            item["text"] = text
            item["rerank_score"] = score
            item["evidence_quality"] = _quality(score, matched_keywords)
            item["matched_keywords"] = matched_keywords
            item["matched_queries"] = _dedupe(matched_queries)
            item["rerank_reason"] = ", ".join(reason)
            reranked.append(item)
        reranked.sort(key=lambda value: -float(value.get("rerank_score", 0.0) or 0.0))
        return reranked[: _safe_limit(top_k, default=5, maximum=20)]

    @staticmethod
    def filter_evidence(
        matches: list[dict[str, Any]],
        threshold: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str]:
        """Split supportable evidence from low-relevance results."""

        min_score = 0.35 if threshold is None else threshold
        evidence = []
        low = []
        for match in matches:
            quality = str(match.get("evidence_quality", "low"))
            score = _safe_float(match.get("rerank_score"), 0.0)
            if quality in {"high", "medium"} and score >= min_score:
                evidence.append(match)
            else:
                low.append(_metadata_preview(match))
        evidence = evidence[:5]
        top_score = max([_safe_float(item.get("rerank_score"), 0.0) for item in matches] or [0.0])
        if evidence:
            return evidence, low, True, f"Found {len(evidence)} evidence chunk(s)."
        if matches and top_score < min_score:
            return [], low, False, f"Top rerank score {top_score:.2f} is below threshold {min_score:.2f}."
        return [], low, False, "No retrieved chunks were relevant enough to support an answer."

    def build_context(self, matches: list[dict[str, Any]], max_chars: int = 4000) -> str:
        """Format evidence chunks for LLM context without embeddings."""

        lines: list[str] = []
        seen: set[str] = set()
        for match in matches:
            if not isinstance(match, dict):
                continue
            chunk_id = str(match.get("chunk_id", ""))
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            text = _ensure_text(match, self.document_store)
            if not text:
                continue
            block = (
                f"[chunk_id={chunk_id} | file={match.get('file_name', '')} | heading={match.get('heading', '') or 'none'}]\n"
                f"{text.strip()}"
            )
            candidate = "\n\n".join([*lines, block]) if lines else block
            if len(candidate) > max_chars:
                if not lines:
                    lines.append(block[:max_chars].rstrip())
                    break
                remaining = max_chars - len("\n\n".join(lines)) - 2
                if remaining > 120:
                    lines.append(block[:remaining].rstrip())
                break
            lines.append(block)
        return "\n\n".join(lines)[:max_chars]

    @staticmethod
    def has_enough_evidence(matches: list[dict[str, Any]]) -> bool:
        """Return True when at least one match is medium or high quality."""

        return any(str(match.get("evidence_quality", "")) in {"high", "medium"} for match in matches if isinstance(match, dict))

    @staticmethod
    def format_citations(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return compact citation metadata."""

        citations = []
        seen: set[str] = set()
        for match in matches:
            if not isinstance(match, dict):
                continue
            chunk_id = str(match.get("chunk_id", ""))
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            citation: dict[str, Any] = {
                "file_name": match.get("file_name", ""),
                "chunk_id": chunk_id,
                "heading": match.get("heading", ""),
                "evidence_quality": match.get("evidence_quality", ""),
            }
            score = match.get("rerank_score", match.get("score"))
            if score is not None:
                citation["score"] = score
            citations.append(citation)
        return citations


def _clean_matches(items: Any) -> list[dict[str, Any]]:
    cleaned = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        cleaned.append({key: value for key, value in item.items() if key != "embedding"})
    return cleaned


def _strip_embedding(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "embedding"}


def _pipeline_status(result: RetrievalResult, evidence_package: EvidencePackage) -> str:
    if evidence_package.enough_evidence and result.degraded:
        return "degraded_retrieval"
    if evidence_package.enough_evidence:
        return "ready_to_answer"
    if result.degraded and not result.matches:
        return "retrieval_failed"
    return "low_evidence_fallback"


def _tag_matches(matches: list[dict[str, Any]], query: str) -> None:
    for match in matches:
        match["matched_queries"] = _dedupe([*match.get("matched_queries", []), query])


def _merge_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for match in matches:
        chunk_id = str(match.get("chunk_id", ""))
        if not chunk_id:
            continue
        current = merged.setdefault(chunk_id, dict(match))
        current["matched_queries"] = _dedupe([*current.get("matched_queries", []), *match.get("matched_queries", [])])
        current["score"] = max(_safe_float(current.get("score"), 0.0), _safe_float(match.get("score"), 0.0))
        if not _match_text(current) and _match_text(match):
            current["text"] = _match_text(match)
    return list(merged.values())


def _ensure_text(match: dict[str, Any], store: DocumentStore) -> str:
    text = _match_text(match)
    if text:
        return text
    chunk = store.get_chunk(str(match.get("chunk_id", "")))
    data = chunk.get("data", {}) if chunk.get("success") else {}
    return str(data.get("text", "")) if isinstance(data, dict) else ""


def _match_text(match: dict[str, Any]) -> str:
    return str(match.get("text") or match.get("chunk_preview") or match.get("text_preview") or "")


def _extract_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-z][A-Za-z0-9_ -]{2,}[A-Za-z0-9_]|\b[A-Za-z_]{2,}\b|[\u4e00-\u9fff]{2,}", text or "")
    cleaned = []
    for term in terms:
        value = " ".join(term.split()).strip()
        if value and value not in {"根据", "文档", "回答", "是什么", "查找", "内容"}:
            cleaned.append(value)
    return cleaned


def _matched_terms(terms: list[str], text: str) -> list[str]:
    lowered = (text or "").lower()
    return _dedupe([term for term in terms if term and term.lower() in lowered])


def _looks_low_quality(text: str, heading: str) -> bool:
    compact = " ".join((text or "").split())
    if len(compact) < 40:
        return True
    lowered = compact.lower()
    nav_markers = ["table of contents", "目录", "上一页", "下一页", "导航", "copyright"]
    if any(marker in lowered for marker in nav_markers) and len(compact) < 200:
        return True
    return not heading and len(compact) < 80


def _quality(score: float, matched_keywords: list[str]) -> str:
    if score >= 0.8 and matched_keywords:
        return "high"
    if score >= 0.35 and matched_keywords:
        return "medium"
    return "low"


def _metadata_preview(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": match.get("chunk_id", ""),
        "document_id": match.get("document_id", ""),
        "file_name": match.get("file_name", ""),
        "path": match.get("path", ""),
        "heading": match.get("heading", ""),
        "score": match.get("rerank_score", match.get("score", 0)),
        "evidence_quality": match.get("evidence_quality", "low"),
        "matched_keywords": match.get("matched_keywords", []),
        "matched_queries": match.get("matched_queries", []),
        "chunk_preview": _match_text(match)[:240],
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_limit(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
