"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

/**
 * Re-fetch server data while pipeline jobs are in flight.
 *
 * When jobs finish, `active` flips to false and we must refresh once more —
 * otherwise the last polled snapshot can still show "loading whisper model"
 * or an early ingest message while the DB is already `ready`.
 */
export function RefreshWhenRunning({ active }: { active: boolean }) {
  const router = useRouter();
  const wasActive = useRef(active);

  useEffect(() => {
    if (active) {
      wasActive.current = true;
      const id = setInterval(() => router.refresh(), 2000);
      return () => {
        clearInterval(id);
        // Pipeline just finished — fetch the final succeeded state.
        router.refresh();
      };
    }

    // Transitioned from running → idle without unmounting (e.g. job succeeded).
    if (wasActive.current) {
      wasActive.current = false;
      router.refresh();
    }
  }, [active, router]);

  return null;
}
