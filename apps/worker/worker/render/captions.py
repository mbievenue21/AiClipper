"""Generate styled, animated ASS subtitles burned in via ffmpeg.

Why ASS instead of pure Remotion: ffmpeg already ships with libass on every
platform that has ffmpeg. The ASS format supports per-word timing, color
gradients, outlines, fades, scale animation, and karaoke highlighting — all
the things the user asked for ("highlight current word", "popup", "karaoke",
"minimal"). It renders in seconds (Remotion via Chromium would take minutes
per clip) and the output is identical regardless of OS.

The Remotion package at packages/remotion is kept around for future use
(richer animations) but the default caption pipeline goes through here.

Output positioning: captions sit at vertical center 80% — well above the
TikTok/Reels UI overlay on the bottom 20% of the screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dominant_color import contrasting_pair, hex_to_rgb


# ---------------------------------------------------------------------------
# Fonts (must exist on the host or be bundled in apps/worker/assets/fonts)
# ---------------------------------------------------------------------------
FONT_FAMILY: dict[str, str] = {
    "inter": "Inter",
    "bebas": "Bebas Neue",
    "anton": "Anton",
    "marker": "Permanent Marker",
    "mono": "Roboto Mono",
    "montserrat": "Montserrat",
}

# Approximate em-width per character for each font, measured in font-size
# units (1.0 = font size px). Bold display fonts are wider than book fonts.
# Used by _auto_font_size to compute a font size that's guaranteed to fit
# the safe area width given `max_chars_per_line`.
_FONT_EM_WIDTH: dict[str, float] = {
    "inter": 0.55,
    "montserrat": 0.58,
    "mono": 0.62,
    "anton": 0.43,  # narrow display
    "bebas": 0.42,
    "marker": 0.60,
}

# ASS reserves these characters for tags/control. Anything in the user's
# transcript that isn't escaped will get parsed as a libass tag (silent
# omission, malformed render, or garbled output). Also normalize the
# fancy unicode that some Whisper outputs sneak in.
_ASS_UNICODE_NORMALIZE = {
    "\u2018": "'",  # ' left single quote
    "\u2019": "'",  # ' right single quote
    "\u201C": '"',  # " left double quote
    "\u201D": '"',  # " right double quote
    "\u2013": "-",  # – en dash
    "\u2014": "-",  # — em dash
    "\u2026": "...",  # … ellipsis
    "\u00A0": " ",  # nbsp
}


def _sanitize_caption_text(raw: str) -> str:
    """Make a token safe to inject into an ASS Dialogue body.

    ASS treats `{...}` blocks as override tags and `\\` as the escape prefix,
    so any literal occurrence of those in transcript text turns into
    garbled / missing output (this is the "some characters are omitted"
    symptom). Replace them with safe equivalents. Also kill control bytes
    and normalize the smart-quote/dash family.
    """
    if not raw:
        return ""
    out_chars: list[str] = []
    for ch in raw:
        if ch in _ASS_UNICODE_NORMALIZE:
            out_chars.append(_ASS_UNICODE_NORMALIZE[ch])
        elif ch == "{":
            out_chars.append("(")
        elif ch == "}":
            out_chars.append(")")
        elif ch == "\\":
            out_chars.append("/")
        elif ch == "\r" or ch == "\n":
            out_chars.append(" ")
        elif ord(ch) < 0x20:
            # Strip other control bytes silently.
            continue
        else:
            out_chars.append(ch)
    return "".join(out_chars).strip()


@dataclass(frozen=True)
class CaptionWord:
    text: str
    start: float  # seconds relative to start of CLIP (not source video)
    end: float


@dataclass(frozen=True)
class CaptionSegment:
    """A single line of captions — typically a transcript segment, possibly
    re-chunked to keep each line under ``max_chars`` characters."""

    start: float
    end: float
    words: list[CaptionWord]


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """ASS uses BGR order with a leading alpha byte: &HAABBGGRR."""
    r, g, b = hex_to_rgb(hex_color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _format_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def chunk_segments(
    raw_segments: list[dict[str, Any]],
    *,
    clip_start: float,
    clip_end: float,
    max_chars_per_line: int = 22,
) -> list[CaptionSegment]:
    """Build CaptionSegments from raw transcript segments scoped to the clip.

    Each ``raw_segments`` entry needs ``start_seconds``, ``end_seconds``,
    ``text``, and optionally ``words`` (list of {word/text, start, end}).

    Times are converted to clip-relative seconds (0 = start of clip).
    Lines longer than ``max_chars_per_line`` are split on word boundaries
    so captions never overflow the safe area on vertical video.

    Tuned for portrait 9:16 with bold display fonts (Anton/Bebas). For wider
    aspects the caller can pass a larger ``max_chars_per_line``.
    """
    out: list[CaptionSegment] = []

    for raw in raw_segments:
        seg_start = float(raw.get("start_seconds", 0.0))
        seg_end = float(raw.get("end_seconds", seg_start))
        if seg_end <= clip_start or seg_start >= clip_end:
            continue
        seg_start = max(seg_start, clip_start) - clip_start
        seg_end = min(seg_end, clip_end) - clip_start

        words_data = raw.get("words") or []
        words: list[CaptionWord] = []
        for w in words_data:
            text = _sanitize_caption_text(
                (w.get("word") or w.get("text") or "")
            )
            if not text:
                continue
            ws = float(w.get("start", seg_start)) - clip_start
            we = float(w.get("end", we_default := ws + 0.3)) - clip_start
            _ = we_default
            if we <= 0 or ws >= clip_end - clip_start:
                continue
            words.append(
                CaptionWord(text=text, start=max(0.0, ws), end=max(ws + 0.05, we))
            )

        # Fall back to even-time-split per word if word-level timing missing.
        if not words:
            raw_text = _sanitize_caption_text(raw.get("text") or "")
            tokens = raw_text.split()
            if not tokens:
                continue
            total = max(0.01, seg_end - seg_start)
            per = total / len(tokens)
            for i, tok in enumerate(tokens):
                ws = seg_start + i * per
                words.append(CaptionWord(text=tok, start=ws, end=ws + per))

        # Re-chunk into lines no wider than max_chars_per_line. We allow a
        # single caption to span up to 2 visual lines (separated by \N in
        # ASS) so longer sentences still get displayed in one continuous
        # subtitle block rather than rapid-fire single-line flicker.
        line: list[CaptionWord] = []
        line_len = 0
        for w in words:
            cost = len(w.text) + (1 if line else 0)
            if line and line_len + cost > max_chars_per_line:
                out.append(
                    CaptionSegment(start=line[0].start, end=line[-1].end, words=line)
                )
                line, line_len = [], 0
            line.append(w)
            line_len += cost
        if line:
            out.append(
                CaptionSegment(start=line[0].start, end=line[-1].end, words=line)
            )

    return out


def segments_from_overrides(
    overrides: list[dict[str, Any]],
) -> list[CaptionSegment]:
    """Build caption segments from user-edited segment-level overrides.

    Times are clip-relative seconds. Word timings are evenly distributed
    within each segment for karaoke/highlight styles.
    """
    out: list[CaptionSegment] = []
    for raw in overrides:
        text = _sanitize_caption_text(str(raw.get("text") or ""))
        if not text:
            continue
        start = max(0.0, float(raw.get("start", 0.0)))
        end = max(start + 0.05, float(raw.get("end", start + 1.0)))
        tokens = text.split()
        if not tokens:
            continue
        total = max(0.01, end - start)
        per = total / len(tokens)
        words = [
            CaptionWord(text=tok, start=start + i * per, end=start + (i + 1) * per)
            for i, tok in enumerate(tokens)
        ]
        out.append(CaptionSegment(start=start, end=end, words=words))
    return out


def _auto_font_size(
    *,
    width_px: int,
    height_px: int,
    margin_lr: int,
    max_chars_per_line: int,
    font_key: str,
    uppercase: bool,
) -> int:
    """Compute a font size that guarantees the line fits in the safe area.

    Why this exists: the previous formula `max(48, height/20)` produced sizes
    that overflowed horizontally on portrait video (1080×1920 with 28 chars
    of bold Anton was ~1600px wide, but the safe area is only ~970px). Words
    got clipped off the screen and libass silently truncated them — exactly
    the "characters omitted" symptom.

    Approach: solve for size such that
        max_chars * em_width * size <= safe_width
    then bound between 48 (legibility floor) and a reasonable visual ceiling
    based on height. Uppercase costs ~8% more width.
    """
    safe_width = max(1, width_px - 2 * margin_lr)
    em = _FONT_EM_WIDTH.get(font_key, 0.55)
    if uppercase:
        em *= 1.08
    # 0.94 leaves a small breathing margin so the last word doesn't kiss
    # the edge under variable letterforms.
    by_width = (safe_width * 0.94) / max(1, max_chars_per_line * em)
    # Visual ceiling: don't make captions look like ransom-note posters.
    by_height = height_px / 11.0  # ~175px on 1920 tall
    # Floor: 48px keeps captions legible even on tiny clips.
    return max(48, int(min(by_width, by_height)))


def _resolve_colors(
    style: dict[str, Any],
    dominant_color: str | None,
) -> tuple[str, str]:
    """Return (primary_hex, accent_hex) for the chosen settings."""
    if style.get("autoColor", True):
        if dominant_color:
            return contrasting_pair(dominant_color)
        return ("#FFD700", "#FFFFFF")
    return (
        str(style.get("primaryColor", "#FFD700")),
        str(style.get("accentColor", "#FFFFFF")),
    )


def _font_family(style: dict[str, Any]) -> str:
    return FONT_FAMILY.get(str(style.get("font", "anton")), "Anton")


def _apply_uppercase(text: str, style: dict[str, Any]) -> str:
    return text.upper() if style.get("uppercase", True) else text


def build_ass(
    segments: list[CaptionSegment],
    *,
    style: dict[str, Any],
    dominant_color: str | None,
    width_px: int,
    height_px: int,
) -> str:
    """Serialize CaptionSegments to a full .ass file string.

    The four styles map to:
      - highlight : line shown in accent color; current word in primary
      - popup     : each word fades in with a tiny scale-up
      - karaoke   : built-in libass karaoke sweep (\\k tags)
      - minimal   : static line, all primary color, no per-word emphasis
    """
    primary, accent = _resolve_colors(style, dominant_color)
    font = _font_family(style)
    font_key = str(style.get("font", "anton"))
    uppercase = bool(style.get("uppercase", True))
    margin_v = int(height_px * 0.20)  # captions hang ~20% above bottom
    margin_lr = int(width_px * 0.05)

    # Default max chars: 22 for narrow portrait (most common), 32 for square,
    # 40 for landscape. Caller can override via style["maxCharsPerLine"].
    if width_px >= height_px:
        default_max_chars = 38
    elif width_px == height_px:
        default_max_chars = 32
    else:
        default_max_chars = 22
    max_chars_per_line = int(
        style.get("maxCharsPerLine", default_max_chars) or default_max_chars
    )

    base_size = _auto_font_size(
        width_px=width_px,
        height_px=height_px,
        margin_lr=margin_lr,
        max_chars_per_line=max_chars_per_line,
        font_key=font_key,
        uppercase=uppercase,
    )

    primary_ass = _ass_color(primary)
    accent_ass = _ass_color(accent)
    outline_ass = _ass_color("#000000")
    back_ass = _ass_color("#000000", alpha=160)  # semi-transparent shadow

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width_px}
PlayResY: {height_px}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption, {font}, {base_size}, {accent_ass}, {primary_ass}, {outline_ass}, {back_ass}, -1, 0, 0, 0, 100, 100, 0, 0, 1, 4, 1, 2, {margin_lr}, {margin_lr}, {margin_v}, 1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    style_name = str(style.get("style", "highlight"))

    events: list[str] = []
    for seg in segments:
        events.extend(
            _events_for_segment(
                seg,
                style=style_name,
                primary_ass=primary_ass,
                accent_ass=accent_ass,
                uppercase=bool(style.get("uppercase", True)),
            )
        )

    return header + "\n".join(events) + "\n"


def _events_for_segment(
    seg: CaptionSegment,
    *,
    style: str,
    primary_ass: str,
    accent_ass: str,
    uppercase: bool,
) -> list[str]:
    """Render one CaptionSegment as one-or-more Dialogue: lines."""
    if not seg.words:
        return []

    if style == "minimal":
        text = " ".join(_apply_uppercase(w.text, {"uppercase": uppercase}) for w in seg.words)
        return [_dialogue(seg.start, seg.end, text, leading_tags=f"{{\\c{primary_ass}}}")]

    if style == "popup":
        # Emit one Dialogue per word with a small fade-in + scale animation.
        events: list[str] = []
        line_text_parts = [_apply_uppercase(w.text, {"uppercase": uppercase}) for w in seg.words]
        for i, w in enumerate(seg.words):
            before = " ".join(line_text_parts[:i])
            current = line_text_parts[i]
            tags = (
                f"{{\\an2\\fad(80,0)\\fscx70\\fscy70"
                f"\\t(0,180,\\fscx105\\fscy105)"
                f"\\t(180,260,\\fscx100\\fscy100)"
                f"\\c{primary_ass}\\3c&H000000&\\bord3}}"
            )
            # Show the already-spoken words in accent, current word in primary.
            line = (
                (f"{{\\c{accent_ass}}}{before} " if before else "")
                + tags
                + current
            )
            events.append(_dialogue(w.start, max(w.end, w.start + 0.18), line))
        return events

    if style == "karaoke":
        # libass karaoke: \k<centiseconds> highlights one syllable at a time.
        pieces: list[str] = []
        for w in seg.words:
            dur_cs = max(1, int(round((w.end - w.start) * 100)))
            pieces.append(
                f"{{\\k{dur_cs}}}"
                + _apply_uppercase(w.text, {"uppercase": uppercase})
                + " "
            )
        text = (
            f"{{\\c{accent_ass}\\2c{primary_ass}\\3c&H000000&\\bord3}}"
            + "".join(pieces).strip()
        )
        return [_dialogue(seg.start, seg.end, text)]

    # Default: highlight — full line in accent, current word swelled in primary.
    events = []
    line_words = [_apply_uppercase(w.text, {"uppercase": uppercase}) for w in seg.words]
    for i, w in enumerate(seg.words):
        before = " ".join(line_words[:i])
        current = line_words[i]
        after = " ".join(line_words[i + 1 :])
        # Word i is in primary + slightly bigger; siblings in accent.
        parts: list[str] = []
        if before:
            parts.append(f"{{\\c{accent_ass}\\bord3\\3c&H000000&}}{before}")
        parts.append(
            f"{{\\c{primary_ass}\\fscx115\\fscy115\\bord4\\3c&H000000&}}"
            + ((" " if before else "") + current)
        )
        if after:
            parts.append(f"{{\\c{accent_ass}\\bord3\\3c&H000000&}} {after}")
        events.append(_dialogue(w.start, max(w.end, w.start + 0.12), "".join(parts)))
    return events


def _dialogue(
    start: float,
    end: float,
    text: str,
    *,
    leading_tags: str = "",
) -> str:
    return (
        f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
        f"Caption,,0,0,0,,{leading_tags}{text}"
    )


def write_ass_file(
    out_path: Path,
    segments: list[CaptionSegment],
    *,
    style: dict[str, Any],
    dominant_color: str | None,
    width_px: int,
    height_px: int,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = build_ass(
        segments,
        style=style,
        dominant_color=dominant_color,
        width_px=width_px,
        height_px=height_px,
    )
    out_path.write_text(body, encoding="utf-8")
