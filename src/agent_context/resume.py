from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import write_jsonl, write_text


RESUME_SCHEMA_VERSION = "0.1"
KNOWN_TECHNOLOGIES = (
    "Python",
    "FastAPI",
    "LangChain",
    "FAISS",
    "Sentence-Transformers",
    "Ollama",
    "Qwen",
    "MySQL",
    "Linux",
    "Docker",
    "Docker Compose",
    "HTML",
    "CSS",
    "JavaScript",
    "RAG",
    "Prompt",
    "Claude",
    "GPT",
    "OpenClaw",
    "WebSocket",
    "Feishu",
    "飞书",
)


def extract_resume_from_attachments(attachments: list[dict[str, Any]], run_dir: Path) -> dict[str, Any] | None:
    image_attachments = [
        attachment
        for attachment in attachments
        if attachment.get("source_type") == "image" and attachment.get("exists")
    ]
    if not image_attachments:
        return None

    ocr_results = [ocr_image(Path(attachment["path"])) for attachment in image_attachments]
    combined_text = "\n\n".join(result.get("text") or "" for result in ocr_results).strip()
    parsed = parse_resume_text(combined_text)
    resume = {
        "resume_schema_version": RESUME_SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "provider": "doctor_resume_ocr",
        "source_group": "lab_inputs",
        "attachments": image_attachments,
        "ocr": ocr_results,
        **parsed,
    }
    resume["redacted_ocr_text"] = redact_resume_contact_info(resume.get("ocr_text") or "")
    resume["markdown"] = render_resume_markdown(resume)

    resume_json_path = run_dir / "resume.json"
    resume_md_path = run_dir / "resume.md"
    resume_sources_path = run_dir / "resume_sources.jsonl"
    write_text(resume_json_path, json.dumps(resume, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(resume_md_path, resume["markdown"])
    write_jsonl(resume_sources_path, [resume_source_record(resume)])
    resume["resume_json_path"] = str(resume_json_path)
    resume["resume_md_path"] = str(resume_md_path)
    resume["resume_sources_jsonl_path"] = str(resume_sources_path)
    return resume


def ocr_image(path: Path) -> dict[str, Any]:
    vision = run_vision_ocr(path)
    if vision.get("status") == "ok" and vision.get("text"):
        return vision
    tesseract = run_tesseract_ocr(path)
    if tesseract.get("status") == "ok" and tesseract.get("text"):
        return tesseract
    return {
        "ocr_schema_version": RESUME_SCHEMA_VERSION,
        "status": "failed",
        "engine": "vision+tesseract",
        "path": str(path),
        "text": "",
        "warnings": [warning for warning in [vision.get("warning"), tesseract.get("warning")] if warning],
        "attempts": [vision, tesseract],
    }


def run_vision_ocr(path: Path) -> dict[str, Any]:
    if not shutil.which("swift"):
        return ocr_unavailable("vision", path, "swift command not found")
    script = """
import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path) else {
    FileHandle.standardError.write("image_load_failed".data(using: .utf8)!)
    exit(2)
}
var rect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
    FileHandle.standardError.write("cgimage_failed".data(using: .utf8)!)
    exit(3)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "en-US"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])
let observations = request.results ?? []
for observation in observations {
    if let text = observation.topCandidates(1).first?.string {
        print(text)
    }
}
"""
    try:
        with tempfile.TemporaryDirectory(prefix="doctor-vision-ocr-") as temp_dir:
            script_path = Path(temp_dir) / "ocr.swift"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                ["swift", str(script_path), str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ocr_unavailable("vision", path, f"{type(exc).__name__}: {exc}")
    if completed.returncode != 0:
        return ocr_unavailable("vision", path, completed.stderr.strip() or f"exit {completed.returncode}")
    return {
        "ocr_schema_version": RESUME_SCHEMA_VERSION,
        "status": "ok",
        "engine": "macos_vision",
        "path": str(path),
        "text": compact_ocr_text(completed.stdout),
    }


def run_tesseract_ocr(path: Path) -> dict[str, Any]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ocr_unavailable("tesseract", path, "tesseract command not found")
    language = tesseract_language()
    try:
        completed = subprocess.run(
            [tesseract, str(path), "stdout", "-l", language, "--psm", "6"],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ocr_unavailable("tesseract", path, f"{type(exc).__name__}: {exc}")
    if completed.returncode != 0:
        return ocr_unavailable("tesseract", path, completed.stderr.strip() or f"exit {completed.returncode}")
    return {
        "ocr_schema_version": RESUME_SCHEMA_VERSION,
        "status": "ok",
        "engine": "tesseract",
        "language": language,
        "path": str(path),
        "text": compact_ocr_text(completed.stdout),
    }


def tesseract_language() -> str:
    try:
        completed = subprocess.run(["tesseract", "--list-langs"], check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "eng"
    langs = set(completed.stdout.split())
    if {"chi_sim", "eng"} <= langs:
        return "chi_sim+eng"
    if "chi_sim" in langs:
        return "chi_sim"
    return "eng"


def ocr_unavailable(engine: str, path: Path, warning: str) -> dict[str, Any]:
    return {
        "ocr_schema_version": RESUME_SCHEMA_VERSION,
        "status": "unavailable",
        "engine": engine,
        "path": str(path),
        "text": "",
        "warning": warning,
    }


def compact_ocr_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_resume_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return {
        "ocr_text": text,
        "target_role": first_match(text, (r"求职意向[:：]?\s*([^\n]+)", r"AI\s*应用实习生")),
        "education": lines_matching(lines, ("学院", "本科", "专业", "课程", "教育背景")),
        "technologies": technologies_for(text),
        "projects": project_lines(lines),
        "sections": section_markers(lines),
        "limits": resume_limits(text),
    }


def first_match(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip() if match.groups() else match.group(0).strip()
    return ""


def lines_matching(lines: list[str], markers: tuple[str, ...]) -> list[str]:
    return [line for line in lines if any(marker.lower() in line.lower() for marker in markers)][:12]


def technologies_for(text: str) -> list[str]:
    lower = text.lower()
    hits = []
    for technology in KNOWN_TECHNOLOGIES:
        if technology.lower() in lower:
            hits.append(technology)
    return list(dict.fromkeys(hits))


def project_lines(lines: list[str]) -> list[str]:
    markers = ("项目", "Chat", "Assistant", "RAG", "OpenClaw", "knowledge", "robot", "机器人")
    return [line for line in lines if any(marker.lower() in line.lower() for marker in markers)][:20]


def section_markers(lines: list[str]) -> list[str]:
    markers = ("基本信息", "教育背景", "专业技能", "项目经历", "自我评价")
    found = []
    for line in lines:
        for marker in markers:
            if marker in line:
                found.append(marker)
    return list(dict.fromkeys(found))


def resume_limits(text: str) -> list[str]:
    limits = []
    if not text.strip():
        limits.append("OCR returned no text.")
    if len(text.strip()) < 120:
        limits.append("OCR text is short; manual review of the image is required.")
    return limits


def redact_resume_contact_info(text: str) -> str:
    redacted_lines: list[str] = []
    for line in text.splitlines():
        if any(marker in line.lower() for marker in ("电子邮箱", "联系电话", "邮箱", "电话", "email", "e-mail")):
            label = re.split(r"[:：]", line, maxsplit=1)[0].strip() or "contact"
            redacted_lines.append(f"{label}：[REDACTED_CONTACT]")
            continue
        line = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", line)
        line = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)", "[REDACTED_PHONE]", line)
        redacted_lines.append(line)
    return "\n".join(redacted_lines)


def render_resume_markdown(resume: dict[str, Any]) -> str:
    lines = [
        "# Resume OCR Evidence",
        "",
        f"- Provider: `{resume.get('provider')}`",
        f"- OCR engines: {', '.join(result.get('engine', '') for result in resume.get('ocr') or [])}",
        f"- Target role: {resume.get('target_role') or 'unknown'}",
        f"- Technologies: {', '.join(resume.get('technologies') or []) or 'unknown'}",
        "",
        "## Education",
        "",
    ]
    lines.extend(f"- {line}" for line in resume.get("education") or ["No education lines extracted."])
    lines.extend(["", "## Projects", ""])
    lines.extend(f"- {line}" for line in resume.get("projects") or ["No project lines extracted."])
    lines.extend(["", "## OCR Text", "", "```text", resume.get("redacted_ocr_text") or resume.get("ocr_text") or "", "```", ""])
    if resume.get("limits"):
        lines.extend(["## Limits", ""])
        lines.extend(f"- {limit}" for limit in resume["limits"])
        lines.append("")
    return "\n".join(lines)


def resume_source_record(resume: dict[str, Any]) -> dict[str, Any]:
    attachment = (resume.get("attachments") or [{}])[0]
    path = str(attachment.get("path") or "")
    source_id = f"resume-ocr:{attachment.get('sha256', 'unknown')[:16]}"
    return {
        "type": "resume_ocr",
        "source_id": source_id,
        "source_group": "lab_inputs",
        "provider": "doctor_resume_ocr",
        "path": path,
        "relative_path": Path(path).name if path else None,
        "title": "Resume OCR Evidence",
        "summary": f"target_role={resume.get('target_role') or 'unknown'}; technologies={', '.join(resume.get('technologies') or [])}",
        "snippet": resume.get("markdown", "")[:1800],
        "text": resume.get("redacted_ocr_text") or resume.get("ocr_text") or "",
        "source_type": "document",
        "score": 1.0 if resume.get("ocr_text") else 0.2,
        "metadata": {
            "target_role": resume.get("target_role"),
            "technologies": resume.get("technologies") or [],
            "ocr_statuses": [result.get("status") for result in resume.get("ocr") or []],
        },
    }
