#!/usr/bin/env bash
set -euo pipefail

cd /Users/gengrf/agent-context-system
clear

GOAL="${1:-开源往事如何在番茄爆火，面向的读者是谁}"

echo "============================================================"
echo "Agent Context Live Run"
echo "Goal: ${GOAL}"
echo "Repo: $(pwd)"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "============================================================"
echo

echo "[0/5] 当前冷索引覆盖范围"
sqlite3 indexes/context.sqlite 'select scope, count(*) as documents from documents group by scope order by documents desc;' || true
echo

echo "[1/5] RAG query: agent-context query"
uv run ./agent-context query --query "${GOAL}" --limit 12
echo

echo "[2/5] 读取最新 RAG sources"
uv run python - <<'PY'
import json
from pathlib import Path

query_dirs = sorted(Path("queries").glob("*rag-*"), key=lambda p: p.stat().st_mtime)
latest = query_dirs[-1]
print("latest_query_dir=", latest)
print("context_md=", latest / "context.md")
print("sources_jsonl=", latest / "sources.jsonl")
print()
print("Top sources:")
for i, line in enumerate((latest / "sources.jsonl").read_text(encoding="utf-8").splitlines(), 1):
    record = json.loads(line)
    print(f"{i:02d}. score={record.get('score')} path={record.get('path')}")
    if i >= 8:
        break
PY
echo

echo "[3/5] Arena: 生成三条候选分支"
uv run ./agent-context arena --scope /Users/gengrf/Downloads --goal "${GOAL}" --skip-ingest
echo

echo "[4/5] 读取最新 Arena slate"
uv run python - <<'PY'
from pathlib import Path

arena_dirs = sorted(Path("packs").glob("*arena-*"), key=lambda p: p.stat().st_mtime)
latest = arena_dirs[-1]
print("latest_arena_dir=", latest)
print("slate_md=", latest / "slate.md")
print("slate_json=", latest / "slate.json")
print("slate_key_json=", latest / "slate_key.json")
print()
print("Candidate files:")
for path in sorted(latest.glob("candidate-*/answer.md")):
    print("-", path)
PY
echo

echo "[5/5] 三个答案分支：基于本次检索的人工可读分析"
uv run python - <<'PY'
import json
from pathlib import Path

query_dirs = sorted(Path("queries").glob("*rag-*"), key=lambda p: p.stat().st_mtime)
latest_q = query_dirs[-1]
sources = [
    json.loads(line)
    for line in (latest_q / "sources.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]

print("Evidence pack:", latest_q / "context.md")
print()
print("分支 A：番茄爆款爽文路线")
print("结论：把《开源往事》从技术史/开源史改写成强冲突、强人物、强连载钩子的商业技术群像文。")
print("爆火机制：前三章必须有矛盾：封闭巨头 vs 开源理想、天才程序员 vs 商业机器、免费软件 vs 资本垄断。每章末尾留一个“下一场战争”的钩子。")
print("目标读者：番茄里爱看逆袭、商战、互联网创业、时代群像的泛读者，不要求懂 Linux/GPL/微软，只需要能感到“弱者挑战巨头”的爽点。")
print()
print("分支 B：知识爽文 / 科普故事路线")
print("结论：保留开源史知识密度，但每个技术概念都用人物选择和时代利益翻译。")
print("爆火机制：把 Unix、GNU、Linux、微软、Sun、IBM 这些名词变成势力阵营；读者每章学到一个知识点，同时看到一个利益格局变化。")
print("目标读者：程序员、产品经理、互联网从业者、科技商业读者，以及喜欢“看故事顺便长知识”的男性读者。")
print()
print("分支 C：职场/个人成长路线")
print("结论：把开源运动写成普通开发者如何在大公司、商业压力、理想主义之间寻找位置的成长故事。")
print("爆火机制：用主角视角承接复杂历史：第一次接触开源、第一次被闭源卡脖子、第一次参与社区、第一次发现理想会被商业化。")
print("目标读者：年轻程序员、转码/学编程人群、AI 工具使用者、对“个人如何借时代浪潮翻身”有兴趣的读者。")
print()
print("当前检索证据前 5 条：")
for i, source in enumerate(sources[:5], 1):
    print(f"{i}. {source.get('path')} | score={source.get('score')}")
print()
print("限制：当前索引只覆盖 /Users/gengrf/Downloads，不代表本地所有项目。")
PY
echo

echo "============================================================"
echo "Run finished. Press Enter to close this window."
echo "============================================================"
read -r _
