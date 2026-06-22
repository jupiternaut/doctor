from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text
from .runtime_vm import inspect_runtime_session


RUNTIME_REVIEW_CLIENT_VERSION = "0.1"
RUNTIME_REVIEW_LAUNCH_VERSION = "0.1"


def export_runtime_review_client(
    out_root: str | Path,
    session_id: str,
    *,
    review_server_url: str = "http://127.0.0.1:8765/",
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session = inspect_runtime_session(root, session_id)
    client_dir = ensure_dir(root / "runtime" / "sessions" / session_id / "review_client")
    normalized_url = normalize_review_server_url(review_server_url)
    manifest_path = client_dir / "review_client_manifest.json"
    html_path = client_dir / "doctor-runtime-review-client.html"
    js_path = client_dir / "doctor-runtime-review-client.js"
    contract_path = client_dir / "runtime-review-api-contract.json"
    now = datetime.now().astimezone().isoformat()
    manifest = {
        "runtime_review_client_version": RUNTIME_REVIEW_CLIENT_VERSION,
        "status": "ready",
        "created_at": now,
        "session_id": session_id,
        "out_root": str(root),
        "review_server_url": normalized_url,
        "api": api_contract(normalized_url),
        "runtime_status": session["status"],
        "current_review_file": session["next"].get("review_file"),
        "files": {
            "manifest": str(manifest_path),
            "html": str(html_path),
            "javascript": str(js_path),
            "api_contract": str(contract_path),
        },
        "runtime_session": session,
    }
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(contract_path, json.dumps(api_contract(normalized_url), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(js_path, render_review_client_js())
    write_text(html_path, render_review_client_html(manifest))
    return manifest


def export_runtime_review_launch(
    out_root: str | Path,
    session_id: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    review_server_url = f"http://{host}:{int(port)}/"
    client = export_runtime_review_client(root, session_id, review_server_url=review_server_url)
    client_dir = Path(str(client["files"]["manifest"])).parent
    launch_json_path = client_dir / "review_launch.json"
    launch_md_path = client_dir / "review_launch.md"
    launch = {
        "runtime_review_launch_version": RUNTIME_REVIEW_LAUNCH_VERSION,
        "status": "ready",
        "created_at": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "out_root": str(root),
        "host": host,
        "port": int(port),
        "review_server_url": review_server_url,
        "api_session_url": review_server_url + "api/session",
        "api_action_url": review_server_url + "api/action",
        "client_html_path": client["files"]["html"],
        "client_js_path": client["files"]["javascript"],
        "api_contract_path": client["files"]["api_contract"],
        "review_client_manifest_path": client["files"]["manifest"],
        "start_server_command": doctor_command(root, "runtime-review-server", "--session-id", session_id, "--host", host, "--port", str(int(port))),
        "export_client_command": doctor_command(root, "runtime-review-client", "--session-id", session_id, "--review-server-url", quote_arg(review_server_url)),
        "open_client_command": "open " + quote_arg(client["files"]["html"]),
        "files": {
            "launch_json": str(launch_json_path),
            "launch_md": str(launch_md_path),
            "client_html": client["files"]["html"],
            "client_js": client["files"]["javascript"],
            "api_contract": client["files"]["api_contract"],
            "review_client_manifest": client["files"]["manifest"],
        },
        "review_client": client,
    }
    write_text(launch_json_path, json.dumps(launch, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(launch_md_path, render_review_launch_markdown(launch))
    return launch


def render_review_launch_markdown(launch: dict[str, Any]) -> str:
    return "\n".join(
        [
            "---",
            f"runtime_review_launch_version: {launch['runtime_review_launch_version']}",
            f"status: {launch['status']}",
            f"session_id: {launch['session_id']}",
            "---",
            "",
            "# Doctor Runtime Review Launch",
            "",
            "This file is the launch contract for a Doctor runtime review surface. Start the local server, then open or embed the generated client.",
            "",
            "## URLs",
            "",
            f"- Review server: `{launch['review_server_url']}`",
            f"- Session API: `{launch['api_session_url']}`",
            f"- Action API: `{launch['api_action_url']}`",
            "",
            "## Files",
            "",
            f"- Client HTML: `{launch['client_html_path']}`",
            f"- Client JS: `{launch['client_js_path']}`",
            f"- API contract: `{launch['api_contract_path']}`",
            "",
            "## Commands",
            "",
            "Start the review server:",
            "",
            "```bash",
            launch["start_server_command"],
            "```",
            "",
            "Open the embeddable client:",
            "",
            "```bash",
            launch["open_client_command"],
            "```",
            "",
        ]
    )


def normalize_review_server_url(url: str) -> str:
    value = (url or "http://127.0.0.1:8765/").strip()
    return value if value.endswith("/") else value + "/"


def api_contract(review_server_url: str) -> dict[str, Any]:
    return {
        "session_endpoint": review_server_url + "api/session",
        "action_endpoint": review_server_url + "api/action",
        "method_contract": {
            "GET /api/session": {
                "returns": [
                    "runtime_session",
                    "runtime_acceptance",
                    "review_preview",
                    "allowed_actions",
                    "endpoints",
                ]
            },
            "POST /api/action": {
                "content_type": "application/json",
                "body": {
                    "action": "string",
                    "reason": "string optional",
                    "answer_text": "string optional",
                    "command": "string optional",
                    "cwd": "string optional",
                    "timeout_seconds": "integer optional",
                },
                "returns": ["result", "session"],
            },
        },
        "allowed_action_source": "Use allowed_actions from GET /api/session; do not hardcode gate transitions in the client.",
    }


def render_review_client_html(manifest: dict[str, Any]) -> str:
    review_server_url = manifest["review_server_url"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Doctor Runtime Review Client</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #172033; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 18px; }}
    header {{ display: flex; gap: 12px; align-items: center; justify-content: space-between; }}
    input, textarea {{ box-sizing: border-box; width: 100%; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px; }}
    button {{ border: 1px solid #1d4ed8; border-radius: 6px; background: #2563eb; color: white; padding: 8px 12px; cursor: pointer; }}
    button.secondary {{ border-color: #64748b; background: #64748b; }}
    section {{ background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 14px; margin: 14px 0; }}
    pre {{ white-space: pre-wrap; background: #101827; color: #f8fafc; border-radius: 8px; padding: 12px; max-height: 480px; overflow: auto; }}
    code {{ color: #7c2d12; }}
    .actions {{ display: grid; gap: 10px; }}
    .error {{ color: #991b1b; }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Doctor Runtime Review</h1>
    <button id="refresh">Refresh</button>
  </header>
  <section>
    <label>Review server URL
      <input id="server-url" value="{escape_attr(review_server_url)}">
    </label>
  </section>
  <section id="status"></section>
  <section>
    <h2>Review Preview</h2>
    <pre id="preview">Loading...</pre>
  </section>
  <section>
    <h2>Actions</h2>
    <div id="actions" class="actions"></div>
  </section>
</main>
<script src="./doctor-runtime-review-client.js"></script>
<script>
  window.DoctorRuntimeReviewClient.mount({{
    root: document,
    serverUrl: {json.dumps(review_server_url)}
  }});
</script>
</body>
</html>
"""


def render_review_client_js() -> str:
    return """/* Doctor Runtime Review Client v0.1 */
(function () {
  function normalizeBaseUrl(value) {
    const url = (value || "http://127.0.0.1:8765/").trim();
    return url.endsWith("/") ? url : url + "/";
  }

  async function fetchSession(baseUrl) {
    const response = await fetch(normalizeBaseUrl(baseUrl) + "api/session");
    if (!response.ok) throw new Error("GET /api/session failed: " + response.status);
    return await response.json();
  }

  async function postAction(baseUrl, payload) {
    const response = await fetch(normalizeBaseUrl(baseUrl) + "api/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload || {})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "POST /api/action failed: " + response.status);
    return data;
  }

  function renderStatus(root, payload) {
    const statusNode = root.getElementById("status");
    const session = payload.runtime_session || {};
    const next = session.next || {};
    statusNode.innerHTML = [
      "<h2>Current Gate</h2>",
      "<p>Status: <code>" + escapeHtml(session.status || "") + "</code></p>",
      "<p>Session: <code>" + escapeHtml(payload.session_id || "") + "</code></p>",
      "<p>Review file: <code>" + escapeHtml(next.review_file || "") + "</code></p>",
      "<p>" + escapeHtml(next.message || "") + "</p>"
    ].join("");
    root.getElementById("preview").textContent = payload.review_preview || "";
  }

  function renderActions(root, baseUrl, payload, refresh) {
    const container = root.getElementById("actions");
    const actions = payload.allowed_actions || [];
    if (!actions.length) {
      container.innerHTML = "<p>No available action for this gate.</p>";
      return;
    }
    container.innerHTML = "";
    actions.forEach(function (action) {
      const form = root.createElement("form");
      form.innerHTML = [
        "<strong>" + escapeHtml(action.label || action.action) + "</strong>",
        action.requires_reason ? "<label>Reason<input name='reason'></label>" : "",
        action.requires_command ? "<label>Command<input name='command' value='cat'></label>" : "",
        action.requires_answer_text ? "<label>Answer text<textarea name='answer_text' rows='5'></textarea></label>" : "",
        "<button type='submit'>" + escapeHtml(action.label || action.action) + "</button>"
      ].join("");
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const formData = new FormData(form);
        const body = {action: action.action};
        ["reason", "command", "answer_text"].forEach(function (key) {
          const value = formData.get(key);
          if (value) body[key] = value;
        });
        await postAction(baseUrl(), body);
        await refresh();
      });
      container.appendChild(form);
    });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (char) {
      const replacements = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
      return replacements[char];
    });
  }

  function mount(options) {
    const root = options.root || document;
    const input = root.getElementById("server-url");
    if (input && options.serverUrl) input.value = options.serverUrl;
    const baseUrl = function () { return input ? input.value : options.serverUrl; };
    async function refresh() {
      try {
        const payload = await fetchSession(baseUrl());
        renderStatus(root, payload);
        renderActions(root, baseUrl, payload, refresh);
      } catch (error) {
        root.getElementById("status").innerHTML = "<p class='error'>" + escapeHtml(error.message) + "</p>";
      }
    }
    const button = root.getElementById("refresh");
    if (button) button.addEventListener("click", refresh);
    refresh();
  }

  window.DoctorRuntimeReviewClient = {fetchSession, postAction, mount};
})();
"""


def escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def doctor_command(root: Path, command: str, *args: str) -> str:
    return " ".join(["doctor", command, "--out", quote_arg(str(root)), *args])


def quote_arg(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"
