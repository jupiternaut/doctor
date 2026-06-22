from agent_context.comparison import prioritize_left_sources


def test_prioritize_left_sources_prefers_doctor_paths() -> None:
    sources = [
        {"path": "/Users/gengrf/Code/random/README.md", "score": 1.0},
        {"path": "/Users/gengrf/Documents/recommendation-system-mvp/README.md", "score": 0.7},
        {"path": "/Users/gengrf/agent-context-system/src/agent_context/resolver.py", "score": 0.5},
    ]

    ranked = prioritize_left_sources(sources)

    assert "agent-context-system" in ranked[0]["path"]
    assert "recommendation-system" in ranked[1]["path"]
