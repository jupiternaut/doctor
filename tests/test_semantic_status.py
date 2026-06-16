from __future__ import annotations

import json

from agent_context.cli import main
from agent_context.semantic import semantic_status


def test_semantic_status_reports_hash_vector_baseline() -> None:
    status = semantic_status()

    assert status["semantic_status_version"] == "0.1"
    assert status["selected_backend"] == "hash-vector-lite"
    assert any(backend["name"] == "hash-vector-lite" and backend["available"] for backend in status["backends"])
    assert "next_step" in status


def test_semantic_status_cli(capsys) -> None:
    assert main(["semantic-status"]) == 0

    status = json.loads(capsys.readouterr().out)

    assert status["configured_backend"] == "hash-vector-lite"
    assert status["selected_backend"] == "hash-vector-lite"
