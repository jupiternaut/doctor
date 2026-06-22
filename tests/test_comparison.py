from agent_context.comparison import filter_left_sources_for_comparison, prioritize_left_sources


def test_prioritize_left_sources_prefers_doctor_paths() -> None:
    sources = [
        {"path": "/Users/gengrf/Code/random/README.md", "score": 1.0},
        {"path": "/Users/gengrf/Documents/recommendation-system-mvp/README.md", "score": 0.7},
        {"path": "/Users/gengrf/agent-context-system/src/agent_context/resolver.py", "score": 0.5},
    ]

    ranked = prioritize_left_sources(sources)

    assert "agent-context-system" in ranked[0]["path"]
    assert "recommendation-system" in ranked[1]["path"]


def test_filter_left_sources_for_codex_comparison_drops_generic_agent_rules() -> None:
    sources = [
        {
            "path": "/Users/gengrf/AiToEarn/AGENTS.md",
            "snippet": "本文件定义 Codex 在仓库内的默认工作规则。",
            "score": 1.0,
        },
        {
            "path": "/Users/gengrf/agent-context-system/docs/DOCTOR_RUNTIME_VM.md",
            "snippet": "Doctor runtime Context Resolver cold index hot pack.",
            "score": 0.8,
        },
        {
            "path": "/Users/gengrf/Code/adversarial-pixel-debate/PROJECT_TASK_README.md",
            "snippet": "Agent Runtime uses Codex exec subprocesses.",
            "score": 0.7,
        },
    ]

    filtered = filter_left_sources_for_comparison(sources, "我codex的项目和这个人的简历比起来有什么区别")

    paths = [source["path"] for source in filtered]
    assert "/Users/gengrf/AiToEarn/AGENTS.md" not in paths
    assert "/Users/gengrf/agent-context-system/docs/DOCTOR_RUNTIME_VM.md" in paths
    assert "/Users/gengrf/Code/adversarial-pixel-debate/PROJECT_TASK_README.md" in paths
