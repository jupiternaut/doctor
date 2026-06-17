#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse one Douyin URL with Evil0ctal/Douyin_TikTok_Download_API.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--repo", required=True, help="Local path to the Douyin_TikTok_Download_API repository.")
    args = parser.parse_args()

    try:
        with contextlib.redirect_stdout(sys.stderr):
            payload = asyncio.run(parse_one(args.url, Path(args.repo)))
    except Exception as exc:
        print(f"douyin-tiktok-api parse failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload or {}, ensure_ascii=False, sort_keys=True))
    return 0


async def parse_one(url: str, repo: Path) -> dict:
    repo = repo.expanduser().resolve()
    if not repo.exists():
        raise FileNotFoundError(f"repo not found: {repo}")
    sys.path.insert(0, str(repo))

    from crawlers.hybrid.hybrid_crawler import HybridCrawler

    crawler = HybridCrawler()
    payload = await crawler.hybrid_parsing_single_video(url, minimal=True)
    if not isinstance(payload, dict):
        raise TypeError(f"expected dict payload, got {type(payload).__name__}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
