"""Smoke test for TwelveLabs Multimodal Analysis wiring (no real API calls)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.analyze.candidate_fusion import fuse_highlight_candidates  # noqa: E402
from worker.analyze.candidates import Candidate  # noqa: E402
from worker.analyze.twelvelabs_convert import (  # noqa: E402
    deduplicate_visual_segments,
    offset_segment_timestamps,
    parse_pegasus_segments,
    parse_marengo_search_hits,
)
from worker.config import get_settings  # noqa: E402
from worker.jobs import twelvelabs_analyze as _ta  # noqa: E402,F401
from worker.jobs import twelvelabs_index as _ti  # noqa: E402,F401
from worker.jobs.handlers import get_handler, registered_types  # noqa: E402
from worker.providers.twelvelabs_client import TwelveLabsClient  # noqa: E402
from worker.providers.twelvelabs_types import VisualSegmentResult  # noqa: E402
from worker.providers.twelvelabs_upload_plan import plan_upload_chunks  # noqa: E402


def test_handler_registration() -> None:
    types = registered_types()
    assert get_handler("twelvelabs_index") is not None, "twelvelabs_index missing"
    assert get_handler("twelvelabs_analyze") is not None, "twelvelabs_analyze missing"
    print(f"OK handlers registered: {', '.join(sorted(types))}")


def test_env_parsing() -> None:
    settings = get_settings()
    print(f"OK TWELVELABS_ENABLED={settings.twelvelabs_enabled}")
    print(f"OK TWELVELABS_FAIL_OPEN={settings.twelvelabs_fail_open}")
    client = TwelveLabsClient(settings)
    print(f"OK client.enabled()={client.enabled()} configured()={client.configured()}")


def test_chunk_offset() -> None:
    seg = VisualSegmentResult(
        provider="twelvelabs",
        model="pegasus1.5",
        source_method="pegasus_segmentation",
        start_seconds=312,
        end_seconds=355,
        segment_type="gameplay_fail",
        confidence=0.87,
    )
    shifted = offset_segment_timestamps([seg], 7200.0)
    assert shifted[0].start_seconds == 7512.0
    assert shifted[0].end_seconds == 7555.0
    print("OK chunk timestamp offset 7200+312=7512")


def test_deduplication() -> None:
    a = VisualSegmentResult(
        provider="twelvelabs",
        model="m",
        source_method="pegasus_segmentation",
        start_seconds=100,
        end_seconds=130,
        segment_type="streamer_reaction",
        confidence=0.9,
    )
    b = VisualSegmentResult(
        provider="twelvelabs",
        model="m",
        source_method="pegasus_segmentation",
        start_seconds=105,
        end_seconds=125,
        segment_type="streamer_reaction",
        confidence=0.7,
    )
    out = deduplicate_visual_segments([a, b])
    assert len(out) == 1
    assert out[0].confidence == 0.9
    print("OK overlap deduplication keeps higher confidence")


def test_pegasus_parse() -> None:
    payload = {
        "segments": [
            {
                "start_seconds": 10,
                "end_seconds": 25,
                "segment_type": "gameplay_win",
                "confidence": 0.8,
                "title": "Clutch",
                "visual_reason": "Visible win reaction",
            }
        ]
    }
    segs = parse_pegasus_segments(payload, model="pegasus1.5", min_confidence=0.55)
    assert len(segs) == 1
    assert segs[0].segment_type == "gameplay_win"
    print("OK Pegasus segment conversion")


def test_marengo_parse() -> None:
    payload = {
        "data": [
            {"start": 100.0, "end": 111.0, "score": 0.82, "text": "big reaction"},
        ]
    }
    hits = parse_marengo_search_hits(
        payload,
        query="streamer surprised reaction",
        model="marengo3.0",
        duration_seconds=3600,
        min_confidence=0.55,
    )
    assert len(hits) == 1
    assert hits[0].start_seconds < 100.0
    print("OK Marengo search hit expansion")


def test_upload_plan_oversized() -> None:
    # Reproduces ~2.1 GB file over TwelveLabs 2 GB cap — needs multiple chunks.
    file_size = int(2.1 * 1024**3)
    duration = 7200.0
    max_bytes = 1_900_000_000
    plans = plan_upload_chunks(
        file_size,
        duration,
        max_upload_bytes=max_bytes,
        max_chunk_seconds=7200,
        overlap_seconds=15,
    )
    assert len(plans) >= 2, f"expected >=2 upload chunks, got {len(plans)}"
    total_span = sum(p.duration_seconds for p in plans)
    assert total_span >= duration - 30, "plans should cover full VOD"
    assert plans[0].start_seconds == 0.0
    print(f"OK upload plan: {len(plans)} chunks for 2.1GB / 2h VOD")


def test_fusion() -> None:
    local = [
        Candidate(
            start_seconds=4900,
            end_seconds=4960,
            text="and then I died wow",
            audio_score=0.4,
            chat_score=0.9,
            keyword_score=0.3,
            composite_score=0.55,
            seed_source="transcript",
            chat_peak_at=4938,
        )
    ]
    visual = [
        VisualSegmentResult(
            provider="twelvelabs",
            model="pegasus1.5",
            source_method="pegasus_segmentation",
            start_seconds=4920,
            end_seconds=4935,
            segment_type="gameplay_fail",
            confidence=0.88,
            visual_reason="Visible death on screen",
            suggested_clip_start_seconds=4912,
            suggested_clip_end_seconds=4945,
        )
    ]
    fused = fuse_highlight_candidates(local, visual)
    assert fused
    top = fused[0]
    assert top.visual_score >= 0.88
    assert top.fusion_score > 0.5
    print(f"OK fusion score={top.fusion_score:.3f} visual={top.visual_score:.3f}")


def main() -> None:
    print("TwelveLabs Multimodal Analysis smoke test")
    test_handler_registration()
    test_env_parsing()
    test_chunk_offset()
    test_deduplication()
    test_pegasus_parse()
    test_marengo_parse()
    test_upload_plan_oversized()
    test_fusion()
    print("OK all TwelveLabs smoke checks passed")


if __name__ == "__main__":
    main()
