"""Hard reset: kill all worker processes, free port 8000, heal the DB.

Run this when the worker is unresponsive, port 8000 is held by a zombie
process, or projects are stuck in pending/transcribing/analyzing with no
active jobs. Safe to run repeatedly. Does NOT touch downloaded media.

Usage::

    pnpm worker:reset
    # or directly:
    .venv\\Scripts\\python scripts\\reset_workers.py
    .venv\\Scripts\\python scripts\\reset_workers.py --dry-run

What it does, in order:
1. Find every ``python ... uvicorn worker.main:app`` process and kill it.
2. Find anything still listening on port 8000 and kill it.
3. Reset jobs stuck in ``running`` → ``pending`` so they retry on next boot.
4. Mark projects stuck mid-pipeline (no active jobs) as ``failed`` with a
   clear note so the UI shows what happened.

After running this, start ``pnpm dev`` again.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "app.db"

IS_WINDOWS = platform.system() == "Windows"


def _resolve_powershell() -> str | None:
    """Find PowerShell. Falls back to the known System32 path if it's not on PATH."""
    candidate = shutil.which("powershell") or shutil.which("pwsh")
    if candidate:
        return candidate
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    fallback = os.path.join(
        sysroot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    return fallback if os.path.exists(fallback) else None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _find_uvicorn_pids_windows() -> list[tuple[int, str]]:
    """Return [(pid, cmdline)] for every python process running our worker."""
    ps = _resolve_powershell()
    if not ps:
        print("  WARN: PowerShell not found, can't enumerate process command lines.")
        return []
    out = _run(
        [
            ps,
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*uvicorn*worker.main*' } | "
            "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }",
        ]
    )
    pids: list[tuple[int, str]] = []
    for line in out.stdout.splitlines():
        if "|" not in line:
            continue
        pid_s, _, cmd = line.partition("|")
        try:
            pids.append((int(pid_s.strip()), cmd.strip()))
        except ValueError:
            continue
    return pids


def _find_uvicorn_pids_posix() -> list[tuple[int, str]]:
    out = _run(["ps", "-Ao", "pid,args"])
    pids: list[tuple[int, str]] = []
    for line in out.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition(" ")
        if "uvicorn" in cmd and "worker.main" in cmd:
            try:
                pids.append((int(pid_s.strip()), cmd.strip()))
            except ValueError:
                continue
    return pids


def _find_port_owners_windows(port: int) -> list[int]:
    # Use netstat (always available in System32) — no PowerShell required.
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    netstat = os.path.join(sysroot, "System32", "netstat.exe")
    if not os.path.exists(netstat):
        netstat = "netstat"
    out = _run([netstat, "-ano", "-p", "TCP"])
    pids: set[int] = set()
    needle = f":{port}"
    for line in out.stdout.splitlines():
        # Format: "  TCP    127.0.0.1:8000   0.0.0.0:0   LISTENING   12345"
        parts = line.split()
        if len(parts) < 5:
            continue
        if "LISTENING" not in parts:
            continue
        local = parts[1]
        if not local.endswith(needle):
            continue
        try:
            pids.add(int(parts[-1]))
        except ValueError:
            continue
    return list(pids)


def _find_port_owners_posix(port: int) -> list[int]:
    out = _run(["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"])
    return [int(p) for p in out.stdout.split() if p.strip().isdigit()]


def kill_pid(pid: int, *, dry_run: bool) -> bool:
    if dry_run:
        print(f"  would kill PID {pid}")
        return True
    try:
        if IS_WINDOWS:
            sysroot = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = os.path.join(sysroot, "System32", "taskkill.exe")
            if not os.path.exists(taskkill):
                taskkill = "taskkill"
            _run([taskkill, "/F", "/PID", str(pid), "/T"])
        else:
            os.kill(pid, signal.SIGKILL)
        print(f"  killed PID {pid}")
        return True
    except (ProcessLookupError, PermissionError, OSError) as exc:
        print(f"  could not kill PID {pid}: {exc}")
        return False


def kill_workers(*, dry_run: bool) -> int:
    print("[1/4] killing uvicorn worker processes")
    found = (
        _find_uvicorn_pids_windows() if IS_WINDOWS else _find_uvicorn_pids_posix()
    )
    if not found:
        print("  none found")
        return 0
    killed = 0
    for pid, cmd in found:
        snippet = cmd[:100] + ("..." if len(cmd) > 100 else "")
        print(f"  -> PID {pid}: {snippet}")
        if kill_pid(pid, dry_run=dry_run):
            killed += 1
    return killed


def free_port(port: int, *, dry_run: bool) -> int:
    print(f"[2/4] freeing port {port}")
    pids = (
        _find_port_owners_windows(port) if IS_WINDOWS else _find_port_owners_posix(port)
    )
    if not pids:
        print("  port is free")
        return 0
    killed = 0
    for pid in pids:
        print(f"  port held by PID {pid}")
        if kill_pid(pid, dry_run=dry_run):
            killed += 1
    return killed


def reset_running_jobs(*, dry_run: bool) -> int:
    print("[3/4] resetting stuck 'running' jobs -> 'pending'")
    if not DB_PATH.exists():
        print(f"  DB not found at {DB_PATH}, skipping")
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, type, project_id, attempts FROM jobs WHERE status = 'running'"
        ).fetchall()
        if not rows:
            print("  no running jobs")
            return 0
        for r in rows:
            print(f"  -> job {r[0]} type={r[1]} project={r[2]} attempts={r[3]}")
        if dry_run:
            return len(rows)
        conn.execute(
            """UPDATE jobs SET status='pending', progress=0,
               progress_message='reset by worker:reset',
               started_at=NULL WHERE status='running'"""
        )
        conn.commit()
        print(f"  reset {len(rows)} job(s)")
        return len(rows)
    finally:
        conn.close()


def heal_orphan_projects(*, dry_run: bool) -> int:
    print("[4/4] healing orphan projects (pending/ingesting/transcribing/analyzing with no active job)")
    if not DB_PATH.exists():
        print(f"  DB not found at {DB_PATH}, skipping")
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.status
            FROM projects p
            LEFT JOIN jobs j ON j.project_id = p.id
            WHERE p.status IN ('pending', 'ingesting', 'transcribing', 'analyzing')
            GROUP BY p.id
            HAVING SUM(CASE WHEN j.status IN ('pending','running') THEN 1 ELSE 0 END) = 0
               AND SUM(CASE WHEN j.status = 'succeeded' THEN 1 ELSE 0 END) = 0
            """
        ).fetchall()
        if not rows:
            print("  no orphans")
            return 0
        for r in rows:
            print(f"  -> project {r[0]} status={r[2]} name={r[1]!r}")
        if dry_run:
            return len(rows)
        conn.executemany(
            """UPDATE projects SET status='failed',
               notes='Worker was unavailable when this stage ran. Delete this project and create a new one.',
               updated_at=? WHERE id=?""",
            [(_now_ms(), r[0]) for r in rows],
        )
        conn.commit()
        print(f"  healed {len(rows)} project(s)")
        return len(rows)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen without changing anything.",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Worker port to free (default 8000)."
    )
    args = parser.parse_args()

    print("=" * 60)
    print("AiClipper worker reset" + (" (dry run)" if args.dry_run else ""))
    print(f"DB:   {DB_PATH}")
    print(f"Port: {args.port}")
    print("=" * 60)

    workers = kill_workers(dry_run=args.dry_run)
    if workers and not args.dry_run:
        # Give the OS a moment to release the port.
        time.sleep(1.5)
    port_owners = free_port(args.port, dry_run=args.dry_run)
    jobs_reset = reset_running_jobs(dry_run=args.dry_run)
    projects_healed = heal_orphan_projects(dry_run=args.dry_run)

    print("=" * 60)
    print(
        f"Summary: workers={workers} port_owners={port_owners} "
        f"jobs_reset={jobs_reset} projects_healed={projects_healed}"
    )
    if not args.dry_run:
        print("Next: run `pnpm dev` to start a clean worker.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
