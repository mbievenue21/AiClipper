"""Quick DB peek: last few projects + all unfinished jobs."""
import sqlite3
import time
from pathlib import Path

db = Path(__file__).resolve().parents[3] / "data" / "app.db"
c = sqlite3.connect(db)

now = int(time.time() * 1000)
print("--- recent projects ---")
for r in c.execute(
    "SELECT id, name, status, notes, created_at FROM projects ORDER BY created_at DESC LIMIT 8"
):
    age = (now - r[4]) / 1000
    print(f"  {r[0]} status={r[2]} age={age:.0f}s name={r[1]!r} notes={r[3]!r}")

print("--- unfinished jobs ---")
for r in c.execute(
    """SELECT id, type, project_id, status, progress, progress_message, attempts,
              started_at, created_at, error_message
       FROM jobs
       WHERE status IN ('pending','running')
       ORDER BY created_at"""
):
    started = (now - r[7]) / 1000 if r[7] else None
    print(
        f"  {r[0]} type={r[1]} project={r[2]} status={r[3]} attempts={r[6]} "
        f"progress={r[4]:.2f} msg={r[5]!r} started_ago={started}s err={r[9]!r}"
    )

print("--- last 8 jobs ---")
for r in c.execute(
    """SELECT id, type, project_id, status, progress_message, attempts
       FROM jobs ORDER BY created_at DESC LIMIT 8"""
):
    print(f"  {r}")

c.close()
