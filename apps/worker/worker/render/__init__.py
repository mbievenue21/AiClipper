"""Clip rendering pipeline (Step 8).

Given an approved highlight (start/end seconds + source video), produce a
single ``clip.mp4`` under ``data/videos/<project>/clips/<clip_id>/``:

1. Snap start/end to the nearest scene cut (within +/- 1.5s) for a clean
   in/out using PySceneDetect.
2. Cut with FFmpeg, copying audio. Apply audio loudnorm so chained clips
   sound consistent.
3. Reformat to target aspect (9:16 / 16:9 / 1:1). For portrait output from
   landscape source, fill the bars with a blurred, scaled copy of the same
   frame (the "industry-standard" look) and center the original on top.
4. Extract dominant color from the middle frame for caption gradients.

Captioning is a SEPARATE second pass — see worker/render/captions.py.
"""

from __future__ import annotations

from .dominant_color import extract_dominant_color
from .ffmpeg import RenderSpec, render_clip
from .scene_snap import snap_to_scenes

__all__ = [
    "RenderSpec",
    "extract_dominant_color",
    "render_clip",
    "snap_to_scenes",
]
