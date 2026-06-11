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
import {
  computeEditorWindow,
  initialTrimFromSource,
} from "@/lib/clips/editor-window";

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
  sourceVideoPath,
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
  editorPadBefore,
  editorPadAfter,
}: {
  projectId: string;
  clipId: string;
  title: string;
  sourceVideoPath: string;
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
  editorPadBefore: number;
  editorPadAfter: number;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [showCaptions, setShowCaptions] = useState(true);
  const [saveOpen, setSaveOpen] = useState(false);
  const [peaks, setPeaks] = useState<Peak[]>([]);

  const editorWindow = useMemo(
    () =>
      computeEditorWindow(
        highlightStart,
        highlightEnd,
        sourceDuration,
        editorPadBefore,
        editorPadAfter,
      ),
    [
      highlightStart,
      highlightEnd,
      sourceDuration,
      editorPadBefore,
      editorPadAfter,
    ],
  );

  const baseDuration = editorWindow.windowDuration;

  const initialTrim = useMemo(
    () =>
      initialTrimFromSource(
        editorWindow,
        highlightStart,
        highlightEnd,
        sourceStart,
        sourceEnd,
        storedTrimStart,
        storedTrimEnd,
      ),
    [
      editorWindow,
      highlightStart,
      highlightEnd,
      sourceStart,
      sourceEnd,
      storedTrimStart,
      storedTrimEnd,
    ],
  );

  const initialState = useMemo<EditorState>(
    () => ({
      trimStart: initialTrim.trimStart,
      trimEnd: initialTrim.trimEnd,
      captionSegments: resolveEditorCaptionSegments(
        storedCaptionSegments,
        transcriptSegments,
        editorWindow.windowStart,
        editorWindow.windowEnd,
        initialTrim.trimStart,
        initialTrim.trimEnd,
        null,
        null,
        true,
      ),
    }),
    [
      initialTrim.trimStart,
      initialTrim.trimEnd,
      storedCaptionSegments,
      transcriptSegments,
      editorWindow.windowStart,
      editorWindow.windowEnd,
    ],
  );

  const [history, dispatchHistory] = useReducer(historyReducer, {
    entries: [initialState],
    index: 0,
  });

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
            editorWindow.windowStart,
            editorWindow.windowEnd,
            trimStart,
            trimEnd,
            null,
            null,
            true,
          )
        : committed.captionSegments,
    };
  }, [
    committed,
    trimDraft,
    transcriptSegments,
    editorWindow.windowStart,
    editorWindow.windowEnd,
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
          editorWindow.windowStart,
          editorWindow.windowEnd,
          trimStart,
          trimEnd,
          null,
          null,
          true,
        ),
      });
    }
  }, [
    trimDraft,
    committed,
    pushState,
    transcriptSegments,
    editorWindow.windowStart,
    editorWindow.windowEnd,
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
          all
            .filter(
              (p) =>
                p.t >= editorWindow.windowStart &&
                p.t <= editorWindow.windowEnd,
            )
            .map((p) => ({ t: p.t - editorWindow.windowStart, v: p.v })),
        );
      })
      .catch(() => setPeaks([]));
  }, [projectId, editorWindow.windowStart, editorWindow.windowEnd]);

  const windowStart = editorWindow.windowStart;

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => {
      setPlayhead(v.currentTime - windowStart);
    };
    v.addEventListener("timeupdate", onTime);
    return () => v.removeEventListener("timeupdate", onTime);
  }, [windowStart]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const t = windowStart + state.trimStart;
    v.currentTime = t;
    setPlayhead(state.trimStart);
  }, [windowStart, state.trimStart]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) {
      v.pause();
      setPlaying(false);
    } else {
      const selStart = windowStart + state.trimStart;
      const selEnd = windowStart + baseDuration - state.trimEnd;
      if (v.currentTime < selStart - 0.01 || v.currentTime >= selEnd - 0.05) {
        v.currentTime = selStart;
        setPlayhead(state.trimStart);
      }
      void v.play();
      setPlaying(true);
    }
  };

  const seek = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    const clamped = Math.max(
      state.trimStart,
      Math.min(baseDuration - state.trimEnd, t),
    );
    v.currentTime = windowStart + clamped;
    setPlayhead(clamped);
  };

  const activeCaption = state.captionSegments.find(
    (s) =>
      playhead - state.trimStart >= s.start &&
      playhead - state.trimStart <= s.end,
  );

  const formatClock = (t: number) => {
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const s = Math.floor(t % 60);
    const f = Math.floor((t % 1) * 30);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`;
  };

  const sourcePreviewUrl = mediaUrl(sourceVideoPath);

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

      <p className="text-xs text-muted-foreground">
        Extended editor: ±{editorPadBefore}s / ±{editorPadAfter}s beyond the
        highlight window. Drag handles into the padded region to add buildup or
        reaction tail — saves train your default pre-roll.
      </p>

      <div className="relative overflow-hidden rounded-lg bg-black">
        <video
          ref={videoRef}
          src={sourcePreviewUrl ?? undefined}
          className="mx-auto max-h-[50vh] w-full"
          style={{
            aspectRatio:
              aspect === "9:16" ? "9/16" : aspect === "1:1" ? "1/1" : "16/9",
          }}
          onLoadedMetadata={(e) => {
            e.currentTarget.currentTime = windowStart + state.trimStart;
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
        highlightOffsetStart={editorWindow.highlightOffsetStart}
        highlightOffsetEnd={editorWindow.highlightOffsetEnd}
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
        editorWindowStart={editorWindow.windowStart}
        editorWindowEnd={editorWindow.windowEnd}
        captionSegments={state.captionSegments}
      />
    </div>
  );
}
