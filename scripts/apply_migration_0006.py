"""One-off helper when pnpm db:migrate fails (Node/better-sqlite3 mismatch)."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"
MIGRATIONS = [
    ROOT / "apps" / "web" / "lib" / "db" / "migrations" / "0006_pipeline_analytics.sql",
    ROOT / "apps" / "web" / "lib" / "db" / "migrations" / "0007_clip_source_window.sql",
]


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS __drizzle_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT NOT NULL,
            created_at INTEGER
        )
        """
    )
    for sql_file in MIGRATIONS:
        if not sql_file.exists():
            print("skip missing", sql_file.name)
            continue
        sql = sql_file.read_text(encoding="utf-8")
        for stmt in sql.split("--> statement-breakpoint"):
            s = stmt.strip()
            if not s:
                continue
            try:
                cur.executescript(s)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    print("skip exists", sql_file.name, exc)
                else:
                    raise
        digest = hashlib.sha256(sql.encode()).hexdigest()
        cur.execute(
            "SELECT 1 FROM __drizzle_migrations WHERE hash = ?",
            (digest,),
        )
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO __drizzle_migrations (hash, created_at) VALUES (?, unixepoch())",
                (digest,),
            )
        print("applied", sql_file.name)
    conn.commit()
    conn.close()
    print("migrations complete")


if __name__ == "__main__":
    main()
