"use client";

import { useCallback, useRef } from "react";

type Peak = { t: number; v: number };

export function EditorTimeline({
  duration,
  trimStart,
  trimEnd,
  playhead,
  peaks,
  zoom,
  onTrimStartChange,
  onTrimEndChange,
  onTrimCommit,
  onSeek,
}: {
  duration: number;
  trimStart: number;
  trimEnd: number;
  playhead: number;
  peaks: Peak[];
  zoom: number;
  onTrimStartChange: (v: number) => void;
  onTrimEndChange: (v: number) => void;
  /** Called when a trim-handle drag ends — commit one undo step. */
  onTrimCommit: () => void;
  onSeek: (t: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const effectiveDuration = Math.max(0.1, duration - trimStart - trimEnd);
  const widthPct = Math.min(400, 100 * zoom);

  const xToTime = useCallback(
    (clientX: number) => {
      const el = trackRef.current;
      if (!el || effectiveDuration <= 0) return 0;
      const rect = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return trimStart + ratio * effectiveDuration;
    },
    [effectiveDuration, trimStart],
  );

  const startDrag = (
    handle: "start" | "end" | "playhead",
    e: React.PointerEvent,
  ) => {
    e.preventDefault();
    const onMove = (ev: PointerEvent) => {
      const t = xToTime(ev.clientX);
      if (handle === "start") {
        onTrimStartChange(Math.max(0, Math.min(t, duration - trimEnd - 1)));
      } else if (handle === "end") {
        onTrimEndChange(Math.max(0, Math.min(duration - t, duration - trimStart - 1)));
      } else {
        onSeek(Math.max(trimStart, Math.min(duration - trimEnd, t)));
      }
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (handle === "start" || handle === "end") {
        onTrimCommit();
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const playRatio =
    effectiveDuration > 0
      ? (playhead - trimStart) / effectiveDuration
      : 0;
  const trimStartRatio = duration > 0 ? trimStart / duration : 0;
  const trimEndRatio = duration > 0 ? trimEnd / duration : 0;
  const selectionLeft = trimStartRatio * 100;
  const selectionWidth = (1 - trimStartRatio - trimEndRatio) * 100;

  const waveform = peaks.length
    ? peaks
    : Array.from({ length: Math.max(20, Math.floor(duration)) }, (_, i) => ({
        t: i,
        v: 0.2,
      }));

  const maxV = Math.max(...waveform.map((p) => p.v), 0.01);

  return (
    <div className="space-y-2 rounded-lg border bg-card p-3">
      <div className="flex justify-between text-[10px] text-muted-foreground">
        {Array.from({ length: 5 }, (_, i) => {
          const t = (i / 4) * duration;
          const m = Math.floor(t / 60);
          const s = Math.floor(t % 60);
          return <span key={i}>{m > 0 ? `${m}m` : `${s}s`}</span>;
        })}
      </div>
      <div
        ref={trackRef}
        className="relative h-20 cursor-pointer overflow-hidden rounded-md bg-muted/40"
        style={{ width: `${widthPct}%`, minWidth: "100%" }}
        onPointerDown={(e) => onSeek(xToTime(e.clientX))}
      >
        <svg
          className="absolute inset-0 h-full w-full opacity-60"
          preserveAspectRatio="none"
          viewBox={`0 0 ${waveform.length} 100`}
        >
          {waveform.map((p, i) => {
            const h = (p.v / maxV) * 90;
            return (
              <rect
                key={i}
                x={i}
                y={100 - h}
                width={1}
                height={h}
                fill="currentColor"
                className="text-foreground/30"
              />
            );
          })}
        </svg>

        <div
          className="absolute inset-y-0 border-x-2 border-primary bg-primary/10"
          style={{ left: `${selectionLeft}%`, width: `${selectionWidth}%` }}
        >
          <div
            className="absolute left-0 top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize rounded-sm bg-primary"
            onPointerDown={(e) => {
              e.stopPropagation();
              startDrag("start", e);
            }}
          />
          <div
            className="absolute right-0 top-0 h-full w-3 translate-x-1/2 cursor-ew-resize rounded-sm bg-primary"
            onPointerDown={(e) => {
              e.stopPropagation();
              startDrag("end", e);
            }}
          />
        </div>

        <div
          className="absolute top-0 h-full w-0.5 bg-primary"
          style={{ left: `${playRatio * selectionWidth + selectionLeft}%` }}
        >
          <div
            className="absolute -top-1 left-1/2 size-3 -translate-x-1/2 rounded-full bg-primary"
            onPointerDown={(e) => {
              e.stopPropagation();
              startDrag("playhead", e);
            }}
          />
        </div>
      </div>
    </div>
  );
}
