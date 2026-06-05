"""Mark already-applied Drizzle migrations when schema was applied out-of-band.

Use when `pnpm db:migrate` fails with duplicate column / table errors because
the DB was partially migrated (e.g. manual SQL or an older branch).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "data" / "app.db"
MIGRATIONS = REPO / "apps" / "web" / "lib" / "db" / "migrations"
JOURNAL = MIGRATIONS / "meta" / "_journal.json"


def migration_hash(sql_path: Path) -> str:
    return hashlib.sha256(sql_path.read_bytes()).hexdigest()


def main() -> None:
    journal = json.loads(JOURNAL.read_text(encoding="utf-8"))
    pending_tags = ["0004_clip_editor_signals", "0005_twelvelabs_multimodal_analysis"]

    conn = sqlite3.connect(DB)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(__drizzle_migrations)")]
    hash_col = "hash" if "hash" in cols else cols[0]
    applied = {
        row[0] for row in conn.execute(f"SELECT {hash_col} FROM __drizzle_migrations")
    }

    for entry in journal["entries"]:
        tag = entry["tag"]
        if tag not in pending_tags:
            continue
        sql_file = MIGRATIONS / f"{tag}.sql"
        if not sql_file.exists():
            print(f"SKIP missing file {sql_file.name}")
            continue
        h = migration_hash(sql_file)
        if h in applied:
            print(f"OK already recorded: {tag}")
            continue
        conn.execute(
            "INSERT INTO __drizzle_migrations (hash, created_at) VALUES (?, ?)",
            (h, entry["when"]),
        )
        print(f"Recorded {tag} (hash={h[:12]}...)")
    conn.commit()

    rows = conn.execute(
        "SELECT created_at, substr(hash,1,12) FROM __drizzle_migrations ORDER BY created_at"
    ).fetchall()
    print("Journal now:", rows)
    conn.close()
    print("Done. Run `pnpm db:migrate` again — it should complete with no pending migrations.")


if __name__ == "__main__":
    main()
