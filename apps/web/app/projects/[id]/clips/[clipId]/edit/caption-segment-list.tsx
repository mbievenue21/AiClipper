"use client";

import type { CaptionSegmentOverride } from "@/lib/db/schema";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Plus, Trash2 } from "lucide-react";

function formatSegTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)}s`;
}

export function CaptionSegmentList({
  segments,
  onChange,
  visible,
}: {
  segments: CaptionSegmentOverride[];
  onChange: (segments: CaptionSegmentOverride[]) => void;
  visible: boolean;
}) {
  if (!visible) return null;

  const update = (idx: number, patch: Partial<CaptionSegmentOverride>) => {
    const next = segments.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    onChange(next);
  };

  const remove = (idx: number) => {
    onChange(segments.filter((_, i) => i !== idx));
  };

  const add = () => {
    const lastEnd = segments.length ? segments[segments.length - 1].end : 0;
    onChange([
      ...segments,
      { start: lastEnd, end: lastEnd + 2, text: "" },
    ]);
  };

  return (
    <div className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Captions</span>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="size-3.5" />
          Add line
        </Button>
      </div>
      {segments.length === 0 ? (
        <p className="text-xs text-muted-foreground">No caption segments in this range.</p>
      ) : (
        <ul className="max-h-48 space-y-2 overflow-y-auto">
          {segments.map((seg, idx) => (
            <li
              key={`${seg.start}-${idx}`}
              className="flex items-start gap-2 rounded-md border bg-background p-2"
            >
              <span className="shrink-0 pt-2 font-mono text-[10px] text-muted-foreground">
                {formatSegTime(seg.start)}
              </span>
              <Input
                value={seg.text}
                onChange={(e) => update(idx, { text: e.target.value })}
                className="h-8 text-sm"
                placeholder="Caption text"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-8 shrink-0"
                onClick={() => remove(idx)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
