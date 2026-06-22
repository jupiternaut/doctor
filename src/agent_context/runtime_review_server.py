from __future__ import annotations

import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .answer_review import run_answer_review
from .context_review import run_context_review
from .execution_review import run_execution_review
from .runtime_vm import export_runtime_handoff, inspect_runtime_session, run_runtime_vm_acceptance


RUNTIME_REVIEW_SERVER_VERSION = "0.1"


def handle_runtime_review_action(
    out_root: str | Path,
    session_id: str,
    *,
    action: str,
    reason: str = "",
    answer_text: str = "",
    command: str = "",
    cwd: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session = inspect_runtime_session(root, session_id)
    status = session["status"]
    result: dict[str, Any]

    if action == "generate_context" and status in {"awaiting_context_generation", "context_rejected"}:
        result = run_context_review(root, action="generate" if status == "awaiting_context_generation" else "regenerate", session_id=session_id, reason=reason, source_scope="all", limit=8, mode="fast")
    elif action in {"approve_context", "reject_context"} and status == "awaiting_context_review":
        result = run_context_review(root, action="approve" if action == "approve_context" else "reject", session_id=session_id, reason=reason)
    elif action == "export_handoff" and status == "ready_for_agent_handoff":
        result = export_runtime_handoff(root, session_id)
    elif action == "prepare_answer" and status == "ready_for_answer_prepare":
        result = run_answer_review(root, action="prepare", session_id=session_id, reason=reason)
    elif action in {"run_answer", "rerun_answer"} and status in {"awaiting_answer_output", "answer_rejected", "answer_failed"}:
        result = run_answer_review(root, action="run", session_id=session_id, command=command, cwd=cwd or str(root), timeout_seconds=max(1, timeout_seconds), reason=reason)
    elif action in {"record_answer", "record_revised_answer"} and status in {"awaiting_answer_output", "answer_rejected", "answer_failed"}:
        result = run_answer_review(root, action="record", session_id=session_id, answer_text=answer_text, reason=reason)
    elif action in {"approve_answer", "reject_answer"} and status == "awaiting_answer_review":
        result = run_answer_review(root, action="approve" if action == "approve_answer" else "reject", session_id=session_id, reason=reason)
    elif action == "prepare_execution" and status == "ready_for_execution_prepare":
        result = run_execution_review(root, action="prepare", session_id=session_id, reason=reason)
    elif action in {"run_execution", "rerun_execution"} and status in {"awaiting_execution", "execution_rejected"}:
        result = run_execution_review(root, action="run", session_id=session_id, command=command, cwd=cwd or str(root), timeout_seconds=max(1, timeout_seconds), reason=reason)
    elif action in {"approve_execution", "reject_execution"} and status == "awaiting_execution_review":
        result = run_execution_review(root, action="approve" if action == "approve_execution" else "reject", session_id=session_id, reason=reason)
    else:
        raise ValueError(f"action {action!r} is not allowed while session status is {status!r}")

    refreshed = inspect_runtime_session(root, session_id)
    acceptance = run_runtime_vm_acceptance(root, session_id)
    return {
        "runtime_review_server_version": RUNTIME_REVIEW_SERVER_VERSION,
        "status": "ok",
        "action": action,
        "stage_result": result,
        "runtime_session": refreshed,
        "runtime_acceptance": acceptance,
    }


def render_runtime_review_html(out_root: str | Path, session_id: str, *, notice: str = "") -> str:
    root = Path(out_root).expanduser().resolve()
    session = inspect_runtime_session(root, session_id)
    acceptance = safe_acceptance(root, session_id)
    preview = review_file_preview(root, session)
    status = session["status"]
    stage_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(stage['label'])}</td>"
        f"<td><code>{html.escape(str(stage['status']))}</code></td>"
        f"<td>{html.escape(str(stage.get('review_path') or ''))}</td>"
        "</tr>"
        for stage in session["stages"]
    )
    action_html = render_action_controls(status)
    missing = acceptance.get("checks", []) if isinstance(acceptance, dict) else []
    missing_rows = "\n".join(
        f"<li><code>{html.escape(str(check.get('status')))}</code> {html.escape(str(check.get('id')))}: {html.escape(str(check.get('description')))}</li>"
        for check in missing
        if check.get("required_for_complete") and check.get("status") != "ok"
    )
    if not missing_rows:
        missing_rows = "<li>All required runtime checks passed.</li>"
    notice_html = f"<p class=\"notice\">{html.escape(notice)}</p>" if notice else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Doctor Runtime Review</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #172033; background: #f8fafc; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    section {{ background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 18px; margin: 16px 0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; vertical-align: top; }}
    button {{ padding: 8px 12px; border-radius: 6px; border: 1px solid #1d4ed8; background: #2563eb; color: #fff; cursor: pointer; }}
    button.secondary {{ border-color: #64748b; background: #64748b; }}
    textarea, input {{ width: 100%; box-sizing: border-box; padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; }}
    pre {{ white-space: pre-wrap; background: #101827; color: #f8fafc; padding: 14px; border-radius: 8px; max-height: 560px; overflow: auto; }}
    code {{ color: #7c2d12; }}
    .notice {{ background: #ecfdf5; border: 1px solid #86efac; padding: 10px; border-radius: 6px; }}
  </style>
</head>
<body>
<main>
  <h1>Doctor Runtime Review</h1>
  {notice_html}
  <section>
    <h2>Current Gate</h2>
    <p>Status: <code>{html.escape(status)}</code></p>
    <p>Session: <code>{html.escape(session_id)}</code></p>
    <p>Review file: <code>{html.escape(str(session['next'].get('review_file') or ''))}</code></p>
    <p>{html.escape(str(session['next'].get('message') or ''))}</p>
  </section>
  <section>
    <h2>Actions</h2>
    {action_html}
  </section>
  <section>
    <h2>Review Preview</h2>
    <pre>{html.escape(preview)}</pre>
  </section>
  <section>
    <h2>Stages</h2>
    <table><tr><th>Stage</th><th>Status</th><th>Review File</th></tr>{stage_rows}</table>
  </section>
  <section>
    <h2>Acceptance Gaps</h2>
    <ul>{missing_rows}</ul>
  </section>
</main>
</body>
</html>"""


def render_action_controls(status: str) -> str:
    if status in {"awaiting_context_generation", "context_rejected"}:
        return action_form("generate_context", "Generate Context", reason=True)
    if status == "awaiting_context_review":
        return action_form("approve_context", "Approve Context", reason=True) + action_form("reject_context", "Reject Context", reason=True, secondary=True)
    if status == "ready_for_agent_handoff":
        return action_form("export_handoff", "Export Agent Handoff", reason=True)
    if status == "ready_for_answer_prepare":
        return action_form("prepare_answer", "Prepare Answer Packet", reason=True)
    if status in {"awaiting_answer_output", "answer_rejected", "answer_failed"}:
        record_action = "record_answer" if status == "awaiting_answer_output" else "record_revised_answer"
        run_action = "run_answer" if status == "awaiting_answer_output" else "rerun_answer"
        return action_form(run_action, "Run Answer Command", command=True, reason=True, command_value="cat") + action_form(
            record_action,
            "Record Answer",
            answer_text=True,
            reason=True,
            secondary=True,
        )
    if status == "awaiting_answer_review":
        return action_form("approve_answer", "Approve Answer", reason=True) + action_form("reject_answer", "Reject Answer", reason=True, secondary=True)
    if status == "ready_for_execution_prepare":
        return action_form("prepare_execution", "Prepare Execution", reason=True)
    if status in {"awaiting_execution", "execution_rejected"}:
        return action_form("run_execution" if status == "awaiting_execution" else "rerun_execution", "Run Command", command=True, reason=True)
    if status == "awaiting_execution_review":
        return action_form("approve_execution", "Approve Execution", reason=True) + action_form("reject_execution", "Reject Execution", reason=True, secondary=True)
    if status == "complete":
        return "<p>All review gates are approved.</p>"
    return "<p>No clickable action is available for this state.</p>"


def action_form(
    action: str,
    label: str,
    *,
    reason: bool = False,
    answer_text: bool = False,
    command: bool = False,
    command_value: str = "python -c \"print('runtime artifact')\"",
    secondary: bool = False,
) -> str:
    fields = [f"<input type=\"hidden\" name=\"action\" value=\"{html.escape(action)}\">"]
    if answer_text:
        fields.append("<label>Answer text<br><textarea name=\"answer_text\" rows=\"8\"></textarea></label>")
    if command:
        fields.append(f"<label>Command<br><input name=\"command\" value=\"{html.escape(command_value)}\"></label>")
        fields.append("<label>CWD<br><input name=\"cwd\" value=\"\"></label>")
    if reason:
        fields.append("<label>Reason<br><input name=\"reason\" value=\"\"></label>")
    class_name = " class=\"secondary\"" if secondary else ""
    fields.append(f"<button{class_name} type=\"submit\">{html.escape(label)}</button>")
    return f"<form method=\"post\" action=\"/action\">{''.join(fields)}</form>"


def review_file_preview(root: Path, session: dict[str, Any], *, max_chars: int = 12_000) -> str:
    path_value = session["next"].get("review_file")
    if not path_value:
        return "No review file for the current state."
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
        root = root.resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return f"Review file is outside Doctor root or unavailable: {path_value}"
    if not path.exists() or not path.is_file():
        return f"Review file does not exist yet: {path}"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n[doctor runtime review: truncated]"
    return text


def safe_acceptance(root: Path, session_id: str) -> dict[str, Any]:
    try:
        return run_runtime_vm_acceptance(root, session_id)
    except Exception as exc:
        return {"status": "failed", "checks": [], "error": str(exc)}


def run_runtime_review_server(
    out_root: str | Path,
    session_id: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    session_id_value = session_id

    class Handler(RuntimeReviewRequestHandler):
        out_root = root
        session_id = session_id_value

    server = ThreadingHTTPServer((host, int(port)), Handler)
    url = f"http://{host}:{server.server_address[1]}/"
    print(json.dumps({"status": "serving", "url": url, "session_id": session_id, "out_root": str(root)}, ensure_ascii=False), flush=True)
    try:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
    return {"status": "stopped", "url": url, "session_id": session_id, "out_root": str(root)}


class RuntimeReviewRequestHandler(BaseHTTPRequestHandler):
    out_root: Path
    session_id: str

    def do_GET(self) -> None:
        if self.path not in {"/", "/index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        self.respond_html(render_runtime_review_html(self.out_root, self.session_id))

    def do_POST(self) -> None:
        if self.path != "/action":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        length = int(self.headers.get("Content-Length") or "0")
        payload = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        try:
            handle_runtime_review_action(
                self.out_root,
                self.session_id,
                action=form_value(payload, "action"),
                reason=form_value(payload, "reason"),
                answer_text=form_value(payload, "answer_text"),
                command=form_value(payload, "command"),
                cwd=form_value(payload, "cwd") or None,
            )
            self.respond_html(render_runtime_review_html(self.out_root, self.session_id, notice="Action applied."))
        except Exception as exc:
            self.respond_html(render_runtime_review_html(self.out_root, self.session_id, notice=f"Action failed: {exc}"), status=HTTPStatus.BAD_REQUEST)

    def respond_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def form_value(payload: dict[str, list[str]], key: str) -> str:
    values = payload.get(key) or [""]
    return values[0]
