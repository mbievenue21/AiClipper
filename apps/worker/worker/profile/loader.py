"""Load active highlight profile version from the database."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from ..db import session_scope
from ..models import HighlightProfile, HighlightProfileVersion
from .config import ProfileConfig, default_valorant_config, load_config_dict

log = structlog.get_logger(__name__)


@dataclass
class ActiveProfileVersion:
    profile_id: str
    profile_slug: str
    version_id: str
    version_number: int
    config: ProfileConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_slug": self.profile_slug,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "config": self.config.to_dict(),
        }


def load_active_profile_version(
    profile_id_or_slug: str | None = None,
) -> ActiveProfileVersion:
    """Resolve the active profile version by id, slug, or default Valorant profile."""
    with session_scope() as session:
        profile: HighlightProfile | None = None
        if profile_id_or_slug:
            profile = session.get(HighlightProfile, profile_id_or_slug)
            if profile is None:
                profile = session.execute(
                    select(HighlightProfile).where(
                        HighlightProfile.slug == profile_id_or_slug
                    )
                ).scalar_one_or_none()

        if profile is None:
            profile = session.execute(
                select(HighlightProfile)
                .where(HighlightProfile.status == "active")
                .order_by(HighlightProfile.updated_at.desc())
            ).scalar_one_or_none()

        if profile is None:
            log.warning("no_active_profile_using_default")
            cfg = default_valorant_config()
            return ActiveProfileVersion(
                profile_id="valorant_reaction",
                profile_slug=cfg.metadata.get("slug", "valorant_reaction_shorts"),
                version_id="valorant_reaction_v1",
                version_number=1,
                config=cfg,
            )

        version: HighlightProfileVersion | None = None
        if profile.active_version_id:
            version = session.get(HighlightProfileVersion, profile.active_version_id)

        if version is None:
            version = session.execute(
                select(HighlightProfileVersion)
                .where(
                    HighlightProfileVersion.profile_id == profile.id,
                    HighlightProfileVersion.is_active.is_(True),
                )
                .order_by(HighlightProfileVersion.version_number.desc())
            ).scalar_one_or_none()

        if version is None:
            version = session.execute(
                select(HighlightProfileVersion)
                .where(HighlightProfileVersion.profile_id == profile.id)
                .order_by(HighlightProfileVersion.version_number.desc())
            ).scalar_one_or_none()

        if version is None:
            log.warning("profile_has_no_version", profile_id=profile.id)
            cfg = default_valorant_config()
            return ActiveProfileVersion(
                profile_id=profile.id,
                profile_slug=profile.slug,
                version_id="default",
                version_number=0,
                config=cfg,
            )

        cfg = load_config_dict(version.config)
        return ActiveProfileVersion(
            profile_id=profile.id,
            profile_slug=profile.slug,
            version_id=version.id,
            version_number=version.version_number,
            config=cfg,
        )
