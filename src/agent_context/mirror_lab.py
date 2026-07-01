from __future__ import annotations

import html
import json
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .io import ensure_dir, write_text
from .lab import run_lab_once


MIRROR_LAB_VERSION = "0.2"
MIRROR_LAB_DIRNAME = "mirror_lab"
SUPPORTED_MODES = ("fast", "deep", "arena")


def build_mirror_lab(out_root: Path, *, goal: str = "", mode: str = "fast", server_url: str = "") -> dict[str, Any]:
    root = out_root.expanduser().resolve()
    lab_dir = ensure_dir(root / MIRROR_LAB_DIRNAME)
    state_path = lab_dir / "state.json"
    html_path = lab_dir / "index.html"

    state = build_mirror_lab_state(root, goal=goal, mode=mode, server_url=server_url)
    write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(html_path, render_mirror_lab_html(state, state_path))

    return {
        "mirror_lab_version": MIRROR_LAB_VERSION,
        "status": "ok",
        "mode": state["mode"],
        "goal": state["goal"],
        "mirror_lab_dir": str(lab_dir),
        "index_html_path": str(html_path),
        "state_json_path": str(state_path),
    }


def build_mirror_lab_state(out_root: Path, *, goal: str, mode: str, server_url: str = "") -> dict[str, Any]:
    selected_mode = mode.strip() or "fast"
    return {
        "mirror_lab_version": MIRROR_LAB_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "out_root": str(out_root),
        "server_url": server_url,
        "mode": selected_mode,
        "goal": goal,
        "supported_modes": list(SUPPORTED_MODES),
        "phases": mirror_lab_phases(),
        "candidates": mirror_lab_candidates(selected_mode),
        "artifacts": {
            "context_md": "context.md",
            "sources_jsonl": "sources.jsonl",
            "token_budget": {
                "fast": "约 8k token，占位",
                "deep": "约 24k token，占位",
                "arena": "三路对照预算，占位",
            },
        },
        "feedback_buttons": mirror_lab_feedback_buttons(),
        "feedback_events": [],
        "image_paths": [],
        "doctor_requests": [],
        "doctor_results": [],
        "doctor_status": "",
    }


def run_mirror_lab_server(
    out_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    goal: str = "",
    mode: str = "fast",
    open_browser: bool = True,
) -> dict[str, Any]:
    root = out_root.expanduser().resolve()

    class Handler(MirrorLabRequestHandler):
        out_root = root
        initial_goal = goal
        initial_mode = mode

    server = ThreadingHTTPServer((host, int(port)), Handler)
    url = f"http://{host}:{server.server_address[1]}/"
    build_mirror_lab(root, goal=goal, mode=mode, server_url=url)
    print(
        json.dumps(
            {
                "status": "serving",
                "url": url,
                "api_send_url": f"{url}api/send",
                "out_root": str(root),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if open_browser:
        webbrowser.open(url)
    try:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
    return {"status": "stopped", "url": url, "out_root": str(root)}


def handle_mirror_lab_send(out_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal") or "").strip()
    mode = str(payload.get("mode") or "fast").strip() or "fast"
    image_paths = [str(path) for path in payload.get("image_paths") or [] if str(path).strip()]
    if not goal and not image_paths:
        raise ValueError("请输入任务目标或图片路径")
    limit = {"fast": 5, "deep": 10, "arena": 8}.get(mode, 5)
    result = run_lab_once(out_root, text=goal, image_paths=image_paths, source_scope="all", limit=limit)
    return {
        "mirror_lab_server_version": MIRROR_LAB_VERSION,
        "status": "ok",
        "mode": mode,
        "goal": goal,
        "lab_result": result,
        "context_md_path": result.get("context_md_path"),
        "sources_jsonl_path": result.get("sources_jsonl_path"),
        "manifest_json_path": result.get("manifest_json_path"),
        "run_json_path": result.get("run_json_path"),
        "top_sources": result.get("top_sources") or [],
    }


def mirror_lab_phases() -> list[dict[str, str]]:
    return [
        {
            "id": "normalize_goal",
            "title": "需求归一化",
            "description": "把用户原始目标整理成可审查的单一任务，记录缺口和隐含假设。",
            "review_focus": "目标是否明确，是否需要追问，是否误把临时探索当成主项目。",
        },
        {
            "id": "doctor_context_injection",
            "title": "Doctor 上下文注入",
            "description": "注入 context.md 与 sources.jsonl 的占位信息，检查来源、时间和隐私边界。",
            "review_focus": "上下文是否足够、来源是否过时、是否包含隐私敏感内容。",
        },
        {
            "id": "model_answer_review",
            "title": "模型回答审查",
            "description": "审查模型答案是否真的使用上下文候选，并区分证据、推断和下一步。",
            "review_focus": "答案是否贴合目标，是否过度承诺，是否缺少证据。",
        },
        {
            "id": "local_execution_review",
            "title": "本机执行审查",
            "description": "审查本机执行结果和产物路径，确认哪些可以继续沉淀为项目记忆。",
            "review_focus": "本地产物是否存在，执行风险是否可控，是否适合简历或主项目标记。",
        },
    ]


def mirror_lab_candidates(selected_mode: str) -> list[dict[str, Any]]:
    specs = [
        (
            "fast",
            "fast 候选",
            "快速候选，优先少量高置信上下文。",
            "适合低成本预检和短任务，token 预算占位：约 8k。",
        ),
        (
            "deep",
            "deep 候选",
            "深度候选，优先更多证据和历史线索。",
            "适合复杂判断和项目定位，token 预算占位：约 24k。",
        ),
        (
            "arena",
            "arena 候选",
            "对照候选，用三路上下文互相校验。",
            "适合比较 fast/deep 的偏差，token 预算占位：三路对照。",
        ),
    ]
    return [
        {
            "mode": mode,
            "title": title,
            "summary": summary,
            "token_budget_placeholder": token_budget,
            "context_md_placeholder": "context.md 待生成或待绑定",
            "sources_jsonl_placeholder": "sources.jsonl 待生成或待绑定",
            "selected": mode == selected_mode,
        }
        for mode, title, summary, token_budget in specs
    ]


def mirror_lab_feedback_buttons() -> list[str]:
    return [
        "这是我的主项目",
        "适合简历",
        "不适合简历",
        "不是我的项目",
        "证据不够",
        "来源过时",
        "隐私敏感",
        "下次少推荐",
    ]


def render_mirror_lab_html(state: dict[str, Any], state_path: Path) -> str:
    goal = str(state.get("goal") or "").strip() or "未填写，等待审查任务"
    runtime_hint = (
        "当前是 localhost 模式；点击“发送到 Doctor”会直接调用本机后端并生成 context.md。"
        if state.get("server_url")
        else "当前是静态 file:// 模式；请启动 mirror-lab-server 后使用直接发送。"
    )
    phase_cards = "\n".join(render_phase_card(phase, index) for index, phase in enumerate(state["phases"], start=1))
    candidate_cards = "\n".join(render_candidate_card(candidate) for candidate in state["candidates"])
    button_html = "\n".join(
        f'<button type="button" class="feedback-button" data-label="{html.escape(label)}">{html.escape(label)}</button>' for label in state["feedback_buttons"]
    )
    initial_state = html.escape(json.dumps(state, ensure_ascii=False), quote=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mirror Lab v0.2 本地审查台</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #5f6b7a;
      --line: #d7dde3;
      --accent: #1f7a8c;
      --accent-soft: #e7f4f6;
      --warn-soft: #fff3d6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      line-height: 1.55;
    }}
    main, header {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    header {{ padding-top: 40px; }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ font-size: 32px; margin-bottom: 10px; }}
    h2 {{ font-size: 22px; margin-bottom: 16px; }}
    h3 {{ font-size: 17px; margin-bottom: 8px; }}
    .eyebrow {{ color: var(--accent); font-weight: 700; letter-spacing: .03em; }}
    .lede {{ max-width: 760px; color: var(--muted); }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 24px 0;
    }}
    .metric, .phase, .candidate, .raw-state {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 6px; word-break: break-word; }}
    .phase-grid, .candidate-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 28px;
    }}
    .phase-number {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      margin-bottom: 12px;
    }}
    .candidate.selected {{ border-color: var(--accent); background: var(--accent-soft); }}
    .candidate-meta {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin-top: 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    code {{
      background: #eef0f2;
      border-radius: 5px;
      padding: 2px 6px;
      word-break: break-word;
    }}
    .buttons {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 28px; }}
    button {{
      border: 1px solid #b8c5ca;
      background: #ffffff;
      color: var(--ink);
      border-radius: 7px;
      padding: 9px 12px;
      font: inherit;
      cursor: default;
    }}
    .note {{
      background: var(--warn-soft);
      border: 1px solid #e6d08b;
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 24px;
    }}
    .workspace {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(280px, .7fr);
      gap: 14px;
      margin-bottom: 28px;
    }}
    .control {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    label {{ display: block; font-weight: 700; margin-bottom: 8px; }}
    textarea, input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px 11px;
      font: inherit;
      background: #fff;
      color: var(--ink);
    }}
    textarea {{ min-height: 132px; resize: vertical; }}
    .row {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; margin-top: 12px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }}
    .primary {{ background: var(--accent); border-color: var(--accent); color: #fff; cursor: pointer; }}
    .secondary {{ cursor: pointer; }}
    .event-log {{ display: grid; gap: 8px; margin-top: 12px; }}
    .event {{
      border: 1px solid var(--line);
      background: #fafbfc;
      border-radius: 7px;
      padding: 9px 10px;
      font-size: 14px;
    }}
    .status-line {{
      margin: 10px 0 0;
      min-height: 22px;
      color: var(--accent);
      font-size: 14px;
      font-weight: 700;
    }}
    .source-list {{
      margin: 8px 0 0 18px;
      padding: 0;
      color: var(--muted);
    }}
    .source-list li {{ margin: 4px 0; overflow-wrap: anywhere; }}
    .muted {{ color: var(--muted); }}
    pre {{
      overflow: auto;
      max-height: 520px;
      padding: 14px;
      background: #17212b;
      color: #f8fafc;
      border-radius: 8px;
    }}
    @media (max-width: 760px) {{
      main, header {{ padding: 20px; }}
      .summary, .phase-grid, .candidate-grid {{ grid-template-columns: 1fr; }}
      .workspace {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
    }}
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">Mirror Lab v{html.escape(str(state["mirror_lab_version"]))}</p>
    <h1>本地中文实验室</h1>
    <p class="lede">用于离线审查四阶段流程和三路上下文候选。页面不依赖网络，不启动前端框架；状态同时写入 <code>state.json</code>。</p>
  </header>
  <main>
    <section class="summary" aria-label="实验室摘要">
      <div class="metric"><span>当前目标</span><strong id="current-goal">{html.escape(goal)}</strong></div>
      <div class="metric"><span>当前模式</span><strong id="current-mode">{html.escape(str(state["mode"]))}</strong></div>
      <div class="metric"><span>上下文占位</span><strong><code>context.md</code></strong></div>
      <div class="metric"><span>来源与预算</span><strong><code>sources.jsonl</code> / token 预算占位</strong></div>
    </section>

    <section class="workspace" aria-label="交互工作区">
      <div class="control">
        <h2>输入与上下文审查</h2>
        <label for="goal-input">用户问题 / 任务目标</label>
        <textarea id="goal-input" placeholder="输入你要让 Mirror 审查的问题">{html.escape(goal)}</textarea>
        <div class="row">
          <div>
            <label for="image-input">图片路径</label>
            <input id="image-input" placeholder="/Users/.../image.png">
          </div>
          <button type="button" id="add-image" class="secondary">添加图片</button>
        </div>
        <div class="row">
          <div>
            <label for="mode-select">上下文模式</label>
            <select id="mode-select">
              {render_mode_options(str(state["mode"]))}
            </select>
          </div>
          <button type="button" id="apply-input" class="primary">更新审查状态</button>
        </div>
        <div class="actions">
          <button type="button" id="send-doctor" class="primary">发送到 Doctor</button>
          <button type="button" id="export-state" class="secondary">导出 state.json</button>
          <button type="button" id="export-request" class="secondary">导出 doctor_request.json</button>
          <button type="button" id="export-feedback" class="secondary">导出 feedback.jsonl</button>
          <button type="button" id="copy-cli" class="secondary">复制 CLI 命令</button>
        </div>
        <p id="doctor-status" class="status-line"></p>
      </div>
      <div class="control">
        <h2>本轮事件</h2>
        <p class="muted">{html.escape(runtime_hint)}</p>
        <div id="image-list" class="event-log"></div>
        <div id="request-log" class="event-log"></div>
        <div id="feedback-log" class="event-log"></div>
      </div>
    </section>

    <section>
      <h2>四阶段流程</h2>
      <div class="phase-grid">
        {phase_cards}
      </div>
    </section>

    <section>
      <h2>三路上下文候选</h2>
      <div class="candidate-grid">
        {candidate_cards}
      </div>
    </section>

    <section>
      <h2>审查反馈</h2>
      <div class="buttons" aria-label="审查按钮">
        {button_html}
      </div>
      <p class="note">点击按钮会写入页面内反馈事件。用“导出 feedback.jsonl”保存后，可作为后续 `profile-event` / `ranker-feedback` 的输入。</p>
    </section>

    <section class="raw-state">
      <h2>状态快照</h2>
      <p>本地状态文件：<code>{html.escape(str(state_path))}</code></p>
      <p>当前模式：<strong>{html.escape(str(state["mode"]))}</strong></p>
      <p>当前目标：{html.escape(goal)}</p>
      <p>已写入离线页面和状态文件，可直接打开 <code>index.html</code> 审查。</p>
      <pre id="state-preview">{html.escape(json.dumps(state, ensure_ascii=False, indent=2))}</pre>
    </section>
  </main>
  <script type="application/json" id="initial-state">{initial_state}</script>
  <script>
    const state = JSON.parse(document.getElementById("initial-state").textContent);
    const goalInput = document.getElementById("goal-input");
    const modeSelect = document.getElementById("mode-select");
    const currentGoal = document.getElementById("current-goal");
    const currentMode = document.getElementById("current-mode");
    const feedbackLog = document.getElementById("feedback-log");
    const imageList = document.getElementById("image-list");
    const requestLog = document.getElementById("request-log");
    const doctorStatus = document.getElementById("doctor-status");
    const statePreview = document.getElementById("state-preview");

    function nowIso() {{
      return new Date().toISOString();
    }}

    function refresh() {{
      currentGoal.textContent = state.goal || "未填写，等待审查任务";
      currentMode.textContent = state.mode;
      doctorStatus.textContent = state.doctor_status || (state.server_url ? "已连接 Doctor，可以直接发送。" : "静态文件模式：请启动 mirror-lab-server 后再直接发送。");
      statePreview.textContent = JSON.stringify(state, null, 2);
      feedbackLog.innerHTML = "";
      for (const event of state.feedback_events) {{
        const item = document.createElement("div");
        item.className = "event";
        item.textContent = `${{event.created_at}} · ${{event.label}} · ${{event.goal || ""}}`;
        feedbackLog.appendChild(item);
      }}
      imageList.innerHTML = "";
      for (const path of state.image_paths) {{
        const item = document.createElement("div");
        item.className = "event";
        item.textContent = `图片：${{path}}`;
        imageList.appendChild(item);
      }}
      requestLog.innerHTML = "";
      for (const request of state.doctor_requests) {{
        const item = document.createElement("div");
        item.className = "event";
        item.textContent = `${{request.created_at}} · 已发送到 Doctor · ${{request.mode}} · ${{request.goal || ""}}`;
        requestLog.appendChild(item);
      }}
      for (const result of state.doctor_results) {{
        const item = document.createElement("div");
        item.className = "event";
        const context = result.context_md_path || "";
        const title = document.createElement("div");
        title.textContent = `${{result.created_at}} · Doctor 已返回 · context: ${{context}}`;
        item.appendChild(title);
        if (result.top_sources && result.top_sources.length) {{
          const list = document.createElement("ol");
          list.className = "source-list";
          for (const source of result.top_sources.slice(0, 5)) {{
            const row = document.createElement("li");
            const score = source.score === undefined ? "" : ` · score=${{Number(source.score).toFixed(3)}}`;
            row.textContent = `${{source.path || source.relative_path || "unknown"}}${{score}}`;
            list.appendChild(row);
          }}
          item.appendChild(list);
        }}
        requestLog.appendChild(item);
      }}
      document.querySelectorAll(".candidate").forEach((card) => {{
        card.classList.toggle("selected", card.dataset.mode === state.mode);
      }});
    }}

    function buildDoctorRequest() {{
      return {{
        mirror_lab_version: state.mirror_lab_version,
        created_at: nowIso(),
        goal: state.goal,
        mode: state.mode,
        image_paths: state.image_paths,
        requested_action: "doctor_context_review",
        command: buildCliCommand()
      }};
    }}

    function buildCliCommand() {{
      const escapedGoal = (state.goal || "").replaceAll('"', '\\\\"');
      return `uv run ./agent-context mirror-lab-server --out {html.escape(str(state.get("out_root") or "."))} --goal "${{escapedGoal}}" --mode ${{state.mode}}`;
    }}

    async function copyText(text, successMessage) {{
      try {{
        await navigator.clipboard.writeText(text);
        alert(successMessage);
      }} catch (error) {{
        window.prompt(successMessage, text);
      }}
    }}

    document.getElementById("apply-input").addEventListener("click", () => {{
      state.goal = goalInput.value.trim();
      state.mode = modeSelect.value;
      state.updated_at = nowIso();
      refresh();
    }});

    document.getElementById("add-image").addEventListener("click", () => {{
      const input = document.getElementById("image-input");
      const value = input.value.trim();
      if (!value) return;
      state.image_paths.push(value);
      input.value = "";
      state.updated_at = nowIso();
      refresh();
    }});

    document.querySelectorAll(".feedback-button").forEach((button) => {{
      button.addEventListener("click", () => {{
        state.feedback_events.push({{
          created_at: nowIso(),
          label: button.dataset.label,
          goal: state.goal,
          mode: state.mode,
          source: "mirror_lab_browser"
        }});
        state.updated_at = nowIso();
        refresh();
      }});
    }});

    document.getElementById("send-doctor").addEventListener("click", async () => {{
      const sendButton = document.getElementById("send-doctor");
      state.goal = goalInput.value.trim();
      state.mode = modeSelect.value;
      const request = buildDoctorRequest();
      state.doctor_requests.push(request);
      state.doctor_status = "Doctor 正在生成上下文，请稍等。";
      state.updated_at = nowIso();
      refresh();
      if (!state.server_url) {{
        await copyText(request.command, "当前是 file:// 静态页面，已复制命令。要直接发送，请使用 mirror-lab-server。");
        return;
      }}
      try {{
        sendButton.disabled = true;
        sendButton.textContent = "生成中...";
        const response = await fetch(new URL("/api/send", state.server_url), {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(request)
        }});
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok") {{
          throw new Error(payload.error || `HTTP ${{response.status}}`);
        }}
        state.doctor_results.push({{
          created_at: nowIso(),
          context_md_path: payload.context_md_path,
          sources_jsonl_path: payload.sources_jsonl_path,
          manifest_json_path: payload.manifest_json_path,
          run_json_path: payload.run_json_path,
          top_sources: payload.top_sources || []
        }});
        state.doctor_status = `已生成上下文：${{payload.context_md_path || ""}}`;
        state.updated_at = nowIso();
        refresh();
      }} catch (error) {{
        state.doctor_status = `发送失败：${{error.message}}`;
        refresh();
        alert(`发送失败：${{error.message}}`);
      }} finally {{
        sendButton.disabled = false;
        sendButton.textContent = "发送到 Doctor";
      }}
    }});

    function download(filename, text, type = "application/json") {{
      const blob = new Blob([text], {{ type }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}

    document.getElementById("export-state").addEventListener("click", () => {{
      download("mirror_lab_state.json", JSON.stringify(state, null, 2) + "\\n");
    }});

    document.getElementById("export-request").addEventListener("click", () => {{
      const request = state.doctor_requests[state.doctor_requests.length - 1] || buildDoctorRequest();
      download("doctor_request.json", JSON.stringify(request, null, 2) + "\\n");
    }});

    document.getElementById("export-feedback").addEventListener("click", () => {{
      const lines = state.feedback_events.map((event) => JSON.stringify(event)).join("\\n");
      download("mirror_lab_feedback.jsonl", lines + (lines ? "\\n" : ""), "application/x-ndjson");
    }});

    document.getElementById("copy-cli").addEventListener("click", async () => {{
      await copyText(buildCliCommand(), "已复制 CLI 命令");
    }});

    refresh();
  </script>
</body>
</html>
"""


def render_phase_card(phase: dict[str, str], index: int) -> str:
    return (
        '<article class="phase">'
        f'<span class="phase-number">{index}</span>'
        f'<h3>{html.escape(phase["title"])}</h3>'
        f'<p>{html.escape(phase["description"])}</p>'
        f'<p><strong>审查重点：</strong>{html.escape(phase["review_focus"])}</p>'
        "</article>"
    )


def render_candidate_card(candidate: dict[str, Any]) -> str:
    classes = "candidate selected" if candidate.get("selected") is True else "candidate"
    return (
        f'<article class="{classes}" data-mode="{html.escape(str(candidate["mode"]))}">'
        f'<h3>{html.escape(candidate["title"])}</h3>'
        f'<p>{html.escape(candidate["summary"])}</p>'
        '<div class="candidate-meta">'
        f'<span>上下文：<code>{html.escape(candidate["context_md_placeholder"])}</code></span>'
        f'<span>来源：<code>{html.escape(candidate["sources_jsonl_placeholder"])}</code></span>'
        f'<span>预算：{html.escape(candidate["token_budget_placeholder"])}</span>'
        "</div>"
        "</article>"
    )


def render_mode_options(selected_mode: str) -> str:
    labels = {
        "fast": "fast：少量高置信上下文",
        "deep": "deep：更多证据和历史线索",
        "arena": "arena：三路对照",
    }
    options = []
    for mode in SUPPORTED_MODES:
        selected = " selected" if mode == selected_mode else ""
        options.append(f'<option value="{mode}"{selected}>{html.escape(labels[mode])}</option>')
    return "\n".join(options)


class MirrorLabRequestHandler(BaseHTTPRequestHandler):
    out_root: Path
    initial_goal: str = ""
    initial_mode: str = "fast"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self.respond_json(build_mirror_lab_state(self.out_root, goal=self.initial_goal, mode=self.initial_mode, server_url=self.server_url()))
            return
        if parsed.path not in {"/", "/index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        state = build_mirror_lab_state(self.out_root, goal=self.initial_goal, mode=self.initial_mode, server_url=self.server_url())
        self.respond_html(render_mirror_lab_html(state, self.out_root / MIRROR_LAB_DIRNAME / "state.json"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/send":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            payload = self.read_json_payload()
            result = handle_mirror_lab_send(self.out_root, payload)
            self.respond_json(result)
        except Exception as exc:
            self.respond_json({"status": "error", "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def read_json_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def respond_html(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def server_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/"

    def log_message(self, format: str, *args: Any) -> None:
        return
