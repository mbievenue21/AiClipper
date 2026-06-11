/** Extended editor timeline: highlight window ± pad seconds on the source VOD. */

export type EditorWindow = {
  windowStart: number;
  windowEnd: number;
  windowDuration: number;
  /** Highlight region inside the editor timeline (seconds from windowStart). */
  highlightOffsetStart: number;
  highlightOffsetEnd: number;
};

export function computeEditorWindow(
  highlightStart: number,
  highlightEnd: number,
  sourceDuration: number,
  padBefore: number,
  padAfter: number,
): EditorWindow {
  const windowStart = Math.max(0, highlightStart - padBefore);
  const windowEnd = Math.min(
    Math.max(highlightEnd, highlightStart + 1),
    sourceDuration > 0 ? sourceDuration : highlightEnd + padAfter,
  );
  const cappedEnd = Math.min(windowEnd, highlightEnd + padAfter);
  return {
    windowStart,
    windowEnd: Math.max(cappedEnd, windowStart + 1),
    windowDuration: Math.max(1, cappedEnd - windowStart),
    highlightOffsetStart: highlightStart - windowStart,
    highlightOffsetEnd: highlightEnd - windowStart,
  };
}

export function initialTrimFromSource(
  window: EditorWindow,
  highlightStart: number,
  highlightEnd: number,
  sourceStart: number | null,
  sourceEnd: number | null,
  storedTrimStart: number,
  storedTrimEnd: number,
): { trimStart: number; trimEnd: number } {
  if (sourceStart != null && sourceEnd != null) {
    return {
      trimStart: Math.max(0, sourceStart - window.windowStart),
      trimEnd: Math.max(0, window.windowEnd - sourceEnd),
    };
  }
  return { trimStart: storedTrimStart, trimEnd: storedTrimEnd };
}

export function sourceCutFromTrim(
  window: EditorWindow,
  trimStart: number,
  trimEnd: number,
) {
  return {
    cutStart: window.windowStart + trimStart,
    cutEnd: window.windowEnd - trimEnd,
  };
}
