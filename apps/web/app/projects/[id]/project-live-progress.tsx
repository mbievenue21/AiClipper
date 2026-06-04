"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

/**
 * Subscribes to /api/projects/<id>/events (Server-Sent Events) and calls
 * router.refresh() whenever a meaningful change comes in. This replaces
 * the old polling-based RefreshWhenRunning.
 *
 * Strategy:
 * - One EventSource per page lifecycle.
 * - On every `snapshot` event we trigger a server refresh; React's diffing
 *   keeps the UI flicker-free.
 * - If the browser drops the connection we let EventSource auto-reconnect.
 * - We also DOUBLE-refresh when transitioning from active -> idle to make
 *   sure the final "succeeded" state is captured.
 */
export function ProjectLiveProgress({
  projectId,
  active,
}: {
  projectId: string;
  active: boolean;
}) {
  const router = useRouter();
  const wasActive = useRef(active);

  useEffect(() => {
    const url = `/api/projects/${encodeURIComponent(projectId)}/events`;
    let source: EventSource | null = null;
    let lastRefresh = 0;
    const REFRESH_DEBOUNCE_MS = 300;

    try {
      source = new EventSource(url);
    } catch {
      // SSE not supported — let RAF/route revalidation fall back below.
      return;
    }

    const refreshSoon = () => {
      const now = Date.now();
      if (now - lastRefresh < REFRESH_DEBOUNCE_MS) return;
      lastRefresh = now;
      router.refresh();
    };

    source.addEventListener("snapshot", () => {
      refreshSoon();
    });
    source.addEventListener("error", () => {
      // Auto-reconnect is built-in; nothing to do unless we get a final close.
    });
    source.addEventListener("close", () => {
      source?.close();
    });

    // Final-state fallback: when we transition from active -> idle, schedule
    // one extra refresh in case the SSE event landed mid-render.
    if (!active && wasActive.current) {
      wasActive.current = false;
      setTimeout(() => router.refresh(), 200);
    }
    if (active) wasActive.current = true;

    return () => {
      source?.close();
    };
  }, [projectId, active, router]);

  return null;
}
