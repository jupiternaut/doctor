from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main


def test_file_catalog_cli_builds_catalog_and_searches_filename(tmp_path: Path, capsys) -> None:
    scope = tmp_path / "scope"
    nested = scope / "notes"
    nested.mkdir(parents=True)
    (nested / "needle.md").write_text("# Needle\n", encoding="utf-8")
    (scope / "other.txt").write_text("other", encoding="utf-8")
    out = tmp_path / "out"

    assert main(["file-catalog", "--scope", str(scope), "--out", str(out), "--reset"]) == 0
    catalog = json.loads(capsys.readouterr().out)

    assert catalog["failure_count"] == 0
    assert catalog["entries_indexed"] >= 4
    assert Path(catalog["db_path"]).exists()
    assert Path(catalog["report_path"]).exists()
    assert Path(catalog["failures_path"]).exists()

    assert main(["file-search", "--query", "needle", "--out", str(out), "--limit", "5"]) == 0
    search = json.loads(capsys.readouterr().out)

    assert search["query"] == "needle"
    needle = next(result for result in search["results"] if Path(result["path"]).name == "needle.md")
    assert needle["scope"] == str(scope)
    assert needle["source_zone"]
    assert isinstance(needle["source_weight"], float)
