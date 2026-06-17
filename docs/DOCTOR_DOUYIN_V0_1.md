# Doctor Douyin v0.1

`doctor-douyin` is a Doctor provider-preparation CLI. It converts
user-supplied Douyin share links and optional parsed metadata into Markdown KV
files, manifests, a small SQLite index, and a first-pass user profile.

It is intentionally metadata-first:

- it does not log into Douyin by default
- it does not download media unless `--download-assets` is explicitly set
- it does not read browser cookies by default
- it does not claim complete liked/collected/history coverage

## Commands

```bash
doctor-douyin init \
  --out /Users/gengrf/doctor-douyin-data

doctor-douyin sync \
  --source /Users/gengrf/doctor-douyin-data/urls.txt \
  --out /Users/gengrf/doctor-douyin-data

doctor-douyin profile \
  --out /Users/gengrf/doctor-douyin-data
```

By default, `sync` resolves Douyin short links such as `https://v.douyin.com/...`
and, when the local GitHub checkout exists, calls
`Evil0ctal/Douyin_TikTok_Download_API` as a built-in parser. That parser can
return public metadata such as publish time, author, BGM, statistics, cover, and
image/video asset URLs.

```bash
doctor-douyin sync \
  --source urls.txt \
  --parser douyin-tiktok-api \
  --parser-repo /Users/gengrf/Code/research/Douyin_TikTok_Download_API \
  --out /Users/gengrf/doctor-douyin-data
```

Use `--parser none` when you only want URL/share-text indexing.

To make signed cover/image/video URLs durable, explicitly download public assets:

```bash
doctor-douyin sync \
  --source urls.txt \
  --download-assets \
  --out /Users/gengrf/doctor-douyin-data
```

This writes files under `media/douyin/` and asset records to
`manifests/douyin_assets.jsonl`. Re-running the command skips files that already
exist locally.

For offline-only runs:

```bash
doctor-douyin sync \
  --source urls.txt \
  --parser none \
  --no-resolve-links \
  --out /Users/gengrf/doctor-douyin-data
```

For richer metadata, pass a sidecar JSONL file:

```bash
doctor-douyin sync \
  --source urls.txt \
  --metadata-jsonl parsed_douyin_metadata.jsonl \
  --out /Users/gengrf/doctor-douyin-data
```

Or call an external parser command that prints one JSON object per URL:

```bash
doctor-douyin sync \
  --source urls.txt \
  --parser-command "python parse_one.py --url {url}" \
  --out /Users/gengrf/doctor-douyin-data
```

## Outputs

```text
/Users/gengrf/doctor-douyin-data/
  urls.txt
  manifests/
    douyin_videos.jsonl
    douyin_authors.jsonl
    douyin_assets.jsonl
    failures.jsonl
  extracted/
    douyin/
      <aweme_id-or-url-hash>.md
  media/
    douyin/
      <source-key>/
        cover/
        image/
        video/
  indexes/
    douyin.sqlite
  profiles/
    douyin_user_profile.md
  reports/
    douyin_ingestion_report.md
```

Each Markdown file is one context object:

```markdown
# Douyin Video: 734001

kv:
  platform: "douyin"
  source_type: "url_list"
  source_url: "https://www.douyin.com/video/734001"
  resolved_url: "https://www.douyin.com/video/734001"
  content_type: "video"
  content_id: "734001"
  aweme_id: "734001"
  create_time: 1778580732
  create_time_iso: "2026-05-12T18:12:12+08:00"
  desc: "..."
  hashtags: ["开源", "推荐系统"]
  statistics: {"comment_count": 7, "digg_count": 120}
  music: {"author": "Demo Artist", "title": "Demo BGM"}
  asset_summary:
    cover_url: "https://..."
    local_cover_path: "/Users/gengrf/doctor-douyin-data/media/douyin/..."
    image_count: 12
    image_urls: ["https://..."]
    local_image_paths: ["/Users/gengrf/doctor-douyin-data/media/douyin/..."]
    downloaded_asset_count: 13

profile_signals:
  topics: ["开源", "推荐系统"]
  intent: null
  format: null
  confidence: 0.30
```

## Doctor Import

The v0.1 bridge is file-based. After `doctor-douyin sync`, Doctor can ingest the
Markdown KV directory:

```bash
doctor build \
  --scope /Users/gengrf/doctor-douyin-data/extracted/douyin \
  --goal "分析我的抖音兴趣画像，判断我适合做什么内容" \
  --out /Users/gengrf/agent-context-system \
  --with-index
```

## Open Source Adapter Boundary

The two researched projects should be used as optional adapters, not copied into
Doctor:

- `Evil0ctal/Douyin_TikTok_Download_API`: best for single-link parsing and API
  server mode. Doctor uses it as an optional adapter, not vendored source.
- `Johnserf-Seed/f2`: best for batch account modes and field-normalization
  patterns; its one-video snippet expects a user-provided cookie.

Later versions can add explicit adapter commands for posts, likes, and
collections, but cookies and raw private platform data must stay local and out
of git.
