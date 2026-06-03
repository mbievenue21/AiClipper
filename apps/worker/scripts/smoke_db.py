"""Verifies that the Python worker can read what Drizzle wrote, and vice versa.

Run with: .venv\\Scripts\\python scripts\\smoke_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `worker` importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from worker.db import session_scope  # noqa: E402
from worker.models import Job, Project  # noqa: E402


def main() -> None:
    with session_scope() as session:
        projects = session.execute(
            select(Project).order_by(Project.created_at.desc())
        ).scalars().all()
        print(f"Projects visible to Python ({len(projects)}):")
        for p in projects:
            print(f"  - {p.id}  [{p.status:12}]  {p.name}")

        # Round-trip: insert a Python-created project, then a Python-created job.
        py_project = Project(
            name="Created from Python",
            source_url="https://example.com/source",
            source_type="upload",
            status="pending",
        )
        session.add(py_project)
        session.flush()

        py_job = Job(
            type="ingest",
            project_id=py_project.id,
            payload_json='{"url": "https://example.com/source"}',
            status="pending",
        )
        session.add(py_job)
        session.flush()

        print(f"\nInserted project from Python: id={py_project.id}")
        print(f"Inserted job from Python:     id={py_job.id} type={py_job.type}")

        # Roll back so we don't pollute the DB.
        session.rollback()
        print("\nRolled back - cross-language read/write works.")


if __name__ == "__main__":
    main()
