from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_context.mirror_lab import MirrorLabRequestHandler, build_mirror_lab


def test_build_mirror_lab_writes_local_chinese_ui(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = build_mirror_lab(out, goal="审查 Doctor 四阶段上下文流程", mode="deep")

    html_path = out / "mirror_lab" / "index.html"
    state_path = out / "mirror_lab" / "state.json"
    assert result["index_html_path"] == str(html_path.resolve())
    assert result["state_json_path"] == str(state_path.resolve())
    assert html_path.exists()
    assert state_path.exists()

    html = html_path.read_text(encoding="utf-8")
    assert "需求归一化" in html
    assert "Doctor 上下文注入" in html
    assert "模型回答审查" in html
    assert "本机执行审查" in html
    assert "fast 候选" in html
    assert "deep 候选" in html
    assert "arena 候选" in html
    assert "context.md" in html
    assert "sources.jsonl" in html
    assert "token 预算占位" in html
    assert 'id="goal-input"' in html
    assert 'id="image-input"' in html
    assert 'id="mode-select"' in html
    assert 'id="export-state"' in html
    assert 'id="send-doctor"' in html
    assert "发送到 Doctor" in html
    assert 'id="doctor-status"' in html
    assert "Doctor 正在生成上下文" in html
    assert "已生成上下文" in html
    assert "source-list" in html
    assert 'id="export-request"' in html
    assert "doctor_request.json" in html
    assert "doctor_requests" in html
    assert "/api/send" in html
    assert "fetch(new URL" in html
    assert "mirror-lab-server" in html
    assert 'id="export-feedback"' in html
    assert "feedback_events" in html
    assert "addEventListener" in html

    for label in [
        "这是我的主项目",
        "适合简历",
        "不适合简历",
        "不是我的项目",
        "证据不够",
        "来源过时",
        "隐私敏感",
        "下次少推荐",
    ]:
        assert label in html

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["mode"] == "deep"
    assert state["goal"] == "审查 Doctor 四阶段上下文流程"


def test_mirror_lab_server_send_endpoint_returns_context(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "out"

    def fake_run_lab_once(out_root, *, text, image_paths, source_scope, limit):
        assert text == "我要去北京"
        assert image_paths == ["/tmp/a.png"]
        return {
            "status": "ok",
            "context_md_path": str(Path(out_root) / "packs" / "context.md"),
            "sources_jsonl_path": str(Path(out_root) / "packs" / "sources.jsonl"),
            "manifest_json_path": str(Path(out_root) / "packs" / "manifest.json"),
            "run_json_path": str(Path(out_root) / "lab" / "run.json"),
            "top_sources": [{"path": "/tmp/source.md"}],
        }

    monkeypatch.setattr("agent_context.mirror_lab.run_lab_once", fake_run_lab_once)

    class Handler(MirrorLabRequestHandler):
        out_root = out
        initial_goal = ""
        initial_mode = "fast"

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/send"
        request = urllib.request.Request(
            url,
            data=json.dumps({"goal": "我要去北京", "mode": "fast", "image_paths": ["/tmp/a.png"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["status"] == "ok"
    assert payload["context_md_path"].endswith("context.md")
    assert payload["top_sources"] == [{"path": "/tmp/source.md"}]
