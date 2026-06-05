"""One-off helper to apply migration 0005 when drizzle-kit push is unavailable."""

from __future__ import annotations

import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "data" / "app.db"
SQL = REPO / "apps" / "web" / "lib" / "db" / "migrations" / "0005_twelvelabs_multimodal_analysis.sql"


def main() -> None:
    text = SQL.read_text(encoding="utf-8")
    conn = sqlite3.connect(DB)
    for stmt in text.split("--> statement-breakpoint"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    for want in ("external_video_indexes", "visual_segments", "highlight_candidates"):
        assert want in names, f"missing table {want}"
    conn.close()
    print("OK migration 0005 applied")


if __name__ == "__main__":
    main()
