from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from agent_context.retrieval_backends import (
    FASTEMBED_BACKEND_ID,
    HNSWLIB_BACKEND_ID,
    FastEmbedDenseBackend,
    HashVectorLiteBackend,
    HnswlibCachePaths,
    RetrievalConfig,
    backend_meta,
    decode_dense_vector,
    default_retrieval_config,
    encode_dense_vector,
    embed_documents,
    get_embedding_backend,
    score_dense_rows_with_hnswlib,
    score_dense_rows_with_hnswlib_cached,
)


class FakeTextEmbedding:
    instances: list["FakeTextEmbedding"] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.embed_calls: list[list[str]] = []
        self.instances.append(self)

    def embed(self, texts: list[str]):
        self.embed_calls.append(list(texts))
        for text in texts:
            lower = text.lower()
            if "rank" in lower or "recommend" in lower:
                yield [1.0, 0.0, 0.0]
            elif "video" in lower:
                yield [0.0, 1.0, 0.0]
            else:
                yield [0.0, 0.0, 1.0]


def install_fake_fastembed(monkeypatch) -> None:
    FakeTextEmbedding.instances = []
    module = types.ModuleType("fastembed")
    module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)


def install_fake_hnswlib(monkeypatch) -> None:
    module = types.ModuleType("hnswlib")

    class FakeIndex:
        def __init__(self, space: str, dim: int) -> None:
            self.space = space
            self.dim = dim
            self.vectors: list[list[float]] = []
            self.labels: list[int] = []

        def init_index(self, max_elements: int, ef_construction: int, M: int) -> None:
            self.max_elements = max_elements
            self.ef_construction = ef_construction
            self.m = M

        def add_items(self, vectors, labels) -> None:
            self.vectors = vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)
            self.labels = labels.tolist() if hasattr(labels, "tolist") else list(labels)

        def set_ef(self, value: int) -> None:
            self.ef = value

        def save_index(self, path: str) -> None:
            Path(path).write_text(
                json.dumps({"vectors": self.vectors, "labels": self.labels}),
                encoding="utf-8",
            )

        def load_index(self, path: str, max_elements: int) -> None:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.vectors = payload["vectors"]
            self.labels = payload["labels"]
            self.max_elements = max_elements

        def knn_query(self, vectors, k: int):
            queries = vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)
            query = queries[0]
            ranked = []
            for label, vector in zip(self.labels, self.vectors):
                score = sum(float(a) * float(b) for a, b in zip(query, vector))
                ranked.append((1.0 - score, label))
            ranked.sort(key=lambda item: (item[0], item[1]))
            return [[label for _, label in ranked[:k]]], [[distance for distance, _ in ranked[:k]]]

    module.Index = FakeIndex
    monkeypatch.setitem(sys.modules, "hnswlib", module)


def test_fastembed_backend_embeds_and_scores_dense_vectors(monkeypatch) -> None:
    install_fake_fastembed(monkeypatch)
    backend = FastEmbedDenseBackend(model_name="fake/model")
    rows = {
        "a": {
            "path": "/tmp/rank.py",
            "relative_path": "rank.py",
            "text": "personal recommendation ranking",
            "embedding_json": backend.embed_document("personal recommendation ranking"),
        },
        "b": {
            "path": "/tmp/video.py",
            "relative_path": "video.py",
            "text": "video transcription",
            "embedding_json": backend.embed_document("video transcription"),
        },
    }

    scores = backend.score_rows(rows, "recommendation ranker", 2)

    assert decode_dense_vector(rows["a"]["embedding_json"])
    assert scores["a"] == 1.0
    assert scores.get("b", 0.0) == 0.0
    assert backend.dimensions == 3


def test_fastembed_backend_embeds_documents_in_one_batch(monkeypatch) -> None:
    install_fake_fastembed(monkeypatch)
    backend = FastEmbedDenseBackend(model_name="fake/model")

    embeddings = backend.embed_documents(["personal recommendation ranking", "video transcription"])

    assert len(embeddings) == 2
    assert [decode_dense_vector(embedding) for embedding in embeddings] == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert FakeTextEmbedding.instances[-1].embed_calls == [
        ["personal recommendation ranking", "video transcription"]
    ]
    assert backend.dimensions == 3


def test_hash_vector_batch_matches_single_document_embeddings() -> None:
    backend = HashVectorLiteBackend()
    texts = ["personal recommendation ranking", "video transcription", "长期记忆 workflow"]

    assert backend.embed_documents(texts) == [backend.embed_document(text) for text in texts]


def test_embed_documents_falls_back_to_single_document_method() -> None:
    class SingleOnlyBackend:
        backend_id = "single-only"
        dimensions = 1
        model_name = None
        storage_format = "test"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed_document(self, text: str) -> str:
            self.calls.append(text)
            return f"embedded:{text}"

        def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
            return {}

    backend = SingleOnlyBackend()

    assert embed_documents(backend, ["a", "b"]) == ["embedded:a", "embedded:b"]
    assert backend.calls == ["a", "b"]


def test_fastembed_can_be_selected_from_config(monkeypatch) -> None:
    install_fake_fastembed(monkeypatch)

    backend = get_embedding_backend(RetrievalConfig(embedding_backend=FASTEMBED_BACKEND_ID))
    meta = backend_meta(RetrievalConfig(embedding_backend=FASTEMBED_BACKEND_ID))

    assert isinstance(backend, FastEmbedDenseBackend)
    assert meta["embedding_backend"] == "fastembed"
    assert meta["embedding_storage_format"] == "json_dense_float32"
    assert meta["embedding_model"] == "BAAI/bge-small-en-v1.5"


def test_default_retrieval_config_reads_vector_backend_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_CONTEXT_VECTOR_BACKEND", FASTEMBED_BACKEND_ID)
    monkeypatch.setenv("AGENT_CONTEXT_ANN_BACKEND", HNSWLIB_BACKEND_ID)

    config = default_retrieval_config()

    assert config.embedding_backend == FASTEMBED_BACKEND_ID
    assert config.ann_backend == HNSWLIB_BACKEND_ID


def test_hnswlib_scores_dense_rows_with_optional_ann(monkeypatch) -> None:
    install_fake_hnswlib(monkeypatch)
    rows = {
        "rank": {"embedding_json": encode_dense_vector([1.0, 0.0, 0.0])},
        "video": {"embedding_json": encode_dense_vector([0.0, 1.0, 0.0])},
    }

    scores = score_dense_rows_with_hnswlib(rows, encode_dense_vector([1.0, 0.0, 0.0]), limit=2)

    assert list(scores)[0] == "rank"
    assert scores["rank"] == 1.0
    assert scores.get("video", 0.0) == 0.0


def test_hnswlib_ann_cache_loads_when_fingerprint_matches(tmp_path: Path, monkeypatch) -> None:
    install_fake_hnswlib(monkeypatch)
    rows = {
        "rank": {"embedding_json": encode_dense_vector([1.0, 0.0, 0.0])},
        "video": {"embedding_json": encode_dense_vector([0.0, 1.0, 0.0])},
    }
    cache_paths = HnswlibCachePaths(
        index_path=tmp_path / "ann.bin",
        metadata_path=tmp_path / "ann.json",
        fingerprint="stable",
    )

    first_scores, first_status = score_dense_rows_with_hnswlib_cached(
        rows,
        encode_dense_vector([1.0, 0.0, 0.0]),
        limit=2,
        cache_paths=cache_paths,
    )
    second_scores, second_status = score_dense_rows_with_hnswlib_cached(
        rows,
        encode_dense_vector([1.0, 0.0, 0.0]),
        limit=2,
        cache_paths=cache_paths,
    )

    assert first_status == "rebuilt"
    assert second_status == "loaded"
    assert first_scores == second_scores
    assert cache_paths.index_path.exists()
    assert cache_paths.metadata_path.exists()
