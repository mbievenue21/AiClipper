"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  CalendarClock,
  CaptionsIcon,
  Download,
  Loader2,
  Pencil,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
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
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { clipPreviewUrl, mediaUrl } from "@/lib/media";

import {
  captionClipAction,
  deleteClipAction,
} from "./actions";
import {
  CaptionStylePicker,
  type CaptionStyleState,
  defaultCaptionStyleState,
} from "./caption-style-picker";
import { ScheduleUploadDialog, type AccountOption } from "./schedule-upload-dialog";
import type { GeneratedUploadMetadata } from "@/lib/db/schema";

export type ClipVersionData = {
  id: string;
  createdAt: number;
  durationSeconds: number | null;
  hasCaptions: boolean;
  isActive: boolean;
  versionLabel: string | null;
};

export type ClipCardData = {
  id: string;
  highlightId: string;
  title: string;
  status: "rendering" | "ready" | "captioning" | "failed";
  filePath: string;
  captionedFilePath: string | null;
  hasCaptions: boolean;
  aspect: string;
  durationSeconds: number | null;
  dominantColor: string | null;
  errorMessage: string | null;
  captionStyle: CaptionStyleState | null;
  generatedMetadata: GeneratedUploadMetadata | null;
  createdAt: number;
  versionLabel: string | null;
  versions: ClipVersionData[];
  // Latest render/caption job (for progress while in-flight).
  activeJob: {
    type: string;
    progress: number;
    progressMessage: string | null;
  } | null;
  uploads: {
    id: string;
    platform: "youtube" | "instagram";
    status: string;
    scheduledFor: number;
    timezone: string;
    externalUrl: string | null;
    errorMessage: string | null;
  }[];
};

export function ClipCard({
  projectId,
  clip,
  accounts,
  defaultTitle,
  defaultDescription,
}: {
  projectId: string;
  clip: ClipCardData;
  accounts: AccountOption[];
  defaultTitle: string;
  defaultDescription: string;
}) {
  const previewUrl = clipPreviewUrl({
    filePath: clip.filePath,
    captionedFilePath: clip.captionedFilePath,
  });
  const captionedUrl = mediaUrl(clip.captionedFilePath);
  const cleanUrl = mediaUrl(clip.filePath);
  const isRendering = clip.status === "rendering";
  const isCaptioning = clip.status === "captioning";
  const isBusy = isRendering || isCaptioning;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">{clip.title}</CardTitle>
            <CardDescription className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
              <Badge variant="outline">{clip.aspect}</Badge>
              {clip.versionLabel && (
                <Badge variant="secondary">{clip.versionLabel}</Badge>
              )}
              {clip.durationSeconds != null && (
                <span>{clip.durationSeconds.toFixed(1)}s</span>
              )}
              <span className="text-muted-foreground">
                {new Date(clip.createdAt).toLocaleString()}
              </span>
              {clip.dominantColor && (
                <span className="flex items-center gap-1">
                  <span
                    className="inline-block size-3 rounded-full ring-1 ring-foreground/20"
                    style={{ background: clip.dominantColor }}
                  />
                  {clip.dominantColor}
                </span>
              )}
            </CardDescription>
          </div>
          <ClipStatusBadge status={clip.status} hasCaptions={clip.hasCaptions} />
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {isBusy && clip.activeJob && (
          <div className="space-y-1">
            <Progress value={Math.round(clip.activeJob.progress * 100)} />
            <p className="text-xs text-muted-foreground">
              {clip.activeJob.type === "caption"
                ? "Burning captions…"
                : clip.activeJob.type === "reedit"
                  ? "Saving edits…"
                  : "Rendering…"}
              {clip.activeJob.progressMessage
                ? ` — ${clip.activeJob.progressMessage}`
                : null}
            </p>
          </div>
        )}

        {previewUrl ? (
          <video
            key={previewUrl}
            src={previewUrl}
            controls
            preload="metadata"
            className="w-full rounded-md bg-black"
            style={{
              maxHeight: 360,
              aspectRatio:
                clip.aspect === "9:16"
                  ? "9 / 16"
                  : clip.aspect === "1:1"
                    ? "1 / 1"
                    : "16 / 9",
            }}
          />
        ) : (
          <div className="grid h-32 place-items-center rounded-md border border-dashed text-xs text-muted-foreground">
            {isBusy ? "Rendering…" : "No preview available."}
          </div>
        )}

        {clip.errorMessage && (
          <pre className="max-h-32 overflow-auto rounded-md bg-destructive/10 p-2 text-[11px] text-destructive whitespace-pre-wrap">
            {clip.errorMessage}
          </pre>
        )}

        {clip.uploads.length > 0 && (
          <UploadList projectId={projectId} uploads={clip.uploads} />
        )}

        {clip.versions.length > 1 && (
          <div className="rounded-md border bg-muted/30 p-2 text-xs">
            <p className="mb-1 font-medium text-muted-foreground">Versions</p>
            <ul className="space-y-0.5">
              {clip.versions.map((v) => (
                <li key={v.id} className="flex justify-between gap-2">
                  <span>
                    {new Date(v.createdAt).toLocaleString()}
                    {v.versionLabel ? ` — ${v.versionLabel}` : ""}
                    {v.isActive ? " (active)" : ""}
                  </span>
                  <span className="text-muted-foreground">
                    {v.durationSeconds?.toFixed(0) ?? "?"}s
                    {v.hasCaptions ? " · captioned" : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>

      <CardFooter className="flex flex-wrap gap-2 pt-0">
        {clip.status === "ready" && (
          <>
            <Button asChild size="sm" variant="default">
              <Link href={`/projects/${projectId}/clips/${clip.id}/edit`}>
                <Pencil className="size-3.5" />
                Edit clip
              </Link>
            </Button>
            <CaptionStyleDialog
              projectId={projectId}
              clipId={clip.id}
              dominantColor={clip.dominantColor}
              initial={clip.captionStyle}
              hasCaptions={clip.hasCaptions}
            />
            <ScheduleUploadDialog
              projectId={projectId}
              highlightId={clip.highlightId}
              clipId={clip.id}
              accounts={accounts}
              defaultTitle={defaultTitle}
              defaultDescription={defaultDescription}
              initialMetadata={clip.generatedMetadata}
            />
            {(cleanUrl || captionedUrl) && (
              <Button asChild variant="ghost" size="sm">
                <a href={captionedUrl || cleanUrl || "#"} download>
                  <Download className="size-3.5" />
                  Download
                </a>
              </Button>
            )}
          </>
        )}
        <DeleteClipButton projectId={projectId} clipId={clip.id} />
      </CardFooter>
    </Card>
  );
}

function ClipStatusBadge({
  status,
  hasCaptions,
}: {
  status: ClipCardData["status"];
  hasCaptions: boolean;
}) {
  if (status === "failed") return <Badge variant="destructive">failed</Badge>;
  if (status === "rendering") return <Badge variant="secondary">rendering</Badge>;
  if (status === "captioning") return <Badge variant="secondary">captioning</Badge>;
  return (
    <Badge>
      ready{hasCaptions ? " · captioned" : ""}
    </Badge>
  );
}

function CaptionStyleDialog({
  projectId,
  clipId,
  dominantColor,
  initial,
  hasCaptions,
}: {
  projectId: string;
  clipId: string;
  dominantColor: string | null;
  initial: CaptionStyleState | null;
  hasCaptions: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const [style, setStyle] = useState<CaptionStyleState>(
    initial ?? defaultCaptionStyleState(),
  );

  const onApply = () => {
    const fd = new FormData();
    fd.set("projectId", projectId);
    fd.set("clipId", clipId);
    fd.set("captionFont", style.font);
    fd.set("captionStyle", style.style);
    if (style.autoColor) fd.set("captionAutoColor", "on");
    if (style.uppercase) fd.set("captionUppercase", "on");
    fd.set("captionPrimary", style.primaryColor);
    fd.set("captionAccent", style.accentColor);
    startTransition(async () => {
      const res = await captionClipAction(fd);
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
          <Button size="sm" variant={hasCaptions ? "outline" : "secondary"}>
            <CaptionsIcon className="size-3.5" />
            {hasCaptions ? "Restyle captions" : "Add captions"}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Caption style</DialogTitle>
          <DialogDescription>
            {dominantColor ? (
              <>
                Clip dominant color is{" "}
                <span
                  className="inline-block size-3 translate-y-0.5 rounded-full ring-1 ring-foreground/20"
                  style={{ background: dominantColor }}
                />{" "}
                — auto-color will pick a contrasting gradient.
              </>
            ) : (
              "Auto-color picks colors that pop against the clip."
            )}
          </DialogDescription>
        </DialogHeader>
        <CaptionStylePicker value={style} onChange={setStyle} />
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={onApply} disabled={pending}>
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {pending ? "Queuing..." : hasCaptions ? "Re-render captions" : "Burn captions"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteClipButton({
  projectId,
  clipId,
}: {
  projectId: string;
  clipId: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const onClick = () => {
    if (!confirm("Delete this clip? Files on disk are kept.")) return;
    const fd = new FormData();
    fd.set("projectId", projectId);
    fd.set("clipId", clipId);
    startTransition(async () => {
      const res = await deleteClipAction(fd);
      (res.ok ? toast.success : toast.error)(res.message);
      router.refresh();
    });
  };

  return (
    <Button
      onClick={onClick}
      variant="ghost"
      size="sm"
      disabled={pending}
      className="text-destructive hover:text-destructive ml-auto"
    >
      {pending ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
      Delete
    </Button>
  );
}

function UploadList({
  projectId,
  uploads,
}: {
  projectId: string;
  uploads: ClipCardData["uploads"];
}) {
  return (
    <div className="rounded-md border bg-muted/30 p-2">
      <p className="mb-1 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <CalendarClock className="size-3" />
        Scheduled uploads
      </p>
      <ul className="space-y-1.5">
        {uploads.map((u) => (
          <li
            key={u.id}
            className="flex flex-wrap items-center gap-2 text-xs"
          >
            <Badge variant="outline">{u.platform}</Badge>
            <UploadStatusPill status={u.status} />
            <span className="text-muted-foreground">
              {new Date(u.scheduledFor).toLocaleString(undefined, {
                timeZone: u.timezone,
                dateStyle: "medium",
                timeStyle: "short",
              })}{" "}
              <span className="opacity-60">({u.timezone})</span>
            </span>
            {u.externalUrl && (
              <a
                href={u.externalUrl}
                target="_blank"
                rel="noreferrer"
                className="ml-auto underline hover:text-foreground"
              >
                Open
              </a>
            )}
            {u.errorMessage && (
              <span
                className="ml-auto truncate text-destructive"
                title={u.errorMessage}
              >
                {u.errorMessage.slice(0, 40)}
              </span>
            )}
            {(u.status === "pending" || u.status === "failed") && (
              <CancelUploadButton projectId={projectId} uploadId={u.id} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function UploadStatusPill({ status }: { status: string }) {
  const variant: "default" | "secondary" | "destructive" | "outline" =
    status === "uploaded"
      ? "default"
      : status === "failed"
        ? "destructive"
        : status === "uploading"
          ? "secondary"
          : status === "cancelled"
            ? "outline"
            : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

function CancelUploadButton({
  projectId,
  uploadId,
}: {
  projectId: string;
  uploadId: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      type="button"
      className="text-[11px] text-muted-foreground underline hover:text-destructive"
      disabled={pending}
      onClick={() => {
        const fd = new FormData();
        fd.set("uploadId", uploadId);
        fd.set("projectId", projectId);
        startTransition(async () => {
          const { cancelScheduledUploadAction } = await import("./actions");
          const res = await cancelScheduledUploadAction(fd);
          (res.ok ? toast.success : toast.error)(res.message);
          router.refresh();
        });
      }}
    >
      Cancel
    </button>
  );
}
