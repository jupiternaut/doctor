from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import agent_context.douyin as douyin


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def test_douyin_sync_writes_markdown_manifest_sqlite_and_profile(tmp_path: Path) -> None:
    out = tmp_path / "douyin-data"
    source = tmp_path / "urls.txt"
    metadata = tmp_path / "metadata.jsonl"
    url = "https://www.douyin.com/video/734001"
    metadata_record = {
        "source_url": url,
        "platform": "douyin",
        "video_id": "734001",
        "desc": "开源推荐系统教程 #开源 #推荐系统",
        "author": {
            "nickname": "Doctor Lab",
            "sec_user_id": "sec-doctor",
        },
        "music": {
            "title": "Demo BGM",
        },
        "statistics": {
            "digg_count": 120,
            "comment_count": 7,
            "share_count": 3,
            "collect_count": 18,
        },
        "hashtags": ["开源", "推荐系统"],
    }
    source.write_text(
        "\n".join(
            [
                "# comments are ignored",
                f"复制打开抖音，看看这个视频 {url}",
                "https://v.douyin.com/no-parser/",
                "6.48 复制打开抖音，看看【東丁设计的图文作品】配色分享丨世界上公认最美的颜色。超级有感染力的颜色... https://v.douyin.com/UwtClU5jOEI/ m@Q.xS :6pm OkP:/ 09/27",
            ]
        ),
        encoding="utf-8",
    )
    write_jsonl(metadata, [metadata_record])

    assert douyin.main(
        [
            "--out",
            str(out),
            "sync",
            "--source",
            str(source),
            "--metadata-jsonl",
            str(metadata),
            "--parser",
            "none",
            "--no-resolve-links",
        ]
    ) == 0

    videos_path = out / "manifests" / "douyin_videos.jsonl"
    authors_path = out / "manifests" / "douyin_authors.jsonl"
    failures_path = out / "manifests" / "failures.jsonl"
    report_path = out / "reports" / "douyin_ingestion_report.md"
    profile_path = out / "profiles" / "douyin_user_profile.md"
    sqlite_path = out / "indexes" / "douyin.sqlite"

    assert videos_path.exists()
    assert authors_path.exists()
    assert failures_path.exists()
    assert report_path.exists()
    assert profile_path.exists()
    assert sqlite_path.exists()

    videos = read_jsonl(videos_path)
    authors = read_jsonl(authors_path)
    failures = read_jsonl(failures_path)
    assert len(videos) == 3
    assert len(authors) == 2
    assert failures == []

    parsed = next(video for video in videos if video["aweme_id"] == "734001")
    assert parsed["parser_status"] == "parsed_metadata"
    assert parsed["hashtags"] == ["开源", "推荐系统"]
    assert parsed["statistics"]["digg_count"] == 120
    extracted = Path(parsed["extracted_md_path"])
    assert extracted.exists()
    extracted_text = extracted.read_text(encoding="utf-8")
    assert "kv:" in extracted_text
    assert "profile_signals:" in extracted_text
    assert "开源推荐系统教程" in extracted_text

    metadata_only = next(video for video in videos if video["parser_status"] == "metadata_only_no_parser")
    assert Path(metadata_only["extracted_md_path"]).exists()

    conn = sqlite3.connect(sqlite_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM videos_fts").fetchone()[0]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()
    share_text = next(video for video in videos if video["parser_status"] == "share_text_only")
    assert share_text["author"]["nickname"] == "東丁设计"
    assert "配色分享" in share_text["desc"]
    assert share_text["share_text"]["media_type"] == "图文"

    assert count == 3
    assert fts_count == 3
    assert meta["provider_version"] == "0.1"

    profile = profile_path.read_text(encoding="utf-8")
    assert "Douyin User Profile" in profile
    assert "开源: 1" in profile

    assert douyin.main(
        [
            "--out",
            str(out),
            "sync",
            "--source",
            str(source),
            "--metadata-jsonl",
            str(metadata),
            "--parser",
            "none",
            "--no-resolve-links",
        ]
    ) == 0
    second_videos = read_jsonl(videos_path)
    assert all(video["status"] == "skipped" for video in second_videos)


def test_douyin_init_creates_workspace(tmp_path: Path) -> None:
    out = tmp_path / "douyin-data"

    assert douyin.main(["--out", str(out), "init"]) == 0

    assert (out / "urls.txt").exists()
    assert (out / "manifests" / "douyin_videos.jsonl").exists()
    assert (out / "extracted" / "douyin").is_dir()


def test_douyin_sync_resolves_short_link_to_note(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "douyin-data"
    source = tmp_path / "urls.txt"
    source.write_text(
        "6.48 复制打开抖音，看看【東丁设计的图文作品】配色分享丨世界上公认最美的颜色。超级有感染力的颜色... https://v.douyin.com/UwtClU5jOEI/ m@Q.xS :6pm OkP:/ 09/27\n",
        encoding="utf-8",
    )

    def fake_resolve(url: str, *, timeout_seconds: int) -> dict:
        assert url == "https://v.douyin.com/UwtClU5jOEI/"
        assert timeout_seconds == 15
        return {
            "resolved_url": "https://www.douyin.com/note/7638946075445560531?previous_page=app_code_link",
            "http_status": 404,
            "http_method": "HEAD",
            "content_type_header": "text/plain; charset=utf-8",
        }

    monkeypatch.setattr(douyin, "resolve_douyin_url", fake_resolve)

    assert douyin.main(["--out", str(out), "sync", "--source", str(source), "--parser", "none"]) == 0

    videos = read_jsonl(out / "manifests" / "douyin_videos.jsonl")
    assert len(videos) == 1
    video = videos[0]
    assert video["source_key"] == "douyin:note:7638946075445560531"
    assert video["content_type"] == "note"
    assert video["content_id"] == "7638946075445560531"
    assert video["resolved_url"].startswith("https://www.douyin.com/note/7638946075445560531")
    assert video["author"]["nickname"] == "東丁设计"
    assert video["parser_status"] == "share_text_only"

    extracted = Path(video["extracted_md_path"])
    assert extracted.name == "note-7638946075445560531.md"
    text = extracted.read_text(encoding="utf-8")
    assert "resolved_url" in text
    assert "content_type: \"note\"" in text
    assert "content_id: \"7638946075445560531\"" in text


def test_douyin_sync_parser_command_preserves_rich_media_fields(tmp_path: Path) -> None:
    out = tmp_path / "douyin-data"
    source = tmp_path / "urls.txt"
    parser_script = tmp_path / "parse_one.py"
    url = "https://www.douyin.com/note/7638946075445560531"
    source.write_text(url, encoding="utf-8")
    parser_script.write_text(
        "\n".join(
            [
                "import json",
                "payload = {",
                "  'type': 'image',",
                "  'platform': 'douyin',",
                "  'video_id': '7638946075445560531',",
                "  'desc': '配色分享丨世界上公认最美的颜色 #色彩搭配 #颜色',",
                "  'create_time': 1778580732,",
                "  'author': {'nickname': '東丁设计', 'uid': '55411342327262', 'sec_uid': 'sec-user', 'unique_id': 'dongding0808', 'signature': '设计配色'},",
                "  'music': {'mid': '7561263228492892211', 'title': 'Memories', 'author': 'VodKe/Klinn'},",
                "  'statistics': {'digg_count': 11453, 'comment_count': 310, 'collect_count': 14074, 'share_count': 2344, 'recommend_count': 233},",
                "  'cover_data': {'cover': {'url_list': ['https://cover.example/cover.jpg']}},",
                "  'image_data': {'no_watermark_image_list': ['https://image.example/1.jpg', 'https://image.example/2.jpg'], 'watermark_image_list': ['https://image.example/wm-1.jpg']},",
                "  'hashtags': [{'hashtag_name': '色彩搭配'}, {'hashtag_name': '颜色'}],",
                "}",
                "print(json.dumps(payload, ensure_ascii=False))",
            ]
        ),
        encoding="utf-8",
    )

    assert douyin.main(
        [
            "--out",
            str(out),
            "sync",
            "--source",
            str(source),
            "--no-resolve-links",
            "--parser-command",
            f"{sys.executable} {parser_script} --url {{url}}",
        ]
    ) == 0

    videos = read_jsonl(out / "manifests" / "douyin_videos.jsonl")
    assert len(videos) == 1
    video = videos[0]
    assert video["parser_status"] == "parsed_metadata"
    assert video["content_type"] == "note"
    assert video["media_type"] == "image"
    assert video["statistics"]["digg_count"] == 11453
    assert video["statistics"]["comment_count"] == 310
    assert video["statistics"]["recommend_count"] == 233
    assert video["music"]["mid"] == "7561263228492892211"
    assert video["music"]["title"] == "Memories"
    assert video["create_time_iso"]
    assert video["asset_summary"]["cover_url"] == "https://cover.example/cover.jpg"
    assert video["asset_summary"]["image_count"] == 2
    assert video["asset_summary"]["image_urls"] == ["https://image.example/1.jpg", "https://image.example/2.jpg"]

    markdown = Path(video["extracted_md_path"]).read_text(encoding="utf-8")
    assert "asset_summary" in markdown
    assert "Memories" in markdown
    assert "11453" in markdown


def test_douyin_sync_downloads_assets_to_local_media(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "douyin-data"
    source = tmp_path / "urls.txt"
    metadata = tmp_path / "metadata.jsonl"
    url = "https://www.douyin.com/note/7638946075445560531"
    source.write_text(url, encoding="utf-8")
    write_jsonl(
        metadata,
        [
            {
                "source_url": url,
                "type": "image",
                "platform": "douyin",
                "video_id": "7638946075445560531",
                "desc": "配色分享 #色彩搭配",
                "cover_data": {"cover": {"url_list": ["https://asset.example/cover.webp"]}},
                "image_data": {
                    "no_watermark_image_list": [
                        "https://asset.example/image-1.webp",
                        "https://asset.example/image-2.webp",
                    ]
                },
                "video_data": {"nwm_video_url": "https://asset.example/video.mp4"},
            }
        ],
    )

    def fake_download(url: str, destination: Path, *, timeout_seconds: int, max_bytes: int) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = f"downloaded:{url}".encode("utf-8")
        destination.write_bytes(payload)
        return {
            "status": "downloaded",
            "bytes": len(payload),
            "sha256": "fake-sha256",
            "content_type": "application/octet-stream",
        }

    monkeypatch.setattr(douyin, "download_asset_url", fake_download)

    assert douyin.main(
        [
            "--out",
            str(out),
            "sync",
            "--source",
            str(source),
            "--metadata-jsonl",
            str(metadata),
            "--parser",
            "none",
            "--no-resolve-links",
            "--download-assets",
        ]
    ) == 0

    videos = read_jsonl(out / "manifests" / "douyin_videos.jsonl")
    assets = read_jsonl(out / "manifests" / "douyin_assets.jsonl")
    assert len(videos) == 1
    assert len(assets) == 4
    assert {asset["asset_type"] for asset in assets} == {"cover", "image", "video"}
    assert all(asset["status"] == "downloaded" for asset in assets)
    assert all(Path(asset["local_path"]).exists() for asset in assets)

    video = videos[0]
    summary = video["asset_summary"]
    assert summary["downloaded_asset_count"] == 4
    assert Path(summary["local_cover_path"]).exists()
    assert len(summary["local_image_paths"]) == 2
    assert len(summary["local_video_paths"]) == 1

    markdown = Path(video["extracted_md_path"]).read_text(encoding="utf-8")
    assert "local_cover_path" in markdown
    assert "local_image_paths" in markdown
    assert "local_video_paths" in markdown
