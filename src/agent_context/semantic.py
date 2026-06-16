from __future__ import annotations

import importlib.util
import os
from typing import Any


SEMANTIC_STATUS_VERSION = "0.1"
DEFAULT_BACKEND = "hash-vector-lite"
OPTIONAL_BACKENDS = (
    {
        "name": "sqlite-vec",
        "package": "sqlite_vec",
        "kind": "vector_sqlite_extension",
        "embedding": False,
        "ann": True,
    },
    {
        "name": "fastembed",
        "package": "fastembed",
        "kind": "local_embedding_model",
        "embedding": True,
        "ann": False,
    },
    {
        "name": "sentence-transformers",
        "package": "sentence_transformers",
        "kind": "local_embedding_model",
        "embedding": True,
        "ann": False,
    },
    {
        "name": "hnswlib",
        "package": "hnswlib",
        "kind": "ann_index",
        "embedding": False,
        "ann": True,
    },
)


def semantic_status() -> dict[str, Any]:
    configured = os.environ.get("AGENT_CONTEXT_VECTOR_BACKEND", DEFAULT_BACKEND)
    configured_ann = os.environ.get("AGENT_CONTEXT_ANN_BACKEND", "exact-json-scan")
    backends = [
        {
            "name": DEFAULT_BACKEND,
            "package": None,
            "kind": "deterministic_sparse_hash_vector",
            "available": True,
            "embedding": False,
            "ann": False,
            "current": configured == DEFAULT_BACKEND,
            "notes": "Always available baseline used by current SQLite indexes.",
        }
    ]
    for backend in OPTIONAL_BACKENDS:
        available = importlib.util.find_spec(str(backend["package"])) is not None
        backends.append(
            {
                **backend,
                "available": available,
                "current": configured == backend["name"],
                "notes": "Installed and selectable." if available else "Not installed; keep optional to avoid heavy default deps.",
            }
        )

    selected = next((backend for backend in backends if backend["name"] == configured), None)
    if not selected or not selected["available"]:
        selected = backends[0]

    return {
        "semantic_status_version": SEMANTIC_STATUS_VERSION,
        "configured_backend": configured,
        "configured_ann_backend": configured_ann,
        "selected_backend": selected["name"],
        "real_embedding_available": any(backend["available"] and backend["embedding"] for backend in backends),
        "ann_available": any(backend["available"] and backend["ann"] for backend in backends),
        "backends": backends,
        "next_step": next_step_for(backends),
    }


def next_step_for(backends: list[dict[str, Any]]) -> str:
    has_embedding = any(backend["available"] and backend["embedding"] for backend in backends)
    has_ann = any(backend["available"] and backend["ann"] for backend in backends)
    if has_embedding and has_ann:
        return "Set AGENT_CONTEXT_ANN_BACKEND=hnswlib to enable optional ANN semantic retrieval; resolver falls back to exact scan if needed."
    if has_embedding:
        return "Add an ANN backend or use SQLite exact vector scoring before scaling."
    if has_ann:
        return "Add a local embedding model; ANN alone cannot improve semantic recall."
    return "Install an optional local embedding backend first; current indexes remain hash-vector-lite."
