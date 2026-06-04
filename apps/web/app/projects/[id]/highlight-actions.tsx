"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Film, Loader2, Wand2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

import { renderHighlightAction } from "./actions";
import {
  CaptionStylePicker,
  type CaptionStyleState,
  defaultCaptionStyleState,
} from "./caption-style-picker";

export function RenderHighlightButton({
  projectId,
  highlightId,
  alreadyRendered,
}: {
  projectId: string;
  highlightId: string;
  alreadyRendered: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const [autoCaption, setAutoCaption] = useState(true);
  const [style, setStyle] = useState<CaptionStyleState>(defaultCaptionStyleState());

  const onRender = () => {
    const fd = new FormData();
    fd.set("projectId", projectId);
    fd.set("highlightId", highlightId);
    if (autoCaption) fd.set("autoCaption", "on");
    fd.set("captionFont", style.font);
    fd.set("captionStyle", style.style);
    if (style.autoColor) fd.set("captionAutoColor", "on");
    if (style.uppercase) fd.set("captionUppercase", "on");
    fd.set("captionPrimary", style.primaryColor);
    fd.set("captionAccent", style.accentColor);

    startTransition(async () => {
      const res = await renderHighlightAction(fd);
      (res.ok ? toast.success : toast.error)(res.message);
      if (res.ok) {
        setOpen(false);
        router.refresh();
      }
    });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="sm" variant={alreadyRendered ? "outline" : "default"}>
            <Film className="size-3.5" />
            {alreadyRendered ? "Re-render" : "Render clip"}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Wand2 className="size-4" />
            Render this highlight
          </DialogTitle>
          <DialogDescription>
            We&apos;ll snap the cut to the nearest scene change, reformat to
            the project aspect ratio, normalize audio, and (optionally) burn
            in styled captions.
          </DialogDescription>
        </DialogHeader>

        <label className="flex items-center justify-between rounded-md border p-3 text-sm">
          <div>
            <p className="font-medium">Burn captions immediately</p>
            <p className="text-xs text-muted-foreground">
              Adds a second pass right after rendering. Adds ~10s on most
              clips.
            </p>
          </div>
          <input
            type="checkbox"
            checked={autoCaption}
            onChange={(e) => setAutoCaption(e.target.checked)}
            className="size-4 accent-foreground"
          />
        </label>

        {autoCaption && (
          <CaptionStylePicker value={style} onChange={setStyle} />
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={onRender} disabled={pending}>
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {pending ? "Queuing..." : "Start render"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
