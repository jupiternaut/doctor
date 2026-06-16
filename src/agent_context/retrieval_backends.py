from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


HASH_VECTOR_DIMENSIONS = 384
HASH_VECTOR_BACKEND_ID = "hash-vector-lite"
FASTEMBED_BACKEND_ID = "fastembed"
DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
EXACT_SCAN_BACKEND_ID = "exact-json-scan"
HNSWLIB_BACKEND_ID = "hnswlib"
DEFAULT_FASTEMBED_BATCH_SIZE = 64
DEFAULT_HASH_BATCH_SIZE = 2048

STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "哪些",
    "文件",
    "适合",
    "进入",
    "个人",
    "什么",
    "如何",
    "怎么",
}
QUERY_EXPANSIONS = {
    "个人助手": ["agent", "assistant", "mcp", "skill", "workflow", "context", "助手"],
    "长期记忆": ["memory", "remember", "skill", "workflow", "context", "handoff", "permalink", "记忆", "沉淀", "上下文", "工作流"],
    "开源往事": ["开源", "黑盒", "gnu", "linux", "unix", "gpl", "微软", "sun", "ibm", "自由软件", "闭源"],
    "开源": ["gnu", "linux", "unix", "gpl", "微软", "sun", "ibm", "自由软件", "闭源", "开放源代码"],
    "热上下文": ["context", "pack", "handoff", "上下文"],
    "冷索引": ["index", "search", "retrieval", "rag", "sqlite"],
    "rag": ["retrieval", "index", "search", "context"],
}


@dataclass(frozen=True)
class RetrievalConfig:
    embedding_backend: str = HASH_VECTOR_BACKEND_ID
    ann_backend: str = EXACT_SCAN_BACKEND_ID
    rerank_backend: str = "none"


class EmbeddingBackend(Protocol):
    backend_id: str
    dimensions: int
    model_name: str | None
    storage_format: str

    def embed_document(self, text: str) -> str: ...

    def embed_query(self, text: str) -> str: ...

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]: ...


class HashVectorLiteBackend:
    backend_id = HASH_VECTOR_BACKEND_ID
    dimensions = HASH_VECTOR_DIMENSIONS
    model_name = None
    storage_format = "json_sparse_pairs"

    def embed_document(self, text: str) -> str:
        return encode_vector(vector_for(text))

    def embed_query(self, text: str) -> str:
        return encode_vector(query_vector_for(text))

    def embed_documents(self, texts: list[str]) -> list[str]:
        return [self.embed_document(text) for text in texts]

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
        query_vector = query_vector_for(query)
        if not query_vector:
            return {}
        terms = set(query_terms(query))
        scored = []
        for source_chunk_id, row in rows.items():
            haystack = " ".join([row.get("path") or "", row.get("relative_path") or "", row.get("text") or ""]).lower()
            overlap = sum(1 for term in terms if term in haystack)
            if overlap == 0:
                continue
            score = cosine(query_vector, decode_vector(row["embedding_json"]))
            if score > 0:
                scored.append((score * min(1.0, overlap / 3.0), source_chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[:limit]
        if not top:
            return {}
        max_score = max(score for score, _ in top) or 1.0
        return {source_chunk_id: score / max_score for score, source_chunk_id in top}


class FastEmbedDenseBackend:
    backend_id = FASTEMBED_BACKEND_ID
    storage_format = "json_dense_float32"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.environ.get("AGENT_CONTEXT_FASTEMBED_MODEL", DEFAULT_FASTEMBED_MODEL)
        self._model: Any | None = None
        self._dimensions: int | None = None

    @property
    def dimensions(self) -> int:
        if self._dimensions is not None:
            return self._dimensions
        # The default FastEmbed model is 384-dimensional. If a different model is
        # selected, this is updated after the first embedding is generated.
        return HASH_VECTOR_DIMENSIONS

    def embed_document(self, text: str) -> str:
        return self.embed_documents([text])[0]

    def embed_query(self, text: str) -> str:
        return encode_dense_vector(self._embed_one(text))

    def embed_documents(self, texts: list[str]) -> list[str]:
        return [encode_dense_vector(vector) for vector in self._embed_many(texts)]

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
        query_vector = self._embed_one(query)
        if not query_vector:
            return {}
        scored = []
        for source_chunk_id, row in rows.items():
            score = dense_cosine(query_vector, decode_dense_vector(row["embedding_json"]))
            if score > 0:
                scored.append((score, source_chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[:limit]
        if not top:
            return {}
        max_score = max(score for score, _ in top) or 1.0
        return {source_chunk_id: score / max_score for score, source_chunk_id in top}

    def _embed_one(self, text: str) -> list[float]:
        return self._embed_many([text])[0]

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        vectors = []
        for vector in model.embed([text or "" for text in texts]):
            values = [float(value) for value in vector]
            self._dimensions = len(values)
            vectors.append(normalize_dense(values))
        return vectors

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed backend requested but package is not installed. "
                "Install it with `uv pip install fastembed` or use the default hash-vector-lite backend."
            ) from exc
        self._model = TextEmbedding(model_name=self.model_name)
        return self._model


def default_retrieval_config() -> RetrievalConfig:
    backend = os.environ.get("AGENT_CONTEXT_VECTOR_BACKEND") or os.environ.get("AGENT_CONTEXT_EMBEDDING_BACKEND")
    ann_backend = os.environ.get("AGENT_CONTEXT_ANN_BACKEND") or EXACT_SCAN_BACKEND_ID
    rerank_backend = os.environ.get("AGENT_CONTEXT_RERANK_BACKEND") or "none"
    return RetrievalConfig(
        embedding_backend=backend or HASH_VECTOR_BACKEND_ID,
        ann_backend=ann_backend,
        rerank_backend=rerank_backend,
    )


def get_embedding_backend(config: RetrievalConfig | None = None) -> EmbeddingBackend:
    config = config or default_retrieval_config()
    if config.embedding_backend == HASH_VECTOR_BACKEND_ID:
        return HashVectorLiteBackend()
    if config.embedding_backend == FASTEMBED_BACKEND_ID:
        return FastEmbedDenseBackend()
    raise ValueError(f"unsupported embedding backend: {config.embedding_backend}")


def embed_documents(backend: EmbeddingBackend, texts: list[str]) -> list[str]:
    batch_embed = getattr(backend, "embed_documents", None)
    if callable(batch_embed):
        embeddings = list(batch_embed(texts))
    else:
        embeddings = [backend.embed_document(text) for text in texts]
    if len(embeddings) != len(texts):
        raise RuntimeError(f"{backend.backend_id} returned {len(embeddings)} embeddings for {len(texts)} texts")
    return embeddings


class AnnSearchUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class HnswlibCachePaths:
    index_path: Path
    metadata_path: Path
    fingerprint: str


def score_dense_rows_with_hnswlib(rows: dict[str, dict], query_embedding_json: str, limit: int) -> dict[str, float]:
    scores, _cache_status = score_dense_rows_with_hnswlib_cached(rows, query_embedding_json, limit)
    return scores


def score_dense_rows_with_hnswlib_cached(
    rows: dict[str, dict],
    query_embedding_json: str,
    limit: int,
    *,
    cache_paths: HnswlibCachePaths | None = None,
) -> tuple[dict[str, float], str]:
    try:
        import hnswlib
    except ImportError as exc:
        raise AnnSearchUnavailable("hnswlib is not installed") from exc

    query_vector = decode_dense_vector(query_embedding_json)
    if not query_vector:
        return {}, "skipped_empty_query"
    vectors = []
    labels = []
    source_ids = []
    dimensions = len(query_vector)
    for numeric_id, (source_chunk_id, row) in enumerate(rows.items()):
        vector = decode_dense_vector(row.get("embedding_json") or "[]")
        if len(vector) != dimensions:
            continue
        vectors.append(vector)
        labels.append(numeric_id)
        source_ids.append(source_chunk_id)
    if not vectors:
        return {}, "skipped_no_compatible_vectors"

    index, cache_status = _load_or_build_hnswlib_index(
        hnswlib,
        vectors,
        labels,
        dimensions,
        cache_paths,
        source_ids,
    )
    if hasattr(index, "set_ef"):
        index.set_ef(max(50, min(len(vectors), limit * 8)))
    raw_labels, raw_distances = index.knn_query(_ann_array([query_vector]), k=min(max(1, limit), len(vectors)))
    ranked = []
    for label, distance in zip(_first_ann_row(raw_labels), _first_ann_row(raw_distances)):
        label_index = int(label)
        if 0 <= label_index < len(source_ids):
            score = max(0.0, 1.0 - float(distance))
            if score > 0:
                ranked.append((score, source_ids[label_index]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    top = ranked[:limit]
    if not top:
        return {}, cache_status
    max_score = max(score for score, _ in top) or 1.0
    return {source_chunk_id: score / max_score for score, source_chunk_id in top}, cache_status


def _load_or_build_hnswlib_index(
    hnswlib: Any,
    vectors: list[list[float]],
    labels: list[int],
    dimensions: int,
    cache_paths: HnswlibCachePaths | None,
    source_ids: list[str],
) -> tuple[Any, str]:
    if cache_paths and hnswlib_cache_valid(cache_paths, dimensions, source_ids):
        index = hnswlib.Index(space="cosine", dim=dimensions)
        index.load_index(str(cache_paths.index_path), max_elements=len(vectors))
        return index, "loaded"

    index = hnswlib.Index(space="cosine", dim=dimensions)
    index.init_index(max_elements=len(vectors), ef_construction=100, M=16)
    index.add_items(_ann_array(vectors), _ann_array(labels, dtype="int64"))
    if cache_paths:
        write_hnswlib_cache_metadata(cache_paths, dimensions, source_ids)
        index.save_index(str(cache_paths.index_path))
        return index, "rebuilt"
    return index, "memory"


def hnswlib_cache_valid(cache_paths: HnswlibCachePaths, dimensions: int, source_ids: list[str]) -> bool:
    if not cache_paths.index_path.exists() or not cache_paths.metadata_path.exists():
        return False
    try:
        metadata = json.loads(cache_paths.metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        metadata.get("fingerprint") == cache_paths.fingerprint
        and metadata.get("dimensions") == dimensions
        and metadata.get("source_ids") == source_ids
    )


def write_hnswlib_cache_metadata(cache_paths: HnswlibCachePaths, dimensions: int, source_ids: list[str]) -> None:
    cache_paths.index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ann_backend": HNSWLIB_BACKEND_ID,
        "fingerprint": cache_paths.fingerprint,
        "dimensions": dimensions,
        "source_ids": source_ids,
    }
    cache_paths.metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ann_array(values: Any, *, dtype: str = "float32") -> Any:
    try:
        import numpy as np
    except ImportError:
        return values
    return np.asarray(values, dtype=dtype)


def _first_ann_row(values: Any) -> list[Any]:
    if hasattr(values, "tolist"):
        values = values.tolist()
    if values and isinstance(values[0], list):
        return values[0]
    return list(values)


def embedding_batch_size_for(backend: EmbeddingBackend) -> int:
    configured = os.environ.get("AGENT_CONTEXT_EMBED_BATCH_SIZE")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            return DEFAULT_FASTEMBED_BATCH_SIZE if backend.backend_id == FASTEMBED_BACKEND_ID else DEFAULT_HASH_BATCH_SIZE
    if backend.backend_id == FASTEMBED_BACKEND_ID:
        return DEFAULT_FASTEMBED_BATCH_SIZE
    return DEFAULT_HASH_BATCH_SIZE


def backend_meta(config: RetrievalConfig | None = None) -> dict[str, str]:
    config = config or default_retrieval_config()
    backend = get_embedding_backend(config)
    return {
        "embedding": f"local-{backend.backend_id}-{backend.dimensions}",
        "embedding_backend": backend.backend_id,
        "embedding_dimensions": str(backend.dimensions),
        "embedding_model": backend.model_name or "",
        "embedding_storage_format": backend.storage_format,
        "ann_backend": config.ann_backend,
        "rerank_backend": config.rerank_backend,
    }


def lexical_terms(text: str) -> list[str]:
    lower = text.lower()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", lower):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            terms.append(token)
            terms.extend(cjk_ngrams(token, 2))
            terms.extend(cjk_ngrams(token, 3))
        else:
            for part in token.split("_"):
                if len(part) >= 2 and part not in STOP_TERMS:
                    terms.append(part)
    deduped = []
    seen = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def query_terms(text: str) -> list[str]:
    terms = lexical_terms(text)
    lower = text.lower()
    for trigger, expansions in QUERY_EXPANSIONS.items():
        if trigger.lower() in lower:
            terms.extend(expansions)
    deduped = []
    seen = set()
    for term in terms:
        if term not in STOP_TERMS and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def cjk_ngrams(token: str, size: int) -> list[str]:
    if len(token) < size:
        return []
    return [token[index : index + size] for index in range(0, len(token) - size + 1)]


def vector_for(text: str) -> dict[int, float]:
    values: dict[int, float] = {}
    for term in lexical_terms(text):
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % HASH_VECTOR_DIMENSIONS
        values[index] = values.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in values.values()))
    if norm == 0:
        return {}
    return {index: value / norm for index, value in values.items()}


def query_vector_for(text: str) -> dict[int, float]:
    return vector_from_terms(query_terms(text))


def vector_from_terms(terms: list[str]) -> dict[int, float]:
    values: dict[int, float] = {}
    for term in terms:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % HASH_VECTOR_DIMENSIONS
        values[index] = values.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in values.values()))
    if norm == 0:
        return {}
    return {index: value / norm for index, value in values.items()}


def encode_vector(vector: dict[int, float]) -> str:
    pairs = [[index, round(value, 8)] for index, value in sorted(vector.items())]
    return json.dumps(pairs, separators=(",", ":"))


def decode_vector(payload: str) -> dict[int, float]:
    return {int(index): float(value) for index, value in json.loads(payload or "[]")}


def cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def encode_dense_vector(vector: list[float]) -> str:
    return json.dumps([round(value, 8) for value in vector], separators=(",", ":"))


def decode_dense_vector(payload: str) -> list[float]:
    values = json.loads(payload or "[]")
    if not values:
        return []
    if isinstance(values[0], list):
        # Sparse hash-vector indexes are not compatible with dense backends.
        return []
    return [float(value) for value in values]


def normalize_dense(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return []
    return [value / norm for value in vector]


def dense_cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(left_value * right_value for left_value, right_value in zip(left, right))
