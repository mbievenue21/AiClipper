"""Heal projects that are stuck pending with no jobs, or failed with no error.

For each affected project, mark it ``failed`` with a clear note so the user
can either retry or delete from the UI. Safe to run repeatedly.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

db = Path(__file__).resolve().parents[3] / "data" / "app.db"
now_ms = int(time.time() * 1000)
conn = sqlite3.connect(db)

orphans = conn.execute(
    """
    SELECT p.id, p.name, p.status
    FROM projects p
    LEFT JOIN jobs j ON j.project_id = p.id
    WHERE p.status IN ('pending', 'ingesting', 'transcribing', 'analyzing')
    GROUP BY p.id
    HAVING SUM(CASE WHEN j.status IN ('pending','running') THEN 1 ELSE 0 END) = 0
       AND SUM(CASE WHEN j.status IN ('succeeded') THEN 1 ELSE 0 END) = 0
    """,
).fetchall()

if not orphans:
    print("No orphans to heal.")
else:
    for row in orphans:
        print(f"healing {row[0]} (name={row[1]!r} status={row[2]})")
        conn.execute(
            """UPDATE projects
               SET status='failed',
                   notes='Worker was unavailable when ingest was requested. Delete this project and create a new one.',
                   updated_at=?
               WHERE id=?""",
            (now_ms, row[0]),
        )
    conn.commit()
    print(f"healed {len(orphans)} project(s)")

conn.close()
