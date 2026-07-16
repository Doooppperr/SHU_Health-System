from __future__ import annotations

import atexit
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


_INDICATOR_QUERY_TERMS = {
    "BMI": ("bmi", "身体质量指数", "体质指数", "超重", "体重"),
    "FBG": ("fbg", "空腹血糖", "血糖", "葡萄糖"),
    "TC": ("tc", "总胆固醇", "胆固醇"),
    "TG": ("tg", "甘油三酯"),
    "HDL": ("hdl", "高密度脂蛋白"),
    "LDL": ("ldl", "低密度脂蛋白"),
    "ALT": ("alt", "丙氨酸氨基转移酶", "谷丙转氨酶"),
    "AST": ("ast", "天门冬氨酸氨基转移酶", "谷草转氨酶"),
    "UA": ("ua", "尿酸", "痛风"),
    "CREA": ("crea", "肌酐", "肾功能", "慢性肾"),
}


def _query_indicator_codes(query: str) -> set[str]:
    normalized = query.lower()

    def contains(term: str) -> bool:
        if term.isascii() and term.isalnum():
            return (
                re.search(
                    rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized
                )
                is not None
            )
        return term in normalized

    return {
        code
        for code, terms in _INDICATOR_QUERY_TERMS.items()
        if any(contains(term) for term in terms)
    }


@dataclass(frozen=True)
class KnowledgeHit:
    source_id: str
    chunk_id: str
    title: str
    publisher: str
    canonical_url: str
    version: str
    locator: str
    content: str
    score: float
    indicator_codes: tuple[str, ...] = ()


@dataclass
class RetrievalResult:
    status: str
    hits: list[KnowledgeHit] = field(default_factory=list)
    duration_ms: int = 0
    candidate_count: int = 0
    error_code: str | None = None

    @property
    def used(self) -> bool:
        return bool(self.hits)

    def log_payload(self) -> dict:
        return {
            "status": self.status,
            "duration_ms": self.duration_ms,
            "candidate_count": self.candidate_count,
            "source_ids": [item.source_id for item in self.hits],
            "scores": [round(item.score, 4) for item in self.hits],
            "error_code": self.error_code,
        }


class KnowledgeRetriever:
    def retrieve(
        self,
        query: str,
        *,
        audience: str,
        indicator_codes: Iterable[str] = (),
        limit: int | None = None,
    ) -> RetrievalResult:
        raise NotImplementedError


class DisabledKnowledgeRetriever(KnowledgeRetriever):
    def __init__(self, status="disabled"):
        self.status = status

    def retrieve(self, query, *, audience, indicator_codes=(), limit=None):
        del query, audience, indicator_codes, limit
        return RetrievalResult(status=self.status)


class MockKnowledgeRetriever(KnowledgeRetriever):
    """Network-free retriever used by tests unless a test injects another fake."""

    def retrieve(self, query, *, audience, indicator_codes=(), limit=None):
        del query, audience, indicator_codes, limit
        return RetrievalResult(status="no_match")


class QdrantKnowledgeRetriever(KnowledgeRetriever):
    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._client = None
        self._embedder = None

    def _initialize(self):
        if self._client is not None and self._embedder is not None:
            return
        with self._lock:
            if self._client is not None and self._embedder is not None:
                return
            from fastembed import TextEmbedding
            from qdrant_client import QdrantClient

            model_cache = Path(self.config["RAG_MODEL_CACHE_PATH"])
            model_cache.mkdir(parents=True, exist_ok=True)
            self._embedder = TextEmbedding(
                model_name=self.config["RAG_EMBEDDING_MODEL"],
                cache_dir=str(model_cache),
                threads=self.config["RAG_EMBEDDING_THREADS"],
                enable_cpu_mem_arena=False,
                local_files_only=True,
            )
            url = self.config.get("RAG_QDRANT_URL")
            if url:
                self._client = QdrantClient(
                    url=url,
                    api_key=self.config.get("RAG_QDRANT_API_KEY") or None,
                    timeout=5,
                )
            else:
                storage = Path(self.config["RAG_STORAGE_PATH"])
                storage.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(storage))
            atexit.register(self.close)

    def close(self):
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def retrieve(self, query, *, audience, indicator_codes=(), limit=None):
        started = time.monotonic()
        if not query.strip():
            return RetrievalResult(status="no_match")
        try:
            self._initialize()
            from qdrant_client import models

            vector = list(self._embedder.query_embed(query))[0].tolist()
            audience_values = ["public"]
            if audience == "authenticated":
                audience_values.append("authenticated")
            audience_filter = models.Filter(
                should=[
                    models.FieldCondition(
                        key="audience", match=models.MatchValue(value=value)
                    )
                    for value in audience_values
                ],
            )
            top_k = int(self.config.get("RAG_TOP_K", 8))
            response = self._client.query_points(
                collection_name=self.config["RAG_COLLECTION_ALIAS"],
                query=vector,
                query_filter=audience_filter,
                limit=top_k,
                with_payload=True,
                score_threshold=float(self.config.get("RAG_MIN_SCORE", 0.35)),
            )
            requested_codes = {
                str(item).upper() for item in indicator_codes
            } | _query_indicator_codes(query)
            candidates = []
            for point in response.points:
                payload = point.payload or {}
                codes = tuple(str(item).upper() for item in payload.get("indicator_codes") or [])
                boost = 0.08 if requested_codes.intersection(codes) else 0.0
                if payload.get("publisher") == "国家卫生健康委员会":
                    boost += 0.015
                candidates.append(
                    KnowledgeHit(
                        source_id=str(payload.get("source_id") or ""),
                        chunk_id=str(payload.get("chunk_id") or point.id),
                        title=str(payload.get("title") or "未命名来源"),
                        publisher=str(payload.get("publisher") or ""),
                        canonical_url=str(payload.get("canonical_url") or ""),
                        version=str(payload.get("version") or ""),
                        locator=str(payload.get("locator") or ""),
                        content=str(payload.get("content") or ""),
                        score=float(point.score) + boost,
                        indicator_codes=codes,
                    )
                )
            candidates.sort(key=lambda item: item.score, reverse=True)
            selected = []
            per_source = {}
            result_limit = limit or int(self.config.get("RAG_CHAT_CONTEXT_K", 4))
            for item in candidates:
                if not item.source_id or not item.content:
                    continue
                if per_source.get(item.source_id, 0) >= 2:
                    continue
                selected.append(item)
                per_source[item.source_id] = per_source.get(item.source_id, 0) + 1
                if len(selected) >= result_limit:
                    break
            return RetrievalResult(
                status="hit" if selected else "no_match",
                hits=selected,
                duration_ms=round((time.monotonic() - started) * 1000),
                candidate_count=len(candidates),
            )
        except Exception as exc:  # Retrieval must not take down the AI path.
            return RetrievalResult(
                status="unavailable",
                duration_ms=round((time.monotonic() - started) * 1000),
                error_code=type(exc).__name__,
            )


def get_knowledge_retriever(app):
    existing = app.extensions.get("knowledge_retriever")
    if existing is not None:
        return existing
    if not app.config.get("RAG_ENABLED"):
        retriever = DisabledKnowledgeRetriever()
    elif app.config.get("RAG_USE_MOCK"):
        retriever = MockKnowledgeRetriever()
    else:
        retriever = QdrantKnowledgeRetriever(app.config)
    app.extensions["knowledge_retriever"] = retriever
    return retriever


def format_knowledge_context(result: RetrievalResult, *, max_chars: int) -> str:
    if not result.hits:
        return ""
    items = []
    used = 0
    for index, hit in enumerate(result.hits, start=1):
        payload = {
            "source_id": f"K{index}",
            "stable_source_id": hit.source_id,
            "title": hit.title,
            "publisher": hit.publisher,
            "version": hit.version,
            "locator": hit.locator,
            "content": hit.content,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if items and used + len(encoded) > max_chars:
            break
        items.append(payload)
        used += len(encoded)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def allowed_grounding_ids(result: RetrievalResult) -> tuple[str, ...]:
    return tuple(f"K{index}" for index, _item in enumerate(result.hits, start=1))
