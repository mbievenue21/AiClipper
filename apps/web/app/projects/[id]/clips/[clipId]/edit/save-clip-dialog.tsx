"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { CaptionSegmentOverride } from "@/lib/db/schema";

import { saveClipEditAction } from "../../../actions";

export function SaveClipDialog({
  open,
  onOpenChange,
  projectId,
  clipId,
  trimStart,
  trimEnd,
  captionSegments,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  clipId: string;
  trimStart: number;
  trimEnd: number;
  captionSegments: CaptionSegmentOverride[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [mode, setMode] = useState<"replace" | "version" | null>(null);

  const save = (replaceOriginal: boolean) => {
    setMode(replaceOriginal ? "replace" : "version");
    startTransition(async () => {
      const res = await saveClipEditAction({
        projectId,
        clipId,
        trimStart,
        trimEnd,
        captionSegments,
        replaceOriginal,
      });
      (res.ok ? toast.success : toast.error)(res.message);
      if (res.ok) {
        onOpenChange(false);
        router.push(`/projects/${projectId}`);
        router.refresh();
      }
      setMode(null);
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save edited clip</DialogTitle>
          <DialogDescription>
            Replace the current render, or keep both versions with timestamps in
            your clip list.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex-col gap-2 sm:flex-col">
          <Button
            disabled={pending}
            onClick={() => save(true)}
            className="w-full"
          >
            {pending && mode === "replace" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null}
            Replace original
          </Button>
          <Button
            variant="outline"
            disabled={pending}
            onClick={() => save(false)}
            className="w-full"
          >
            {pending && mode === "version" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null}
            Save as new version (keep both)
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
