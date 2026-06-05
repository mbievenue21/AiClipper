import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[3] / "data" / "app.db"
c = sqlite3.connect(db)
try:
    rows = c.execute("SELECT id, hash, created_at FROM __drizzle_migrations ORDER BY created_at").fetchall()
    print("Applied migrations:", rows)
except Exception as e:
    print("Migrations table error:", e)
cols = [r[1] for r in c.execute("PRAGMA table_info(videos)").fetchall()]
print("videos columns:", cols)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print("tables:", tables)
c.close()
