"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import {
  deleteAudioExtractsAction,
  deleteChatDumpsAction,
  deleteFailedProjectMediaAction,
  deleteOrphanFilesAction,
  deleteReadySourcesAction,
  deleteUncaptionedClipsAction,
  pruneCancelledUploadsAction,
  pruneFinishedJobsAction,
  vacuumDatabaseAction,
  wipeAllMediaAction,
  wipeNextCacheAction,
  type CleanupResult,
} from "./actions";

type Action = () => Promise<CleanupResult>;

type Severity = "safe" | "caution" | "danger";

function ActionRow({
  label,
  description,
  action,
  severity = "safe",
  confirm,
  hint,
}: {
  label: string;
  description: string;
  action: Action;
  severity?: Severity;
  confirm?: string;
  hint?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const variant: "default" | "secondary" | "destructive" | "outline" =
    severity === "safe"
      ? "default"
      : severity === "caution"
        ? "secondary"
        : "destructive";
  const tag: "safe" | "caution" | "danger" = severity;

  const run = () => {
    if (confirm && !window.confirm(confirm)) return;
    startTransition(async () => {
      try {
        const res = await action();
        (res.ok ? toast.success : toast.error)(res.message);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        router.refresh();
      }
    });
  };

  return (
    <div className="flex items-start justify-between gap-3 rounded-md border p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">{label}</p>
          <SeverityPill tag={tag} />
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        {hint && (
          <p className="mt-0.5 text-[11px] text-amber-700 dark:text-amber-300">
            {hint}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant={variant}
        onClick={run}
        disabled={pending}
        className="shrink-0"
      >
        {pending && <Loader2 className="size-3.5 animate-spin" />}
        {pending ? "Running…" : "Run"}
      </Button>
    </div>
  );
}

function SeverityPill({ tag }: { tag: Severity }) {
  if (tag === "safe")
    return (
      <Badge variant="outline" className="text-[10px] uppercase">
        safe
      </Badge>
    );
  if (tag === "caution")
    return (
      <Badge
        variant="secondary"
        className="text-[10px] uppercase text-amber-700 dark:text-amber-300"
      >
        caution
      </Badge>
    );
  return (
    <Badge variant="destructive" className="text-[10px] uppercase">
      destructive
    </Badge>
  );
}

export function StorageActionsPanel() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Disk cleanup</CardTitle>
          <CardDescription>
            Reclaim space under <code className="rounded bg-muted px-1">data/videos/</code>
            . Severity tags tell you what&apos;s safe to run any time vs. what
            forfeits future re-processing.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <ActionRow
            label="Delete audio extracts (.wav)"
            description="Removes audio extracted for transcription. Only needed during the transcribe stage."
            action={deleteAudioExtractsAction}
            severity="safe"
          />
          <ActionRow
            label="Delete chat replay JSONs"
            description="Removes Twitch / YouTube chat dumps used during highlight analysis."
            action={deleteChatDumpsAction}
            severity="safe"
          />
          <ActionRow
            label="Delete uncaptioned clip variants"
            description="When a captioned version exists, the original raw clip is rarely watched."
            action={deleteUncaptionedClipsAction}
            severity="safe"
          />
          <ActionRow
            label="Delete orphan files"
            description="Files on disk that no DB row references. Usually leftovers from aborted jobs."
            action={deleteOrphanFilesAction}
            severity="safe"
          />
          <ActionRow
            label="Delete source videos for ready projects"
            description="Big files. Frees the most space."
            hint="After this, re-rendering highlights with different settings is no longer possible — but existing clips stay."
            action={deleteReadySourcesAction}
            severity="caution"
            confirm="Delete source videos for every ready project? Clips remain; re-rendering will not be possible."
          />
          <ActionRow
            label="Wipe Next.js dev cache"
            description="Removes apps/web/.next/dev/cache. Forces Turbopack to rebuild on next reload."
            action={wipeNextCacheAction}
            severity="caution"
          />
          <ActionRow
            label="Delete failed projects + their media"
            description="Removes every project in the 'failed' status entirely (DB rows + disk)."
            action={deleteFailedProjectMediaAction}
            severity="danger"
            confirm="Delete ALL failed projects and their media? Cascade-deletes jobs, transcripts, highlights, clips, scheduled uploads."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Database cleanup</CardTitle>
          <CardDescription>
            Trim rows that the worker no longer needs and compact the
            SQLite file so deleted pages return to the OS.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <ActionRow
            label="Prune finished jobs older than 24h"
            description="Deletes succeeded/failed/cancelled job rows. Keeps the jobs table small."
            action={() => pruneFinishedJobsAction(24)}
            severity="safe"
          />
          <ActionRow
            label="Prune finished jobs older than 1h"
            description="More aggressive — only keeps the last hour of history."
            action={() => pruneFinishedJobsAction(1)}
            severity="caution"
          />
          <ActionRow
            label="Delete cancelled scheduled uploads"
            description="Removes scheduled_uploads rows whose status is 'cancelled'."
            action={pruneCancelledUploadsAction}
            severity="safe"
          />
          <ActionRow
            label="Compact SQLite (VACUUM)"
            description="Returns deleted-page space to the OS. Run after the actions above."
            action={vacuumDatabaseAction}
            severity="safe"
          />
          <NuclearRow />
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * "Wipe everything" with type-DELETE confirmation. Lives outside ActionRow
 * because the confirm flow needs a real input field.
 */
function NuclearRow() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [pending, startTransition] = useTransition();

  const run = () => {
    startTransition(async () => {
      try {
        const res = await wipeAllMediaAction(confirmText);
        (res.ok ? toast.success : toast.error)(res.message);
        if (res.ok) setOpen(false);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        router.refresh();
      }
    });
  };

  return (
    <div className="flex items-start justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">Nuke everything</p>
          <SeverityPill tag="danger" />
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Deletes every file in <code>data/videos/</code>, drops every clip
          row, and marks all in-flight projects as failed.
        </p>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <Button
          size="sm"
          variant="destructive"
          onClick={() => setOpen(true)}
          className="shrink-0"
        >
          Nuke…
        </Button>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Nuke all media?</DialogTitle>
            <DialogDescription>
              This deletes every file under <code>data/videos/</code> and
              drops every clip row. Projects are kept (so links don&apos;t
              404) but flipped to <code>failed</code>. Type{" "}
              <code className="rounded bg-muted px-1">DELETE</code> to
              confirm.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="DELETE"
            autoFocus
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setOpen(false);
                setConfirmText("");
              }}
              disabled={pending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={run}
              disabled={pending || confirmText !== "DELETE"}
            >
              {pending && <Loader2 className="size-3.5 animate-spin" />}
              Wipe everything
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
