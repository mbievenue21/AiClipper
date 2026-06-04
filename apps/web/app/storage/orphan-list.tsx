"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { deleteOrphanByPathAction } from "./actions";

export type OrphanRow = {
  relPath: string;
  bytes: number;
  mtimeMs: number;
};

function formatBytes(n: number): string {
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

function timeAgo(ms: number): string {
  const diff = Date.now() - ms;
  const m = Math.floor(diff / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function OrphanList({ orphans }: { orphans: OrphanRow[] }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [deletingPath, setDeletingPath] = useState<string | null>(null);

  if (orphans.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No orphan files — every file on disk maps to a DB row. Nice.
      </p>
    );
  }

  const deleteOne = (relPath: string) => {
    setDeletingPath(relPath);
    startTransition(async () => {
      try {
        const res = await deleteOrphanByPathAction(relPath);
        (res.ok ? toast.success : toast.error)(res.message);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setDeletingPath(null);
        router.refresh();
      }
    });
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr className="border-b">
            <th className="px-2 py-2 text-left font-medium">Path</th>
            <th className="px-2 py-2 text-right font-medium">Size</th>
            <th className="px-2 py-2 text-right font-medium">Modified</th>
            <th className="px-2 py-2 text-right font-medium" />
          </tr>
        </thead>
        <tbody>
          {orphans.slice(0, 50).map((o) => (
            <tr key={o.relPath} className="border-b last:border-b-0">
              <td className="max-w-[40ch] truncate px-2 py-2 font-mono">
                <span title={o.relPath}>{o.relPath}</span>
              </td>
              <td className="px-2 py-2 text-right">{formatBytes(o.bytes)}</td>
              <td className="px-2 py-2 text-right text-muted-foreground">
                {timeAgo(o.mtimeMs)}
              </td>
              <td className="px-2 py-2 text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={pending}
                  className="text-destructive hover:text-destructive"
                  onClick={() => deleteOne(o.relPath)}
                >
                  {deletingPath === o.relPath ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="size-3.5" />
                  )}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {orphans.length > 50 && (
        <p className="mt-2 text-xs text-muted-foreground">
          Showing 50 of {orphans.length} orphan files. Run{" "}
          <Badge variant="outline">Delete orphan files</Badge> above to clear
          them all at once.
        </p>
      )}
    </div>
  );
}
