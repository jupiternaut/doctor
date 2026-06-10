from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".jsonl",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".css",
    ".plist",
    ".strings",
    ".skill",
}

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".pptx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"}
MEDIA_EXTENSIONS = {".mp3", ".wav", ".m4a", ".mp4", ".mov"}
ARCHIVE_EXTENSIONS = {
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".dmg",
    ".pkg",
    ".app",
    ".iso",
}


@dataclass(frozen=True)
class FilePolicy:
    policy: str
    parser: str
    reason: str


def extension_for(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return ".tar.gz"
    return path.suffix.lower()


def policy_for(path: Path) -> FilePolicy:
    extension = extension_for(path)

    if extension in TEXT_EXTENSIONS:
        return FilePolicy("extract", "direct_text", "text-like file")
    if extension in DOCUMENT_EXTENSIONS:
        return FilePolicy("extract", "markitdown", "document conversion file")
    if extension in ARCHIVE_EXTENSIONS:
        return FilePolicy("metadata_only", "metadata_only", "archive/package files are not expanded in v0.1")
    if extension in IMAGE_EXTENSIONS:
        return FilePolicy("metadata_only", "metadata_only", "images are metadata-only in v0.1")
    if extension in MEDIA_EXTENSIONS:
        return FilePolicy("metadata_only", "metadata_only", "audio/video are metadata-only in v0.1")
    return FilePolicy("metadata_only", "metadata_only", "unknown or binary file")
