from __future__ import annotations

import hashlib
import json
import shlex
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import append_jsonl, ensure_dir, read_jsonl, write_jsonl, write_text
from .panel import record_panel_feedback
from .resolver import resolve_context


LAB_VERSION = "0.1"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


def run_lab(
    out_root: Path,
    *,
    text: str | None = None,
    image_paths: list[str] | None = None,
    source_scope: str = "all",
    limit: int = 8,
    once: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    if once or text or image_paths:
        return run_lab_once(
            out_root,
            text=text or "",
            image_paths=image_paths or [],
            source_scope=source_scope,
            limit=limit,
        )
    return run_interactive_lab(out_root, source_scope=source_scope, limit=limit)


def run_lab_once(
    out_root: Path,
    *,
    text: str,
    image_paths: list[str],
    source_scope: str = "all",
    limit: int = 8,
) -> dict[str, Any]:
    attachments = [describe_attachment(Path(value).expanduser()) for value in image_paths]
    goal = resolver_goal_for(text, attachments)
    if not goal.strip():
        raise ValueError("doctor lab requires text or at least one image path")

    resolve_result = resolve_context(out_root, goal, limit=max(1, limit), source_scope=source_scope)
    sources = read_jsonl(Path(resolve_result["sources_jsonl_path"]))
    run_id = datetime.now().strftime("lab-%Y%m%d%H%M%S%f")
    run_dir = ensure_dir(out_root / "lab" / "runs" / run_id)
    input_md_path = run_dir / "input.md"
    attachments_path = run_dir / "attachments.jsonl"
    run_json_path = run_dir / "run.json"

    attachment_records = [
        {
            "lab_attachment_version": LAB_VERSION,
            "source_group": "lab_inputs",
            "provider": "doctor_lab",
            "source_id": f"lab-attachment:{attachment['sha256'][:16]}" if attachment.get("sha256") else f"lab-attachment:{index}",
            "path": attachment["path"],
            "relative_path": Path(attachment["path"]).name if attachment.get("path") else None,
            "title": attachment.get("name") or Path(attachment["path"]).name,
            "summary": attachment_summary(attachment),
            "snippet": attachment_summary(attachment),
            "metadata": attachment,
        }
        for index, attachment in enumerate(attachments, start=1)
    ]
    write_jsonl(attachments_path, attachment_records)
    write_text(input_md_path, render_lab_input_markdown(text, attachments, run_id=run_id))
    prepend_lab_input_to_context(Path(resolve_result["context_md_path"]), input_md_path)

    result = {
        "lab_version": LAB_VERSION,
        "status": "ok",
        "run_id": run_id,
        "text": text,
        "images": attachments,
        "source_scope": source_scope,
        "limit": max(1, limit),
        "input_md_path": str(input_md_path),
        "attachments_jsonl_path": str(attachments_path),
        "context_md_path": resolve_result["context_md_path"],
        "sources_jsonl_path": resolve_result["sources_jsonl_path"],
        "manifest_json_path": resolve_result["manifest_json_path"],
        "resolution_plan_json_path": resolve_result["resolution_plan_json_path"],
        "top_sources": summarize_sources(sources, limit=max(1, limit)),
    }
    write_text(run_json_path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    result["run_json_path"] = str(run_json_path)
    append_jsonl(out_root / "lab" / "runs.jsonl", result)
    return result


def run_interactive_lab(out_root: Path, *, source_scope: str, limit: int) -> dict[str, Any]:
    print("Doctor Lab")
    print("输入任务文字；空行提交。命令：/image <path>、/run <text>、/good <n>、/bad <n>、/open <n>、/clear、/quit")
    buffer: list[str] = []
    image_paths: list[str] = []
    last_result: dict[str, Any] | None = None
    runs = 0
    while True:
        try:
            line = input("doctor> " if not buffer else "     > ")
        except EOFError:
            print()
            break
        stripped = line.strip()
        if stripped.startswith("/"):
            command, *args = shlex.split(stripped)
            command = command.lower()
            if command in {"/quit", "/exit"}:
                break
            if command == "/clear":
                buffer = []
                image_paths = []
                print("cleared")
                continue
            if command == "/image":
                if not args:
                    print("usage: /image /absolute/or/relative/path")
                    continue
                image_paths.append(args[0])
                print(f"attached image: {args[0]}")
                continue
            if command == "/run":
                prompt = " ".join(args).strip() or "\n".join(buffer).strip()
                last_result = run_lab_once(out_root, text=prompt, image_paths=image_paths, source_scope=source_scope, limit=limit)
                runs += 1
                print_lab_result(last_result)
                buffer = []
                image_paths = []
                continue
            if command in {"/good", "/use", "/bad", "/reject"}:
                if not last_result:
                    print("no previous lab result")
                    continue
                if not args:
                    print(f"usage: {command} <source-number> [reason]")
                    continue
                rating = "useful" if command in {"/good", "/use"} else "irrelevant"
                reason = " ".join(args[1:]).strip()
                feedback = record_lab_feedback(out_root, last_result, args[0], rating=rating, reason=reason)
                print(f"feedback recorded: {feedback['rating']} -> {feedback['source']}")
                continue
            if command == "/open":
                if not last_result:
                    print("no previous lab result")
                    continue
                source = source_by_index(last_result, args[0] if args else "")
                print(source.get("path") or source.get("source_id") if source else "source not found")
                continue
            print(f"unknown command: {command}")
            continue
        if not stripped and (buffer or image_paths):
            prompt = "\n".join(buffer).strip()
            last_result = run_lab_once(out_root, text=prompt, image_paths=image_paths, source_scope=source_scope, limit=limit)
            runs += 1
            print_lab_result(last_result)
            buffer = []
            image_paths = []
            continue
        buffer.append(line)
    return {"lab_version": LAB_VERSION, "status": "ended", "runs": runs, "out_root": str(out_root)}


def describe_attachment(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    suffix = resolved.suffix.lower()
    exists = resolved.exists()
    record: dict[str, Any] = {
        "path": str(resolved),
        "name": resolved.name,
        "source_type": "image" if suffix in IMAGE_EXTENSIONS else "unknown",
        "extension": suffix,
        "exists": exists,
        "status": "ok" if exists and suffix in IMAGE_EXTENSIONS else "missing" if not exists else "unsupported_extension",
    }
    if not exists or not resolved.is_file():
        return record
    stat = resolved.stat()
    record["size_bytes"] = stat.st_size
    record["mtime"] = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
    record["sha256"] = sha256_file(resolved)
    width, height = image_dimensions(resolved)
    if width and height:
        record["width"] = width
        record["height"] = height
    return record


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        header = path.read_bytes()[:4096]
    except OSError:
        return None, None
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        width, height = struct.unpack("<HH", header[6:10])
        return int(width), int(height)
    if header.startswith(b"\xff\xd8"):
        return jpeg_dimensions(path)
    return None, None


def jpeg_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        data = path.read_bytes()
    except OSError:
        return None, None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return int(width), int(height)
        index += max(2, segment_length)
    return None, None


def resolver_goal_for(text: str, attachments: list[dict[str, Any]]) -> str:
    lines = [text.strip()] if text.strip() else []
    if attachments:
        image_count = sum(1 for attachment in attachments if attachment.get("source_type") == "image")
        hint = attachment_intent_hint(text, attachments)
        lines.append(f"附加输入: {image_count or len(attachments)} 张图片; 图片内容尚未 OCR")
        if hint:
            lines.append(f"attachment_hint: {hint}")
    return "\n".join(lines).strip()


def attachment_intent_hint(text: str, attachments: list[dict[str, Any]]) -> str:
    lower = text.lower()
    if "简历" in text or "resume" in lower or "cv" in lower:
        return "resume_image"
    if "截图" in text or "screenshot" in lower:
        return "screenshot"
    if any(attachment.get("source_type") == "image" for attachment in attachments):
        return "image"
    return ""


def render_lab_input_markdown(text: str, attachments: list[dict[str, Any]], *, run_id: str) -> str:
    lines = [
        "# Doctor Lab Input",
        "",
        f"run_id: `{run_id}`",
        "",
        "## Text",
        "",
        text.strip() or "_No text input._",
        "",
        "## Images",
        "",
    ]
    if not attachments:
        lines.append("- No images attached.")
    for attachment in attachments:
        path = attachment.get("path") or ""
        name = attachment.get("name") or Path(path).name
        if attachment.get("source_type") == "image" and attachment.get("exists"):
            lines.append(f"![{name}]({path})")
        lines.append(f"- path: `{path}`")
        lines.append(f"- status: `{attachment.get('status')}`")
        if attachment.get("width") and attachment.get("height"):
            lines.append(f"- size: `{attachment['width']}x{attachment['height']}`")
        if attachment.get("sha256"):
            lines.append(f"- sha256: `{attachment['sha256']}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def prepend_lab_input_to_context(context_path: Path, input_md_path: Path) -> None:
    input_text = input_md_path.read_text(encoding="utf-8")
    existing = context_path.read_text(encoding="utf-8")
    write_text(context_path, f"{input_text}\n---\n\n{existing}")


def summarize_sources(sources: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    result = []
    for index, source in enumerate(sources[:limit], start=1):
        parts = source.get("resolver_score_parts") or {}
        result.append(
            {
                "rank": index,
                "source_group": source.get("source_group"),
                "source_id": source.get("source_id"),
                "source_chunk_id": source.get("source_chunk_id"),
                "path": source.get("path"),
                "relative_path": source.get("relative_path"),
                "score": source.get("score"),
                "grep_route": parts.get("grep_route"),
                "feedback": parts.get("feedback"),
                "route_selector": parts.get("route_selector"),
                "snippet": source.get("snippet"),
            }
        )
    return result


def print_lab_result(result: dict[str, Any]) -> None:
    print(f"context: {result['context_md_path']}")
    print(f"sources: {result['sources_jsonl_path']}")
    for source in result.get("top_sources") or []:
        print(
            f"{source['rank']}. {source.get('source_group')} score={source.get('score')} "
            f"grep={source.get('grep_route')} path={source.get('path')}"
        )


def record_lab_feedback(
    out_root: Path,
    lab_result: dict[str, Any],
    source_index: str,
    *,
    rating: str,
    reason: str = "",
) -> dict[str, Any]:
    source = source_by_index(lab_result, source_index)
    if not source:
        raise ValueError(f"source not found: {source_index}")
    source_key = source.get("source_chunk_id") or source.get("source_id") or source.get("path")
    event = {
        "lab_feedback_version": LAB_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "run_id": lab_result.get("run_id"),
        "source": source_key,
        "rating": rating,
        "reason": reason,
        "context_md_path": lab_result.get("context_md_path"),
        "run_json_path": lab_result.get("run_json_path"),
    }
    append_jsonl(out_root / "feedback" / "lab_feedback.jsonl", event)
    panel_result = record_panel_feedback(
        out_root,
        source=str(source_key),
        rating=rating,
        reason=reason,
        status_path=str(lab_result.get("run_json_path") or ""),
    )
    return {**event, "panel_feedback_path": panel_result["feedback_path"], "feedback_model_path": panel_result["feedback_model_path"]}


def source_by_index(lab_result: dict[str, Any], source_index: str) -> dict[str, Any] | None:
    try:
        index = int(source_index)
    except ValueError:
        return None
    sources = lab_result.get("top_sources") or []
    if index < 1 or index > len(sources):
        return None
    return sources[index - 1]


def attachment_summary(attachment: dict[str, Any]) -> str:
    parts = [
        f"name={attachment.get('name')}",
        f"status={attachment.get('status')}",
        f"type={attachment.get('source_type')}",
    ]
    if attachment.get("width") and attachment.get("height"):
        parts.append(f"dimensions={attachment['width']}x{attachment['height']}")
    return "; ".join(part for part in parts if part)
