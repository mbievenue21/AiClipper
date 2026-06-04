/**
 * CaptionedClip — overlays animated word-level captions onto a pre-rendered
 * vertical/square/horizontal clip.
 *
 * This is the future-expansion path. The shipped worker pipeline uses
 * ffmpeg + libass via worker/render/captions.py for ~100x faster renders.
 * Keep this composition in sync with the four ASS styles (highlight, popup,
 * karaoke, minimal) so swapping back is a one-line change in the worker.
 */
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type CaptionFont =
  | "inter"
  | "bebas"
  | "anton"
  | "marker"
  | "mono"
  | "montserrat";

export type CaptionStyleName =
  | "highlight"
  | "popup"
  | "karaoke"
  | "minimal";

export type CaptionedClipProps = {
  clipPath: string;
  durationSeconds: number;
  width: number;
  height: number;
  segments: Array<{
    start: number;
    end: number;
    words: Array<{ text: string; start: number; end: number }>;
  }>;
  style: {
    font: CaptionFont;
    style: CaptionStyleName;
    primaryColor: string;
    accentColor: string;
    uppercase: boolean;
  };
};

const FONT_STACK: Record<CaptionFont, string> = {
  inter: "Inter, system-ui, sans-serif",
  bebas: '"Bebas Neue", Impact, sans-serif',
  anton: "Anton, Impact, sans-serif",
  marker: '"Permanent Marker", Impact, cursive',
  mono: '"Roboto Mono", ui-monospace, monospace',
  montserrat: "Montserrat, system-ui, sans-serif",
};

export const CaptionedClip: React.FC<CaptionedClipProps> = (props) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const tSeconds = frame / fps;

  const fontFamily = FONT_STACK[props.style.font];
  const cap = (s: string) => (props.style.uppercase ? s.toUpperCase() : s);

  // Find the segment that covers the current second.
  const active = props.segments.find(
    (s) => tSeconds >= s.start && tSeconds <= s.end + 0.15,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {props.clipPath && (
        <OffthreadVideo
          src={props.clipPath}
          // Always start at 0 — the source MP4 is already trimmed.
          startFrom={0}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      )}

      {active && (
        <CaptionLayer
          segment={active}
          tSeconds={tSeconds}
          fps={fps}
          fontFamily={fontFamily}
          uppercase={props.style.uppercase}
          primary={props.style.primaryColor}
          accent={props.style.accentColor}
          styleName={props.style.style}
          height={props.height}
        />
      )}
    </AbsoluteFill>
  );
};

function CaptionLayer({
  segment,
  tSeconds,
  fps,
  fontFamily,
  uppercase,
  primary,
  accent,
  styleName,
  height,
}: {
  segment: CaptionedClipProps["segments"][number];
  tSeconds: number;
  fps: number;
  fontFamily: string;
  uppercase: boolean;
  primary: string;
  accent: string;
  styleName: CaptionStyleName;
  height: number;
}) {
  const cap = (s: string) => (uppercase ? s.toUpperCase() : s);
  const fontSize = Math.max(48, Math.floor(height / 18));

  return (
    <div
      style={{
        position: "absolute",
        left: "5%",
        right: "5%",
        bottom: "22%",
        textAlign: "center",
        fontFamily,
        fontSize,
        fontWeight: 800,
        color: accent,
        textShadow: "0 4px 12px rgba(0,0,0,0.85)",
        lineHeight: 1.05,
        letterSpacing: 0.5,
      }}
    >
      {styleName === "minimal" && (
        <span style={{ color: primary }}>
          {cap(segment.words.map((w) => w.text).join(" "))}
        </span>
      )}

      {styleName !== "minimal" &&
        segment.words.map((w, i) => {
          const isCurrent = tSeconds >= w.start && tSeconds <= w.end + 0.1;
          const popFrame = (tSeconds - w.start) * fps;
          const scale =
            styleName === "popup"
              ? spring({ frame: popFrame, fps, config: { damping: 14 } })
              : 1;
          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                marginRight: "0.35em",
                color: isCurrent ? primary : accent,
                transform: `scale(${0.85 + 0.15 * scale})`,
                opacity:
                  styleName === "karaoke"
                    ? tSeconds >= w.start
                      ? 1
                      : 0.55
                    : 1,
                transition: "color 80ms linear",
              }}
            >
              {cap(w.text)}
            </span>
          );
        })}
    </div>
  );
}

// Silence the unused-import warning for interpolate; left here as a hook
// for richer animations in future styles.
void interpolate;
