"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-fetch server data while ingest (or other) jobs are in flight. */
export function RefreshWhenRunning({ active }: { active: boolean }) {
  const router = useRouter();

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => router.refresh(), 2000);
    return () => clearInterval(id);
  }, [active, router]);

  return null;
}
