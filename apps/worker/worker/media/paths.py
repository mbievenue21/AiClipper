"""Paths under MEDIA_ROOT for a given project."""

from __future__ import annotations

from pathlib import Path

from ..config import get_settings


def project_dir(project_id: str) -> Path:
    root = get_settings().media_root_path
    path = root / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def rel_path(absolute: Path) -> str:
    """Store paths relative to MEDIA_ROOT in the database."""
    root = get_settings().media_root_path.resolve()
    return absolute.resolve().relative_to(root).as_posix()
