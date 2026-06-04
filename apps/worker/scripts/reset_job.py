"""Reset a job to pending. Usage: python scripts/reset_job.py JOB_ID"""
import sqlite3
import sys
from pathlib import Path

db = Path(__file__).resolve().parents[3] / "data" / "app.db"
job_id = sys.argv[1] if len(sys.argv) > 1 else "ZWX5FKHrh6fY"
conn = sqlite3.connect(db)
rows = conn.execute(
    "SELECT id, type, status, progress_message, attempts FROM jobs WHERE id = ?",
    (job_id,),
).fetchall()
print("before:", rows)
conn.execute(
    """UPDATE jobs SET status='pending', progress=0,
       progress_message='retry after cuda fix', error_message=NULL,
       started_at=NULL, finished_at=NULL WHERE id=?""",
    (job_id,),
)
conn.commit()
print("reset ok")
conn.close()
