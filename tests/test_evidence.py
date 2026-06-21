from __future__ import annotations

from agent_context.evidence import source_to_evidence_record, validate_evidence_record


def test_codebase_memory_source_becomes_code_evidence() -> None:
    source = {
        "provider": "codebase_memory",
        "source_group": "codebase_memory",
        "source_id": "codebase-memory:repo:src/app.py",
        "path": "/repo/src/app.py",
        "relative_path": "src/app.py",
        "line": 42,
        "snippet": "def recommend_items(): pass",
        "score": 0.72,
        "matched_queries": ["推荐系统"],
        "retrieval_channel": "codebase_memory_search_code",
    }

    evidence = source_to_evidence_record(source, goal="如何构建个人推荐系统")

    assert validate_evidence_record(evidence) == []
    assert evidence["source_type"] == "code"
    assert evidence["provider"] == "codebase_memory"
    assert evidence["location"]["line"] == 42
    assert evidence["retrieval"]["channels"] == ["codebase_memory_search_code"]
    assert {"kind": "code", "ref": "provider:code_graph"} in evidence["embedding_refs"]


def test_download_chunk_source_becomes_document_evidence() -> None:
    source = {
        "type": "chunk",
        "path": "/Users/example/Downloads/research.pdf",
        "relative_path": "research.pdf",
        "doc_id": "sha256:abc",
        "chunk_id": "chunk-1",
        "parser": "markitdown",
        "snippet": "用户画像和长期记忆需要证据引用。",
    }

    evidence = source_to_evidence_record(source, goal="用户画像")

    assert validate_evidence_record(evidence) == []
    assert evidence["source_type"] == "document"
    assert evidence["provider"] == "markitdown"
    assert evidence["provenance"]["source_chunk_id"] == "chunk-1"
    assert {"kind": "text", "ref": "derived:text"} in evidence["embedding_refs"]


def test_douyin_video_source_becomes_video_evidence() -> None:
    source = {
        "provider": "douyin_video",
        "source_group": "douyin_videos",
        "path": "/Users/example/Movies/douyin/a.mp4",
        "title": "开源往事爆火分析",
        "snippet": "视频讨论开源故事、番茄读者和用户兴趣。",
        "timestamp": 12.4,
    }

    evidence = source_to_evidence_record(source, goal="构建抖音用户画像")

    assert validate_evidence_record(evidence) == []
    assert evidence["source_type"] == "video"
    assert evidence["location"]["timestamp"] == 12.4
    assert {"kind": "vision", "ref": "derived:vision"} in evidence["embedding_refs"]
    assert {"kind": "audio", "ref": "derived:asr"} in evidence["embedding_refs"]
