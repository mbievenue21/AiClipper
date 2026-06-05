"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Captions,
  Minus,
  Pause,
  Play,
  Plus,
  Redo2,
  Undo2,
  ZoomIn,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CaptionSegmentOverride, ClipCaptionSettings } from "@/lib/db/schema";
import { mediaUrl } from "@/lib/media";
import { resolveEditorCaptionSegments } from "@/lib/clips/caption-segments";

import { CaptionSegmentList } from "./caption-segment-list";
import { EditorTimeline } from "./editor-timeline";
import { SaveClipDialog } from "./save-clip-dialog";

type EditorState = {
  trimStart: number;
  trimEnd: number;
  captionSegments: CaptionSegmentOverride[];
};

type Peak = { t: number; v: number };

type HistoryState = {
  entries: EditorState[];
  index: number;
};

type HistoryAction =
  | { type: "push"; state: EditorState }
  | { type: "undo" }
  | { type: "redo" };

function historyReducer(
  prev: HistoryState,
  action: HistoryAction,
): HistoryState {
  switch (action.type) {
    case "push": {
      const nextIndex = prev.index + 1;
      return {
        entries: [...prev.entries.slice(0, nextIndex), action.state],
        index: nextIndex,
      };
    }
    case "undo":
      return { ...prev, index: Math.max(0, prev.index - 1) };
    case "redo":
      return {
        ...prev,
        index: Math.min(prev.entries.length - 1, prev.index + 1),
      };
    default:
      return prev;
  }
}

export function ClipEditor({
  projectId,
  clipId,
  title,
  filePath,
  aspect,
  sourceDuration,
  highlightStart,
  highlightEnd,
  sourceStart,
  sourceEnd,
  storedTrimStart,
  storedTrimEnd,
  storedCaptionSegments,
  transcriptSegments,
  captionStyle,
}: {
  projectId: string;
  clipId: string;
  title: string;
  filePath: string;
  aspect: string;
  sourceDuration: number;
  highlightStart: number;
  highlightEnd: number;
  sourceStart: number | null;
  sourceEnd: number | null;
  storedTrimStart: number;
  storedTrimEnd: number;
  storedCaptionSegments: CaptionSegmentOverride[] | null;
  transcriptSegments: { startSeconds: number; endSeconds: number; text: string }[];
  captionStyle: ClipCaptionSettings | null;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [showCaptions, setShowCaptions] = useState(true);
  const [saveOpen, setSaveOpen] = useState(false);
  const [peaks, setPeaks] = useState<Peak[]>([]);

  const baseDuration = Math.max(1, highlightEnd - highlightStart);

  const initialState = useMemo<EditorState>(
    () => ({
      trimStart: storedTrimStart,
      trimEnd: storedTrimEnd,
      captionSegments: resolveEditorCaptionSegments(
        storedCaptionSegments,
        transcriptSegments,
        highlightStart,
        highlightEnd,
        storedTrimStart,
        storedTrimEnd,
        sourceStart,
        sourceEnd,
      ),
    }),
    [
      storedTrimStart,
      storedTrimEnd,
      storedCaptionSegments,
      transcriptSegments,
      highlightStart,
      highlightEnd,
      sourceStart,
      sourceEnd,
    ],
  );

  const [history, dispatchHistory] = useReducer(historyReducer, {
    entries: [initialState],
    index: 0,
  });
  // Live trim preview while dragging — avoids pushing dozens of undo steps per
  // pointermove and prevents historyIdx from racing ahead of history.length.
  const [trimDraft, setTrimDraft] = useState<{
    trimStart?: number;
    trimEnd?: number;
  } | null>(null);

  const committed =
    history.entries[history.index] ?? history.entries.at(-1) ?? initialState;

  const state = useMemo<EditorState>(() => {
    const trimStart = trimDraft?.trimStart ?? committed.trimStart;
    const trimEnd = trimDraft?.trimEnd ?? committed.trimEnd;
    return {
      trimStart,
      trimEnd,
      captionSegments: trimDraft
        ? resolveEditorCaptionSegments(
            committed.captionSegments,
            transcriptSegments,
            highlightStart,
            highlightEnd,
            trimStart,
            trimEnd,
            sourceStart,
            sourceEnd,
            true,
          )
        : committed.captionSegments,
    };
  }, [
    committed,
    trimDraft,
    transcriptSegments,
    highlightStart,
    highlightEnd,
  ]);

  const effectiveDuration = baseDuration - state.trimStart - state.trimEnd;

  const pushState = useCallback((next: EditorState) => {
    dispatchHistory({ type: "push", state: next });
  }, []);

  const previewTrimStart = (v: number) => {
    setTrimDraft((d) => ({ ...d, trimStart: v }));
  };

  const previewTrimEnd = (v: number) => {
    setTrimDraft((d) => ({ ...d, trimEnd: v }));
  };

  const commitTrim = useCallback(() => {
    if (!trimDraft) return;
    const trimStart = trimDraft.trimStart ?? committed.trimStart;
    const trimEnd = trimDraft.trimEnd ?? committed.trimEnd;
    const unchanged =
      trimStart === committed.trimStart && trimEnd === committed.trimEnd;
    setTrimDraft(null);
    if (!unchanged) {
      pushState({
        trimStart,
        trimEnd,
        captionSegments: resolveEditorCaptionSegments(
          committed.captionSegments,
          transcriptSegments,
          highlightStart,
          highlightEnd,
          trimStart,
          trimEnd,
          sourceStart,
          sourceEnd,
          true,
        ),
      });
    }
  }, [
    trimDraft,
    committed,
    pushState,
    transcriptSegments,
    highlightStart,
    highlightEnd,
  ]);

  const setCaptionSegments = (segments: CaptionSegmentOverride[]) => {
    pushState({ ...state, captionSegments: segments });
  };

  const undo = () => {
    setTrimDraft(null);
    dispatchHistory({ type: "undo" });
  };
  const redo = () => {
    setTrimDraft(null);
    dispatchHistory({ type: "redo" });
  };

  useEffect(() => {
    fetch(`/api/projects/${projectId}/waveform`)
      .then((r) => r.json())
      .then((data: { peaks?: Peak[] }) => {
        const all = data.peaks ?? [];
        setPeaks(
          all.filter(
            (p) => p.t >= highlightStart && p.t <= highlightEnd,
          ).map((p) => ({ t: p.t - highlightStart, v: p.v })),
        );
      })
      .catch(() => setPeaks([]));
  }, [projectId, highlightStart, highlightEnd]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => {
      const rel = v.currentTime + state.trimStart;
      setPlayhead(rel);
    };
    v.addEventListener("timeupdate", onTime);
    return () => v.removeEventListener("timeupdate", onTime);
  }, [state.trimStart]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) {
      v.pause();
      setPlaying(false);
    } else {
      if (v.currentTime < 0.01 || v.currentTime >= effectiveDuration - 0.05) {
        v.currentTime = 0;
      }
      void v.play();
      setPlaying(true);
    }
  };

  const seek = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    const rel = Math.max(0, Math.min(effectiveDuration, t - state.trimStart));
    v.currentTime = rel;
    setPlayhead(t);
  };

  const activeCaption = state.captionSegments.find(
    (s) => playhead >= s.start && playhead <= s.end,
  );

  const formatClock = (t: number) => {
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const s = Math.floor(t % 60);
    const f = Math.floor((t % 1) * 30);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`;
  };

  const previewUrl = mediaUrl(filePath);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 p-4 pb-8">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/projects/${projectId}`}>
            <ArrowLeft className="size-4" />
            Back
          </Link>
        </Button>
        <h1 className="truncate text-lg font-semibold">Edit: {title}</h1>
      </div>

      <div className="relative overflow-hidden rounded-lg bg-black">
        <video
          ref={videoRef}
          src={previewUrl ?? undefined}
          className="mx-auto max-h-[50vh] w-full"
          style={{
            aspectRatio:
              aspect === "9:16" ? "9/16" : aspect === "1:1" ? "1/1" : "16/9",
          }}
          onEnded={() => setPlaying(false)}
        />
        {showCaptions && activeCaption && (
          <div
            className="pointer-events-none absolute inset-x-0 bottom-[18%] px-4 text-center text-xl font-bold uppercase tracking-wide text-white drop-shadow-lg"
            style={{
              fontFamily: captionStyle?.font ?? "anton",
              color: captionStyle?.primaryColor ?? "#FFD700",
            }}
          >
            {activeCaption.text}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2">
        <div className="flex flex-wrap gap-1">
          <Button type="button" variant="ghost" size="sm" disabled>
            Split
          </Button>
          <Button type="button" variant="ghost" size="sm" disabled>
            Delete
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={undo}
            disabled={history.index <= 0}
          >
            <Undo2 className="size-3.5" />
            Undo
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={redo}
            disabled={history.index >= history.entries.length - 1}
          >
            <Redo2 className="size-3.5" />
            Redo
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" size="icon" onClick={togglePlay}>
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </Button>
          <span className="font-mono text-sm tabular-nums">
            {formatClock(playhead - state.trimStart)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
          >
            <Minus className="size-4" />
          </Button>
          <ZoomIn className="size-4 text-muted-foreground" />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
          >
            <Plus className="size-4" />
          </Button>
          <Button
            type="button"
            variant={showCaptions ? "secondary" : "outline"}
            size="sm"
            onClick={() => setShowCaptions((v) => !v)}
          >
            <Captions className="size-3.5" />
            Captions
          </Button>
        </div>
      </div>

      <EditorTimeline
        duration={baseDuration}
        trimStart={state.trimStart}
        trimEnd={state.trimEnd}
        playhead={playhead}
        peaks={peaks}
        zoom={zoom}
        onTrimStartChange={previewTrimStart}
        onTrimEndChange={previewTrimEnd}
        onTrimCommit={commitTrim}
        onSeek={seek}
      />

      <CaptionSegmentList
        segments={state.captionSegments}
        onChange={setCaptionSegments}
        visible={showCaptions}
      />

      <div className="flex justify-end gap-2">
        <Button variant="outline" asChild>
          <Link href={`/projects/${projectId}`}>Cancel</Link>
        </Button>
        <Button onClick={() => setSaveOpen(true)}>Save</Button>
      </div>

      <SaveClipDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        projectId={projectId}
        clipId={clipId}
        trimStart={state.trimStart}
        trimEnd={state.trimEnd}
        captionSegments={state.captionSegments}
      />
    </div>
  );
}
