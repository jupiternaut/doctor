from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


EMBEDDING_BENCHMARK_VERSION = "0.1"


def run_embedding_benchmark(
    out_root: Path,
    *,
    source: str = "projects",
    queries: list[str] | tuple[str, ...],
    limit: int = 8,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_queries = [query.strip() for query in queries if query and query.strip()]
    if not normalized_queries:
        raise ValueError("at least one benchmark query is required")

    module = load_benchmark_script()
    configs = getattr(module, "SOURCE_CONFIGS", {})
    if source not in configs:
        supported = ", ".join(sorted(configs))
        raise ValueError(f"unsupported benchmark source: {source}; expected one of: {supported}")

    started_at = datetime.now().astimezone()
    report_path = Path(module.run_benchmark(out_root, configs[source], normalized_queries, max(1, int(limit))))
    finished_at = datetime.now().astimezone()
    return {
        "embedding_benchmark_version": EMBEDDING_BENCHMARK_VERSION,
        "status": "ok",
        "source": source,
        "queries": normalized_queries,
        "limit": max(1, int(limit)),
        "out_root": str(out_root),
        "script_path": str(benchmark_script_path()),
        "report_path": str(report_path),
        "report_exists": report_path.exists(),
        "report_size_bytes": report_path.stat().st_size if report_path.exists() else 0,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_ms": int((finished_at - started_at).total_seconds() * 1000),
        "policy": "Builds temporary hash indexes under /tmp; only writes the benchmark report under --out.",
    }


def load_benchmark_script() -> Any:
    path = benchmark_script_path()
    if not path.exists():
        raise FileNotFoundError(f"benchmark script not found: {path}")
    spec = importlib.util.spec_from_file_location("agent_context_embedding_benchmark_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load benchmark script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def benchmark_script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "benchmark_embedding_backends.py"
