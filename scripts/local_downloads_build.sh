#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

uv run ./agent-context build \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
