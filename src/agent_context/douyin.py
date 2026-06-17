from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shlex
import shutil
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .pack import snippet


DOUYIN_PROVIDER_VERSION = "0.1"
DOUYIN_MARKDOWN_VERSION = "0.3"
DEFAULT_OUT_ROOT = Path.home() / "doctor-douyin-data"
DEFAULT_DOUYIN_TIKTOK_API_REPO = Path.home() / "Code" / "research" / "Douyin_TikTok_Download_API"
MAX_MARKDOWN_TEXT_CHARS = 12000
DEFAULT_MAX_ASSET_BYTES = 250_000_000
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
CONTENT_ID_PATTERNS = (
    ("note", re.compile(r"/(?:share/)?note/(\d+)")),
    ("video", re.compile(r"/(?:share/)?video/(\d+)")),
    ("video", re.compile(r"[?&](?:aweme_id|modal_id|video_id)=(\d+)")),
)
HASHTAG_PATTERN = re.compile(r"#([\w\u4e00-\u9fff-]+)")
SHORT_LINK_HOSTS = {"v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DOUYIN_TIKTOK_API_UV_WITH = (
    "qrcode",
    "gmssl",
    "pycryptodomex",
    "httpx==0.27.0",
    "PyYAML",
    "pydantic==2.7.0",
    "tenacity",
    "browser-cookie3",
    "aiofiles",
    "rich",
    "user-agents",
    "importlib_resources",
)


@dataclass(frozen=True)
class DouyinPaths:
    root: Path
    manifests: Path
    extracted: Path
    media: Path
    indexes: Path
    profiles: Path
    reports: Path
    videos_jsonl: Path
    authors_jsonl: Path
    assets_jsonl: Path
    failures_jsonl: Path
    sqlite_path: Path
    profile_md: Path
    report_md: Path
    url_template: Path

    @classmethod
    def from_root(cls, out_root: Path) -> "DouyinPaths":
        root = out_root.expanduser().resolve()
        return cls(
            root=root,
            manifests=root / "manifests",
            extracted=root / "extracted" / "douyin",
            media=root / "media" / "douyin",
            indexes=root / "indexes",
            profiles=root / "profiles",
            reports=root / "reports",
            videos_jsonl=root / "manifests" / "douyin_videos.jsonl",
            authors_jsonl=root / "manifests" / "douyin_authors.jsonl",
            assets_jsonl=root / "manifests" / "douyin_assets.jsonl",
            failures_jsonl=root / "manifests" / "failures.jsonl",
            sqlite_path=root / "indexes" / "douyin.sqlite",
            profile_md=root / "profiles" / "douyin_user_profile.md",
            report_md=root / "reports" / "douyin_ingestion_report.md",
            url_template=root / "urls.txt",
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_root = Path(args.out).expanduser().resolve()

    if args.command == "init":
        result = init_douyin_workspace(out_root, overwrite=args.overwrite)
    elif args.command == "sync":
        parser_command, parser_name = resolve_parser_command(
            parser_name=args.parser,
            parser_command=args.parser_command,
            parser_repo=Path(args.parser_repo),
            parser_uv_bin=args.parser_uv_bin,
        )
        result = sync_douyin_urls(
            out_root,
            source_path=Path(args.source),
            metadata_jsonl=Path(args.metadata_jsonl) if args.metadata_jsonl else None,
            parser_command=parser_command,
            parser_name=parser_name,
            timeout_seconds=max(1, args.timeout_seconds),
            resolve_links=not args.no_resolve_links,
            resolve_timeout_seconds=max(1, args.resolve_timeout_seconds),
            download_assets=args.download_assets,
            asset_timeout_seconds=max(1, args.asset_timeout_seconds),
            max_asset_bytes=max(1, args.max_asset_bytes),
            build_profile=not args.no_profile,
        )
    elif args.command == "profile":
        result = build_douyin_profile(out_root)
    elif args.command == "report":
        result = write_douyin_report(out_root)
    else:
        parser.error(f"unknown command: {args.command}")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doctor-douyin")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_ROOT),
        help=f"Output root. Defaults to {DEFAULT_OUT_ROOT}.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a doctor-douyin workspace.")
    init.add_argument("--overwrite", action="store_true", help="Overwrite the sample urls.txt file.")

    sync = subparsers.add_parser("sync", help="Turn a URL list into Markdown KV and provider manifests.")
    sync.add_argument("--source", required=True, help="Text file containing one Douyin share URL or share line per row.")
    sync.add_argument(
        "--metadata-jsonl",
        default=None,
        help="Optional JSONL with parsed metadata records keyed by source_url/url/video_url.",
    )
    sync.add_argument(
        "--parser-command",
        default=None,
        help="Optional external command that prints JSON for one URL. Use {url} as the placeholder.",
    )
    sync.add_argument(
        "--parser",
        choices=("auto", "none", "douyin-tiktok-api"),
        default="auto",
        help="Built-in metadata parser. auto uses Douyin_TikTok_Download_API when the local repo is present.",
    )
    sync.add_argument(
        "--parser-repo",
        default=str(DEFAULT_DOUYIN_TIKTOK_API_REPO),
        help="Local path to Evil0ctal/Douyin_TikTok_Download_API for --parser douyin-tiktok-api.",
    )
    sync.add_argument("--parser-uv-bin", default="uv", help="uv binary used to run the built-in parser adapter.")
    sync.add_argument("--timeout-seconds", type=int, default=120, help="Timeout for each parser command call.")
    sync.add_argument("--no-resolve-links", action="store_true", help="Do not resolve Douyin short links before indexing.")
    sync.add_argument("--resolve-timeout-seconds", type=int, default=15, help="Timeout for each short-link resolution.")
    sync.add_argument("--download-assets", action="store_true", help="Download cover, image, and video assets to the local media directory.")
    sync.add_argument("--asset-timeout-seconds", type=int, default=60, help="Timeout for each asset download.")
    sync.add_argument("--max-asset-bytes", type=int, default=DEFAULT_MAX_ASSET_BYTES, help="Maximum bytes to download per asset.")
    sync.add_argument("--no-profile", action="store_true", help="Do not regenerate profiles/douyin_user_profile.md.")

    subparsers.add_parser("profile", help="Regenerate profiles/douyin_user_profile.md from the video manifest.")
    subparsers.add_parser("report", help="Regenerate reports/douyin_ingestion_report.md from manifests.")
    return parser


def init_douyin_workspace(out_root: Path, *, overwrite: bool = False) -> dict:
    paths = DouyinPaths.from_root(out_root)
    for directory in (paths.manifests, paths.extracted, paths.media, paths.indexes, paths.profiles, paths.reports):
        ensure_dir(directory)
    if overwrite or not paths.url_template.exists():
        write_text(
            paths.url_template,
            "\n".join(
                [
                    "# Put one Douyin share URL or share line per row.",
                    "# Example:",
                    "# https://v.douyin.com/xxxxxxxx/",
                    "",
                ]
            ),
        )
    for jsonl_path in (paths.videos_jsonl, paths.authors_jsonl, paths.assets_jsonl, paths.failures_jsonl):
        if not jsonl_path.exists():
            write_jsonl(jsonl_path, [])
    return {
        "provider_version": DOUYIN_PROVIDER_VERSION,
        "out": str(paths.root),
        "urls": str(paths.url_template),
        "videos_jsonl": str(paths.videos_jsonl),
        "authors_jsonl": str(paths.authors_jsonl),
        "assets_jsonl": str(paths.assets_jsonl),
        "failures_jsonl": str(paths.failures_jsonl),
    }


def sync_douyin_urls(
    out_root: Path,
    *,
    source_path: Path,
    metadata_jsonl: Path | None = None,
    parser_command: str | None = None,
    parser_name: str = "none",
    timeout_seconds: int = 120,
    resolve_links: bool = True,
    resolve_timeout_seconds: int = 15,
    download_assets: bool = False,
    asset_timeout_seconds: int = 60,
    max_asset_bytes: int = DEFAULT_MAX_ASSET_BYTES,
    build_profile: bool = True,
) -> dict:
    paths = DouyinPaths.from_root(out_root)
    init_douyin_workspace(paths.root)
    source_urls = read_source_urls(source_path)
    metadata_by_url = load_metadata_by_url(metadata_jsonl) if metadata_jsonl else {}
    previous_by_key = {record["source_key"]: record for record in read_jsonl(paths.videos_jsonl) if record.get("source_key")}
    previous_assets = read_jsonl(paths.assets_jsonl) if paths.assets_jsonl.exists() else []

    videos: list[dict] = []
    assets: list[dict] = [] if download_assets else previous_assets
    failures: list[dict] = []
    for item in source_urls:
        url = item["source_url"]
        resolution = link_resolution_record(url, enabled=resolve_links, timeout_seconds=resolve_timeout_seconds)
        if resolution.get("error"):
            failures.append(
                {
                    "provider": "douyin_video",
                    "provider_version": DOUYIN_PROVIDER_VERSION,
                    "source_url": url,
                    "stage": "resolve_link",
                    "error_type": resolution.get("error_type") or "LinkResolutionError",
                    "error": resolution.get("error") or "",
                    "recoverable": True,
                    "captured_at": now_iso(),
                }
            )
        resolved_url = str(resolution.get("resolved_url") or "")
        raw_metadata = metadata_by_url.get(url)
        if raw_metadata is None and resolved_url:
            raw_metadata = metadata_by_url.get(resolved_url)
        parser_error = None
        if raw_metadata is None and parser_command:
            parser_url = resolved_url or url
            try:
                raw_metadata = run_parser_command(parser_command, parser_url, timeout_seconds=timeout_seconds)
            except Exception as exc:  # pragma: no cover - exact subprocess errors vary.
                parser_error = {
                    "provider": "douyin_video",
                    "provider_version": DOUYIN_PROVIDER_VERSION,
                    "source_url": url,
                    "stage": "parser_command",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "recoverable": True,
                    "captured_at": now_iso(),
                }
                failures.append(parser_error)

        record = normalize_video_record(
            raw_metadata or {},
            source_url=url,
            original_line=item.get("original_line", ""),
            parser_error=parser_error,
            resolution=resolution,
        )
        force_write = False
        if download_assets:
            asset_records, asset_failures = download_record_assets(
                paths,
                record,
                timeout_seconds=asset_timeout_seconds,
                max_bytes=max_asset_bytes,
            )
            record["asset_downloads"] = asset_records
            attach_local_assets_to_summary(record, asset_records)
            assets.extend(asset_records)
            failures.extend(asset_failures)
            force_write = True
        stem = safe_stem(
            f"{record['content_type']}-{record['content_id']}"
            if record.get("content_type") and record.get("content_id")
            else f"url-{record['source_hash']}"
        )
        record["extracted_md_path"] = str(paths.extracted / f"{stem}.md")
        previous = previous_by_key.get(record["source_key"])
        if (
            not force_write
            and previous
            and previous.get("fingerprint") == record["fingerprint"]
            and Path(previous.get("extracted_md_path", "")).exists()
        ):
            record["status"] = "skipped"
            record["extracted_md_path"] = previous["extracted_md_path"]
        else:
            record["status"] = "written"
            markdown = render_video_markdown(record)
            write_text(Path(record["extracted_md_path"]), markdown)
        videos.append(record)

    removed_orphans = cleanup_orphan_extracted_files(paths, videos)
    write_jsonl(paths.videos_jsonl, videos)
    write_jsonl(paths.authors_jsonl, build_author_records(videos))
    if download_assets:
        write_jsonl(paths.assets_jsonl, assets)
    write_jsonl(paths.failures_jsonl, failures)
    build_douyin_sqlite(paths, videos, failures)
    report = write_douyin_report(paths.root)
    profile = build_douyin_profile(paths.root) if build_profile else None
    return {
        "provider_version": DOUYIN_PROVIDER_VERSION,
        "source": str(source_path.expanduser().resolve()),
        "out": str(paths.root),
        "videos": len(videos),
        "written": sum(1 for record in videos if record.get("status") == "written"),
        "skipped": sum(1 for record in videos if record.get("status") == "skipped"),
        "metadata_records": len(metadata_by_url),
        "parser": parser_name,
        "download_assets": download_assets,
        "assets": len(assets),
        "assets_downloaded": sum(1 for asset in assets if asset.get("status") == "downloaded"),
        "assets_skipped": sum(1 for asset in assets if asset.get("status") == "skipped"),
        "resolved": sum(1 for record in videos if record.get("resolved_url")),
        "resolve_links": resolve_links,
        "removed_orphan_markdown": removed_orphans,
        "failures": len(failures),
        "videos_jsonl": str(paths.videos_jsonl),
        "authors_jsonl": str(paths.authors_jsonl),
        "assets_jsonl": str(paths.assets_jsonl),
        "failures_jsonl": str(paths.failures_jsonl),
        "sqlite": str(paths.sqlite_path),
        "report": report["report"],
        "profile": profile["profile"] if profile else None,
    }


def read_source_urls(source_path: Path) -> list[dict]:
    source_path = source_path.expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"source file not found: {source_path}")
    records: list[dict] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), start=1):
        original = line.strip()
        if not original or original.startswith("#"):
            continue
        url = extract_first_url(original) or original
        url = url.strip().rstrip("，,。)")
        if url in seen:
            continue
        seen.add(url)
        records.append(
            {
                "source_url": url,
                "original_line": original,
                "line_number": line_number,
            }
        )
    return records


def extract_first_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def load_metadata_by_url(metadata_jsonl: Path) -> dict[str, dict]:
    records = read_jsonl(metadata_jsonl.expanduser().resolve())
    by_url: dict[str, dict] = {}
    for record in records:
        for key in ("source_url", "url", "share_url", "video_url"):
            value = record.get(key)
            if isinstance(value, str) and value:
                by_url[value] = record
        nested_url = nested_get(record, ("raw", "source_url"))
        if isinstance(nested_url, str) and nested_url:
            by_url[nested_url] = record
    return by_url


def resolve_parser_command(
    *,
    parser_name: str,
    parser_command: str | None,
    parser_repo: Path,
    parser_uv_bin: str,
) -> tuple[str | None, str]:
    if parser_command:
        return parser_command, "parser-command"
    if parser_name == "none":
        return None, "none"

    repo = parser_repo.expanduser().resolve()
    uv_path = shutil.which(parser_uv_bin)
    script = repo_root() / "scripts" / "douyin_tiktok_api_parse_one.py"
    available = repo.exists() and script.exists() and uv_path is not None
    if not available:
        if parser_name == "douyin-tiktok-api":
            missing = []
            if not repo.exists():
                missing.append(f"repo not found: {repo}")
            if not script.exists():
                missing.append(f"adapter script not found: {script}")
            if uv_path is None:
                missing.append(f"uv binary not found: {parser_uv_bin}")
            raise FileNotFoundError("; ".join(missing))
        return None, "none"

    return build_douyin_tiktok_api_command(script=script, repo=repo, uv_bin=uv_path), "douyin-tiktok-api"


def build_douyin_tiktok_api_command(*, script: Path, repo: Path, uv_bin: str) -> str:
    parts = [shlex.quote(uv_bin), "run", "--no-project"]
    for dependency in DOUYIN_TIKTOK_API_UV_WITH:
        parts.extend(["--with", shlex.quote(dependency)])
    parts.extend(
        [
            "python",
            shlex.quote(str(script)),
            "--repo",
            shlex.quote(str(repo)),
            "--url",
            "{url}",
        ]
    )
    return " ".join(parts)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_parser_command(command: str, url: str, *, timeout_seconds: int) -> dict:
    rendered = command.replace("{url}", url) if "{url}" in command else f"{command} {url}"
    completed = subprocess.run(
        shlex.split(rendered),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(stderr or f"parser command exited with {completed.returncode}")
    payload = json.loads(completed.stdout)
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and not has_video_identity(payload):
            return data
        return payload
    raise TypeError("parser command must print one JSON object")


def link_resolution_record(url: str, *, enabled: bool, timeout_seconds: int) -> dict:
    identity = extract_content_identity(url)
    if not enabled:
        return {
            "enabled": False,
            "source_url": url,
            "resolved_url": "",
            **identity,
        }
    if identity.get("content_id") and not should_resolve_url(url):
        return {
            "enabled": True,
            "source_url": url,
            "resolved_url": "",
            **identity,
        }
    if not should_resolve_url(url):
        return {
            "enabled": True,
            "source_url": url,
            "resolved_url": "",
            **identity,
        }
    try:
        resolved = resolve_douyin_url(url, timeout_seconds=timeout_seconds)
        resolved_url = str(resolved.get("resolved_url") or "")
        resolved_identity = extract_content_identity(resolved_url)
        return {
            "enabled": True,
            "source_url": url,
            **resolved,
            **resolved_identity,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "source_url": url,
            "resolved_url": "",
            **identity,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def should_resolve_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in SHORT_LINK_HOSTS


def resolve_douyin_url(url: str, *, timeout_seconds: int) -> dict:
    last_error: Exception | None = None
    for method in ("HEAD", "GET"):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return {
                    "resolved_url": response.geturl(),
                    "http_status": getattr(response, "status", None),
                    "http_method": method,
                    "content_type_header": response.headers.get("Content-Type", ""),
                }
        except HTTPError as exc:
            if exc.code in {403, 404, 405} and exc.url and exc.url != url:
                return {
                    "resolved_url": exc.url,
                    "http_status": exc.code,
                    "http_method": method,
                    "content_type_header": exc.headers.get("Content-Type", "") if exc.headers else "",
                }
            last_error = exc
        except URLError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("link resolution failed")


def normalize_video_record(
    raw_metadata: dict,
    *,
    source_url: str,
    original_line: str,
    parser_error: dict | None = None,
    resolution: dict | None = None,
) -> dict:
    raw = unwrap_payload(raw_metadata)
    detail = raw.get("aweme_detail") if isinstance(raw.get("aweme_detail"), dict) else raw
    resolution = resolution or {}
    resolved_url = str(resolution.get("resolved_url") or "")
    url_identity = extract_content_identity(resolved_url or source_url)
    share_text = parse_douyin_share_line(original_line, source_url)
    aweme_id = first_text(
        raw.get("aweme_id"),
        raw.get("video_id"),
        raw.get("id"),
        detail.get("aweme_id") if isinstance(detail, dict) else None,
        url_identity.get("content_id") if url_identity.get("content_type") == "video" else "",
    )
    content_type = first_text(
        raw.get("content_type"),
        url_identity.get("content_type"),
        parser_media_type_to_content_type(raw.get("type")),
        parser_media_type_to_content_type(raw.get("media_type")),
        share_media_type_to_content_type(share_text.get("media_type")),
        "video" if aweme_id else "",
    )
    media_type = first_text(raw.get("type"), raw.get("media_type"), content_type)
    content_id = first_text(
        raw.get("content_id"),
        raw.get("note_id"),
        aweme_id,
        url_identity.get("content_id"),
    )
    source_hash = stable_hash(source_url)[:16]
    source_key = f"douyin:{content_type}:{content_id}" if content_type and content_id else f"douyin:url:{source_hash}"
    file_stem = safe_stem(f"{content_type}-{content_id}" if content_type and content_id else f"url-{source_hash}")

    author_raw = first_dict(raw.get("author"), detail.get("author") if isinstance(detail, dict) else None)
    music_raw = first_dict(raw.get("music"), detail.get("music") if isinstance(detail, dict) else None)
    stats_raw = first_dict(raw.get("statistics"), detail.get("statistics") if isinstance(detail, dict) else None)
    cover_data = first_dict(raw.get("cover_data"), detail.get("cover_data") if isinstance(detail, dict) else None)
    image_data = first_dict(raw.get("image_data"), detail.get("image_data") if isinstance(detail, dict) else None)
    video_data = first_dict(raw.get("video_data"), detail.get("video_data") if isinstance(detail, dict) else None)
    desc = first_text(
        raw.get("desc"),
        raw.get("caption"),
        detail.get("desc") if isinstance(detail, dict) else None,
        share_text.get("desc"),
        "",
    )
    create_time = raw.get("create_time")
    if create_time is None and isinstance(detail, dict):
        create_time = detail.get("create_time")
    hashtag_source = raw.get("hashtags") or raw.get("text_extra")
    if hashtag_source is None and isinstance(detail, dict):
        hashtag_source = detail.get("text_extra")
    hashtags = normalize_hashtags(hashtag_source)
    hashtags = sorted(set([*hashtags, *HASHTAG_PATTERN.findall(desc)]))
    captured_at = now_iso()
    status_reason = "parsed_metadata" if raw else "metadata_only_no_parser"
    if not raw and share_text:
        status_reason = "share_text_only"
    if parser_error:
        status_reason = "metadata_only_parser_failed"

    author = normalize_author(author_raw)
    if not author.get("nickname") and share_text.get("author_name"):
        author["nickname"] = share_text["author_name"]
    public_record = {
        "provider": "douyin_video",
        "provider_version": DOUYIN_PROVIDER_VERSION,
        "markdown_version": DOUYIN_MARKDOWN_VERSION,
        "source_key": source_key,
        "source_hash": source_hash,
        "source_url": source_url,
        "resolved_url": resolved_url,
        "original_line": original_line,
        "aweme_id": aweme_id,
        "content_id": content_id,
        "content_type": content_type,
        "media_type": media_type,
        "platform": first_text(raw.get("platform"), "douyin"),
        "source_type": first_text(raw.get("source_type"), "url_list"),
        "desc": desc,
        "create_time": create_time,
        "create_time_iso": timestamp_to_iso(create_time),
        "author": author,
        "music": normalize_music(music_raw),
        "statistics": normalize_statistics(stats_raw),
        "hashtags": hashtags,
        "asset_summary": build_asset_summary(cover_data=cover_data, image_data=image_data, video_data=video_data),
        "cover_data": cover_data,
        "image_data": image_data,
        "video_data": video_data,
        "parser_status": status_reason,
        "parser_error": parser_error,
        "link_resolution": resolution,
        "share_text": share_text,
        "captured_at": captured_at,
    }
    public_record["text"] = build_record_text(public_record)
    public_record["fingerprint"] = stable_json_hash(
        {
            "source_url": source_url,
            "raw": raw_metadata,
            "public": {key: value for key, value in public_record.items() if key not in {"captured_at", "fingerprint"}},
        }
    )
    public_record["extracted_md_path"] = f"{file_stem}.md"
    return public_record


def unwrap_payload(record: dict) -> dict:
    if not isinstance(record, dict):
        return {}
    current = record
    for key in ("data", "result"):
        value = current.get(key)
        if isinstance(value, dict) and not has_video_identity(current):
            current = value
    return current


def has_video_identity(record: dict) -> bool:
    return any(key in record for key in ("aweme_id", "video_id", "aweme_detail", "desc", "caption"))


def extract_video_id(url: str) -> str | None:
    identity = extract_content_identity(url)
    return str(identity.get("content_id") or "") if identity.get("content_type") == "video" else None


def extract_content_identity(url: str) -> dict:
    if not url:
        return {"content_type": "", "content_id": ""}
    for content_type, pattern in CONTENT_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return {"content_type": content_type, "content_id": match.group(1)}
    return {"content_type": "", "content_id": ""}


def share_media_type_to_content_type(media_type: str | None) -> str:
    if media_type == "图文":
        return "note"
    if media_type == "视频":
        return "video"
    return ""


def parser_media_type_to_content_type(media_type: Any) -> str:
    text = first_text(media_type, "").lower()
    if text in {"image", "photo", "note", "images"}:
        return "note"
    if text in {"video", "aweme"}:
        return "video"
    return ""


def parse_douyin_share_line(original_line: str, source_url: str) -> dict:
    if not original_line:
        return {}
    text = original_line.replace(source_url, " ")
    text = URL_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return {}
    match = re.search(r"【(.+?)的(图文|视频)作品】(.+)", text)
    if match:
        author_name = match.group(1).strip()
        media_type = match.group(2).strip()
        desc = clean_share_desc(match.group(3))
        return {
            "author_name": author_name,
            "media_type": media_type,
            "desc": desc,
            "raw": original_line,
        }
    cleaned = clean_share_desc(text)
    return {"desc": cleaned, "raw": original_line} if cleaned else {}


def clean_share_desc(text: str) -> str:
    text = re.sub(r"^\S*\s*复制打开抖音，看看", "", text).strip()
    text = re.sub(r"\s+[A-Za-z]@[A-Za-z0-9.]+.*$", "", text).strip()
    text = re.sub(r"\s+\d{1,2}/\d{1,2}\s*$", "", text).strip()
    return text


def normalize_author(author: dict) -> dict:
    return {
        "nickname": first_text(author.get("nickname"), author.get("name"), author.get("unique_id"), ""),
        "uid": first_text(author.get("uid"), author.get("id"), ""),
        "sec_user_id": first_text(author.get("sec_uid"), author.get("sec_user_id"), ""),
        "unique_id": first_text(author.get("unique_id"), ""),
        "signature": first_text(author.get("signature"), ""),
        "avatar_url": first_media_url(author.get("avatar_thumb"), author.get("avatar_medium"), author.get("avatar_larger")),
        "follower_count": first_number(author.get("follower_count")),
        "following_count": first_number(author.get("following_count"), author.get("favoriting_count")),
        "total_favorited": first_number(author.get("total_favorited")),
        "aweme_count": first_number(author.get("aweme_count")),
    }


def normalize_music(music: dict) -> dict:
    return {
        "mid": first_text(music.get("mid"), music.get("id"), ""),
        "title": first_text(music.get("title"), music.get("music_title"), ""),
        "author": first_text(music.get("author"), music.get("owner_nickname"), ""),
        "duration": first_number(music.get("duration")),
        "play_url": first_media_url(music.get("play_url")),
        "cover_url": first_media_url(music.get("cover_hd"), music.get("cover_large"), music.get("cover_medium"), music.get("cover_thumb")),
    }


def normalize_statistics(statistics: dict) -> dict:
    keys = (
        "digg_count",
        "comment_count",
        "share_count",
        "collect_count",
        "play_count",
        "recommend_count",
        "admire_count",
        "download_count",
    )
    return {key: statistics.get(key) for key in keys if statistics.get(key) is not None}


def build_asset_summary(*, cover_data: dict, image_data: dict, video_data: dict) -> dict:
    image_urls = list_media_urls(image_data.get("no_watermark_image_list"))
    watermark_image_urls = list_media_urls(image_data.get("watermark_image_list"))
    summary = {
        "cover_url": first_media_url(
            nested_get(cover_data, ("cover",)),
            nested_get(cover_data, ("origin_cover",)),
            nested_get(cover_data, ("dynamic_cover",)),
        ),
        "image_count": len(image_urls),
        "image_urls": image_urls,
        "watermark_image_count": len(watermark_image_urls),
        "video_url_available": any(
            bool(video_data.get(key))
            for key in ("nwm_video_url", "nwm_video_url_HQ", "wm_video_url", "wm_video_url_HQ")
        ),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def download_record_assets(
    paths: DouyinPaths,
    record: dict,
    *,
    timeout_seconds: int,
    max_bytes: int,
) -> tuple[list[dict], list[dict]]:
    assets: list[dict] = []
    failures: list[dict] = []
    for source in collect_asset_sources(record):
        destination = asset_destination(paths, record, source)
        asset_record = {
            "provider": "douyin_asset",
            "provider_version": DOUYIN_PROVIDER_VERSION,
            "source_key": record.get("source_key"),
            "content_type": record.get("content_type"),
            "content_id": record.get("content_id"),
            "asset_key": source["asset_key"],
            "asset_type": source["asset_type"],
            "label": source["label"],
            "ordinal": source["ordinal"],
            "source_url": source["url"],
            "local_path": str(destination),
            "captured_at": now_iso(),
        }
        try:
            asset_record.update(
                download_asset_url(
                    source["url"],
                    destination,
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )
            )
        except Exception as exc:
            asset_record.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            failures.append(
                {
                    "provider": "douyin_asset",
                    "provider_version": DOUYIN_PROVIDER_VERSION,
                    "source_key": record.get("source_key"),
                    "source_url": source["url"],
                    "stage": "asset_download",
                    "asset_type": source["asset_type"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "recoverable": True,
                    "captured_at": now_iso(),
                }
            )
        assets.append(asset_record)
    return assets, failures


def collect_asset_sources(record: dict) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()

    def add(asset_type: str, label: str, ordinal: int, url: str) -> None:
        if not url or url in seen:
            return
        if not url.startswith(("http://", "https://")):
            return
        seen.add(url)
        sources.append(
            {
                "asset_key": f"{record.get('source_key')}:{asset_type}:{ordinal}",
                "asset_type": asset_type,
                "label": label,
                "ordinal": ordinal,
                "url": url,
            }
        )

    summary = record.get("asset_summary") or {}
    add("cover", "cover", 0, str(summary.get("cover_url") or ""))
    for index, url in enumerate(summary.get("image_urls") or [], start=1):
        add("image", "no_watermark", index, str(url))

    video_data = record.get("video_data") or {}
    for index, key in enumerate(("nwm_video_url", "nwm_video_url_HQ", "wm_video_url", "wm_video_url_HQ"), start=1):
        add("video", key, index, str(video_data.get(key) or ""))
    return sources


def asset_destination(paths: DouyinPaths, record: dict, source: dict) -> Path:
    source_key = str(record.get("source_key") or record.get("source_hash") or "unknown")
    source_stem = safe_stem(source_key.replace(":", "-"))
    asset_type = safe_stem(str(source.get("asset_type") or "asset"))
    label = safe_stem(str(source.get("label") or asset_type))
    ordinal = int(source.get("ordinal") or 0)
    ext = guess_asset_extension(str(source.get("url") or ""), asset_type=asset_type)
    if asset_type == "cover":
        filename = f"cover{ext}"
    elif asset_type == "image":
        filename = f"image-{ordinal:03d}{ext}"
    elif asset_type == "video":
        filename = f"video-{ordinal:03d}-{label}{ext}"
    else:
        filename = f"{asset_type}-{ordinal:03d}-{label}{ext}"
    return paths.media / source_stem / asset_type / filename


def download_asset_url(url: str, destination: Path, *, timeout_seconds: int, max_bytes: int) -> dict:
    ensure_dir(destination.parent)
    if destination.exists() and destination.stat().st_size > 0:
        return {
            "status": "skipped",
            "bytes": destination.stat().st_size,
            "sha256": file_sha256(destination),
            "content_type": "",
        }

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
            "Referer": "https://www.douyin.com/",
        },
        method="GET",
    )
    temp_path = destination.with_name(f".{destination.name}.tmp")
    hasher = hashlib.sha256()
    total = 0
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            content_length = first_number(response.headers.get("Content-Length"))
            if content_length is not None and content_length > max_bytes:
                raise ValueError(f"asset exceeds max bytes: {int(content_length)} > {max_bytes}")
            with temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"asset exceeds max bytes: {total} > {max_bytes}")
                    hasher.update(chunk)
                    handle.write(chunk)
        temp_path.replace(destination)
        return {
            "status": "downloaded",
            "bytes": total,
            "sha256": hasher.hexdigest(),
            "content_type": content_type,
        }
    finally:
        if temp_path.exists():
            temp_path.unlink()


def attach_local_assets_to_summary(record: dict, assets: list[dict]) -> None:
    summary = record.setdefault("asset_summary", {})
    successful = [asset for asset in assets if asset.get("status") in {"downloaded", "skipped"}]
    local_cover = next((asset.get("local_path") for asset in successful if asset.get("asset_type") == "cover"), "")
    local_images = [asset["local_path"] for asset in successful if asset.get("asset_type") == "image" and asset.get("local_path")]
    local_videos = [asset["local_path"] for asset in successful if asset.get("asset_type") == "video" and asset.get("local_path")]
    if local_cover:
        summary["local_cover_path"] = local_cover
    if local_images:
        summary["local_image_paths"] = local_images
    if local_videos:
        summary["local_video_paths"] = local_videos
    summary["downloaded_asset_count"] = len(successful)
    summary["failed_asset_count"] = sum(1 for asset in assets if asset.get("status") == "failed")


def guess_asset_extension(url: str, *, asset_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix and len(suffix) <= 8:
        guessed_type = mimetypes.guess_type(f"file{suffix}")[0]
        if guessed_type or suffix in {".webp", ".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".m4v"}:
            return suffix
    if asset_type in {"cover", "image"}:
        return ".jpg"
    if asset_type == "video":
        return ".mp4"
    return ".bin"


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def normalize_hashtags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return HASHTAG_PATTERN.findall(value)
    tags: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                tags.append(item.lstrip("#"))
            elif isinstance(item, dict):
                name = first_text(item.get("hashtag_name"), item.get("cha_name"), item.get("name"), "")
                if name:
                    tags.append(name.lstrip("#"))
    return tags


def render_video_markdown(record: dict) -> str:
    lines = [
        f"# Douyin {record_label(record)}",
        "",
        "kv:",
    ]
    for key in (
        "platform",
        "source_type",
        "source_url",
        "resolved_url",
        "content_type",
        "content_id",
        "media_type",
        "aweme_id",
        "create_time",
        "create_time_iso",
        "desc",
        "hashtags",
        "statistics",
        "music",
        "author",
        "asset_summary",
        "parser_status",
    ):
        value = record.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
    lines.extend(
        [
            "",
            "content:",
            f"  caption: {json.dumps(record.get('desc') or '', ensure_ascii=False)}",
            "",
            "profile_signals:",
            f"  topics: {json.dumps(record.get('hashtags') or [], ensure_ascii=False)}",
            "  intent: null",
            "  format: null",
            "  confidence: 0.30",
            "",
            "evidence:",
        ]
    )
    if record.get("desc"):
        lines.append(f"  - caption: {json.dumps(snippet(record['desc'], 600), ensure_ascii=False)}")
    if record.get("hashtags"):
        lines.append(f"  - hashtags: {json.dumps(record['hashtags'], ensure_ascii=False)}")
    if record.get("asset_summary"):
        lines.append(f"  - assets: {json.dumps(record['asset_summary'], ensure_ascii=False, sort_keys=True)}")
    if record.get("parser_error"):
        lines.append("  - limitation: parser command failed; this record is metadata-only.")
    elif record.get("parser_status") == "metadata_only_no_parser":
        lines.append("  - limitation: no parsed metadata was supplied; only the source URL is indexed.")
    lines.extend(
        [
            "",
            "doctor:",
            "  provider: douyin_video",
            f"  source_key: {json.dumps(record.get('source_key'), ensure_ascii=False)}",
            f"  extracted_at: {json.dumps(record.get('captured_at'), ensure_ascii=False)}",
            "",
        ]
    )
    return "\n".join(lines)


def record_label(record: dict) -> str:
    content_type = str(record.get("content_type") or "Video").title()
    content_id = str(record.get("content_id") or record.get("aweme_id") or record.get("source_hash") or "")
    return f"{content_type}: {content_id}" if content_id else content_type


def build_record_text(record: dict) -> str:
    parts = [
        record.get("desc") or "",
        " ".join(record.get("hashtags") or []),
        nested_get(record, ("author", "nickname")) or "",
        nested_get(record, ("music", "title")) or "",
    ]
    return snippet("\n".join(part for part in parts if part), MAX_MARKDOWN_TEXT_CHARS)


def build_author_records(videos: Iterable[dict]) -> list[dict]:
    authors: dict[str, dict] = {}
    for video in videos:
        author = video.get("author") or {}
        key = author.get("sec_user_id") or author.get("uid") or author.get("nickname")
        if not key:
            continue
        record = authors.setdefault(
            key,
            {
                "provider": "douyin_author",
                "provider_version": DOUYIN_PROVIDER_VERSION,
                "author_key": key,
                "nickname": author.get("nickname"),
                "uid": author.get("uid"),
                "sec_user_id": author.get("sec_user_id"),
                "unique_id": author.get("unique_id"),
                "video_count": 0,
                "source_keys": [],
            },
        )
        record["video_count"] += 1
        record["source_keys"].append(video["source_key"])
    return sorted(authors.values(), key=lambda item: (-item["video_count"], item.get("nickname") or ""))


def cleanup_orphan_extracted_files(paths: DouyinPaths, videos: Iterable[dict]) -> int:
    active_paths = {
        Path(video["extracted_md_path"]).expanduser().resolve()
        for video in videos
        if video.get("extracted_md_path")
    }
    if not paths.extracted.exists():
        return 0
    removed = 0
    for path in paths.extracted.glob("*.md"):
        resolved = path.resolve()
        if resolved in active_paths:
            continue
        path.unlink()
        removed += 1
    return removed


def build_douyin_sqlite(paths: DouyinPaths, videos: list[dict], failures: list[dict]) -> None:
    ensure_dir(paths.sqlite_path.parent)
    if paths.sqlite_path.exists():
        paths.sqlite_path.unlink()
    conn = sqlite3.connect(paths.sqlite_path)
    try:
        conn.executescript(
            """
            CREATE TABLE videos (
              source_key TEXT PRIMARY KEY,
              aweme_id TEXT,
              platform TEXT,
              source_type TEXT,
              source_url TEXT NOT NULL,
              resolved_url TEXT,
              content_type TEXT,
              content_id TEXT,
              author_name TEXT,
              desc TEXT,
              hashtags_json TEXT NOT NULL,
              statistics_json TEXT NOT NULL,
              parser_status TEXT,
              extracted_md_path TEXT NOT NULL,
              fingerprint TEXT NOT NULL,
              captured_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL
            );

            CREATE TABLE failures (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_url TEXT NOT NULL,
              stage TEXT,
              error_type TEXT,
              error TEXT,
              recoverable INTEGER,
              metadata_json TEXT NOT NULL
            );

            CREATE INDEX idx_videos_aweme_id ON videos(aweme_id);
            CREATE INDEX idx_videos_content_type ON videos(content_type);
            CREATE INDEX idx_videos_content_id ON videos(content_id);
            CREATE INDEX idx_videos_author_name ON videos(author_name);
            CREATE INDEX idx_videos_parser_status ON videos(parser_status);
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE videos_fts USING fts5(
                  source_key UNINDEXED,
                  source_url,
                  author_name,
                  desc,
                  hashtags
                )
                """
            )
            fts_enabled = True
        except sqlite3.OperationalError:
            fts_enabled = False

        for video in videos:
            author_name = nested_get(video, ("author", "nickname")) or ""
            hashtags = video.get("hashtags") or []
            conn.execute(
                """
                INSERT INTO videos (
                  source_key, aweme_id, platform, source_type, source_url, resolved_url,
                  content_type, content_id, author_name,
                  desc, hashtags_json, statistics_json, parser_status, extracted_md_path,
                  fingerprint, captured_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video.get("source_key"),
                    video.get("aweme_id"),
                    video.get("platform"),
                    video.get("source_type"),
                    video.get("source_url"),
                    video.get("resolved_url"),
                    video.get("content_type"),
                    video.get("content_id"),
                    author_name,
                    video.get("desc") or "",
                    json.dumps(hashtags, ensure_ascii=False, sort_keys=True),
                    json.dumps(video.get("statistics") or {}, ensure_ascii=False, sort_keys=True),
                    video.get("parser_status"),
                    video.get("extracted_md_path"),
                    video.get("fingerprint"),
                    video.get("captured_at"),
                    json.dumps(video, ensure_ascii=False, sort_keys=True),
                ),
            )
            if fts_enabled:
                conn.execute(
                    "INSERT INTO videos_fts (source_key, source_url, author_name, desc, hashtags) VALUES (?, ?, ?, ?, ?)",
                    (
                        video.get("source_key"),
                        video.get("source_url"),
                        author_name,
                        video.get("desc") or "",
                        " ".join(hashtags),
                    ),
                )
        for failure in failures:
            conn.execute(
                """
                INSERT INTO failures (source_url, stage, error_type, error, recoverable, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    failure.get("source_url") or "",
                    failure.get("stage") or "",
                    failure.get("error_type") or "",
                    failure.get("error") or "",
                    1 if failure.get("recoverable") else 0,
                    json.dumps(failure, ensure_ascii=False, sort_keys=True),
                ),
            )
        conn.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for key, value in {
            "provider_version": DOUYIN_PROVIDER_VERSION,
            "built_at": now_iso(),
            "videos": str(len(videos)),
            "failures": str(len(failures)),
            "fts_enabled": "true" if fts_enabled else "false",
        }.items():
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def build_douyin_profile(out_root: Path) -> dict:
    paths = DouyinPaths.from_root(out_root)
    videos = read_jsonl(paths.videos_jsonl)
    hashtag_counts = Counter(tag for video in videos for tag in video.get("hashtags") or [])
    author_counts = Counter((video.get("author") or {}).get("nickname") for video in videos if (video.get("author") or {}).get("nickname"))
    parser_counts = Counter(video.get("parser_status") for video in videos)
    lines = [
        "# Douyin User Profile",
        "",
        f"generated_at: {now_iso()}",
        f"provider_version: {DOUYIN_PROVIDER_VERSION}",
        f"video_count: {len(videos)}",
        "",
        "## Topic Signals",
        "",
    ]
    if hashtag_counts:
        for tag, count in hashtag_counts.most_common(20):
            lines.append(f"- {tag}: {count}")
    else:
        lines.append("- No hashtag signals yet.")
    lines.extend(["", "## Author Signals", ""])
    if author_counts:
        for author, count in author_counts.most_common(20):
            lines.append(f"- {author}: {count}")
    else:
        lines.append("- No author signals yet.")
    lines.extend(["", "## Parser Coverage", ""])
    for status, count in parser_counts.most_common():
        lines.append(f"- {status or 'unknown'}: {count}")
    lines.extend(
        [
            "",
            "## Doctor Import",
            "",
            f"- Markdown KV directory: `{paths.extracted}`",
            f"- Video manifest: `{paths.videos_jsonl}`",
            f"- Asset manifest: `{paths.assets_jsonl}`",
            f"- Local media directory: `{paths.media}`",
            f"- SQLite index: `{paths.sqlite_path}`",
            "",
            "## Limitations",
            "",
            "- v0.1 does not log into Douyin, transcribe audio, or OCR video frames by default.",
            "- Media download is explicit via `--download-assets` and only covers public cover/image/video URLs exposed by the parser.",
            "- Liked, collected, and browsing-history coverage depends on an explicitly configured external provider in later versions.",
            "",
        ]
    )
    write_text(paths.profile_md, "\n".join(lines))
    return {
        "provider_version": DOUYIN_PROVIDER_VERSION,
        "profile": str(paths.profile_md),
        "videos": len(videos),
        "top_topics": hashtag_counts.most_common(10),
        "top_authors": author_counts.most_common(10),
    }


def write_douyin_report(out_root: Path) -> dict:
    paths = DouyinPaths.from_root(out_root)
    videos = read_jsonl(paths.videos_jsonl)
    assets = read_jsonl(paths.assets_jsonl)
    failures = read_jsonl(paths.failures_jsonl)
    parser_counts = Counter(video.get("parser_status") for video in videos)
    asset_status_counts = Counter(asset.get("status") for asset in assets)
    asset_type_counts = Counter(asset.get("asset_type") for asset in assets)
    lines = [
        "# Douyin Ingestion Report",
        "",
        f"generated_at: {now_iso()}",
        f"provider_version: {DOUYIN_PROVIDER_VERSION}",
        "",
        "## Counts",
        "",
        f"- videos: {len(videos)}",
        f"- assets: {len(assets)}",
        f"- failures: {len(failures)}",
        f"- markdown_files: {sum(1 for video in videos if video.get('extracted_md_path'))}",
        "",
        "## Parser Status",
        "",
    ]
    for status, count in parser_counts.most_common():
        lines.append(f"- {status or 'unknown'}: {count}")
    lines.extend(["", "## Asset Status", ""])
    if asset_status_counts:
        for status, count in asset_status_counts.most_common():
            lines.append(f"- {status or 'unknown'}: {count}")
    else:
        lines.append("- No downloaded assets yet.")
    lines.extend(["", "## Asset Types", ""])
    if asset_type_counts:
        for asset_type, count in asset_type_counts.most_common():
            lines.append(f"- {asset_type or 'unknown'}: {count}")
    else:
        lines.append("- No asset type signals yet.")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- videos_jsonl: `{paths.videos_jsonl}`",
            f"- authors_jsonl: `{paths.authors_jsonl}`",
            f"- assets_jsonl: `{paths.assets_jsonl}`",
            f"- failures_jsonl: `{paths.failures_jsonl}`",
            f"- extracted_dir: `{paths.extracted}`",
            f"- media_dir: `{paths.media}`",
            f"- sqlite: `{paths.sqlite_path}`",
            f"- profile: `{paths.profile_md}`",
            "",
            "## Next Steps",
            "",
            "- Add explicit user-authorized batch mode for posts, likes, and collections.",
            "- Add OCR and audio/video transcription as separate, explicit steps.",
            "",
        ]
    )
    write_text(paths.report_md, "\n".join(lines))
    return {
        "provider_version": DOUYIN_PROVIDER_VERSION,
        "report": str(paths.report_md),
        "videos": len(videos),
        "assets": len(assets),
        "failures": len(failures),
    }


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return text
    return ""


def first_number(*values: Any) -> int | float | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            try:
                return int(text)
            except ValueError:
                try:
                    return float(text)
                except ValueError:
                    continue
    return None


def first_media_url(*values: Any) -> str:
    for value in values:
        urls = list_media_urls(value)
        if urls:
            return urls[0]
    return ""


def list_media_urls(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(value, dict):
        for key in ("url_list", "download_url_list", "urls"):
            urls = list_media_urls(value.get(key))
            if urls:
                return urls
        return list_media_urls(value.get("url"))
    if isinstance(value, (list, tuple)):
        urls: list[str] = []
        for item in value:
            urls.extend(list_media_urls(item))
        return urls
    return []


def timestamp_to_iso(value: Any) -> str:
    number = first_number(value)
    if number is None:
        return ""
    timestamp = float(number)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def first_dict(*values: Any) -> dict:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def nested_get(record: dict, path: tuple[str, ...]) -> Any:
    current: Any = record
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return stem[:80] or stable_hash(value)[:16]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
