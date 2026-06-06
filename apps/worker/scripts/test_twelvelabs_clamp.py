"""Unit checks for TwelveLabs analyze window clamping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.providers.twelvelabs_client import TwelveLabsClient  # noqa: E402


def test_parse_asset_duration() -> None:
    payload = {"system_metadata": {"duration": 3564.133}}
    assert TwelveLabsClient._parse_asset_duration_seconds(payload) == 3564.133
    print("OK parse asset duration")


def test_clamp_end_time() -> None:
    end = TwelveLabsClient._clamp_analyze_end(0.0, 3600.0, 3564.133)
    assert end <= 3564.133 - 0.5 + 0.01
    assert end > 3560.0
    print(f"OK clamp end_time 3600 -> {end:.3f}")


def main() -> None:
    test_parse_asset_duration()
    test_clamp_end_time()
    print("OK all clamp tests passed")


if __name__ == "__main__":
    main()
