"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

/**
 * Subscribes to /api/projects/<id>/events (Server-Sent Events) and calls
 * router.refresh() whenever a meaningful change comes in. This replaces
 * the old polling-based RefreshWhenRunning.
 *
 * Strategy:
 * - One EventSource per page lifecycle, with automatic reconnect when the
 *   server rotates the stream (max lifetime) or the connection drops.
 * - On every `snapshot` event we trigger a server refresh; React's diffing
 *   keeps the UI flicker-free.
 * - When the pipeline was active and goes idle, we double-refresh so the
 *   final "ready" state is captured even if the last SSE tick was missed.
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
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    let lastRefresh = 0;
    const REFRESH_DEBOUNCE_MS = 300;

    const refreshSoon = () => {
      const now = Date.now();
      if (now - lastRefresh < REFRESH_DEBOUNCE_MS) return;
      lastRefresh = now;
      router.refresh();
    };

    const connect = () => {
      if (disposed) return;
      try {
        source = new EventSource(url);
      } catch {
        return;
      }

      source.addEventListener("snapshot", () => {
        refreshSoon();
      });

      // Server sends `close` when rotating the stream (max lifetime). Refresh
      // once so we don't freeze on a stale "indexing" frame, then reconnect.
      source.addEventListener("close", () => {
        refreshSoon();
        source?.close();
        source = null;
        if (!disposed) {
          reconnectTimer = setTimeout(connect, 500);
        }
      });

      source.addEventListener("error", () => {
        // EventSource auto-reconnects on transient errors; refresh on reopen
        // is handled by the next snapshot. If readyState is CLOSED, reconnect.
        if (source?.readyState === EventSource.CLOSED && !disposed) {
          reconnectTimer = setTimeout(connect, 1000);
        }
      });
    };

    connect();

    // Belt-and-suspenders: long pipelines (TL multipart upload) can outlive a
    // single SSE rotation. Poll every 30s while active so the UI never freezes
    // on a stale "indexing" frame if the event stream glitches.
    const poll =
      active
        ? setInterval(() => {
            router.refresh();
          }, 30_000)
        : null;

    // Final-state fallback: when we transition from active -> idle, schedule
    // one extra refresh in case the SSE event landed mid-render.
    if (!active && wasActive.current) {
      wasActive.current = false;
      setTimeout(() => router.refresh(), 200);
    }
    if (active) wasActive.current = true;

    return () => {
      disposed = true;
      if (poll) clearInterval(poll);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [projectId, active, router]);

  return null;
}
