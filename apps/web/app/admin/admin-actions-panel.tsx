"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  cancelPendingAction,
  deleteFailedProjectsAction,
  healWorkerAction,
  pruneFinishedJobsAction,
  type AdminActionResult,
} from "./actions";

type Action = () => Promise<AdminActionResult>;

function ActionButton({
  label,
  description,
  action,
  variant = "default",
  confirm,
}: {
  label: string;
  description: string;
  action: Action;
  variant?: "default" | "secondary" | "destructive" | "outline";
  confirm?: string;
}) {
  const [pending, startTransition] = useTransition();
  const router = useRouter();

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
    <div className="flex items-start justify-between gap-4 rounded-md border p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Button
        size="sm"
        variant={variant}
        onClick={run}
        disabled={pending}
        className="shrink-0"
      >
        {pending && <Loader2 className="size-3.5 animate-spin" />}
        {pending ? "Running..." : "Run"}
      </Button>
    </div>
  );
}

export function AdminActionsPanel() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cleanup actions</CardTitle>
        <CardDescription>
          Database-only cleanup. These do not touch downloaded media files.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <ActionButton
          label="Heal stuck workers"
          description="Reset 'running' jobs back to 'pending' and mark orphan projects as failed. Safe to run any time."
          action={healWorkerAction}
        />
        <ActionButton
          label="Cancel all pending jobs"
          description="Move every pending job to 'cancelled'. Use when you want to stop the queue without restarting."
          variant="secondary"
          action={cancelPendingAction}
          confirm="Cancel every pending job?"
        />
        <ActionButton
          label="Prune finished jobs (> 1 hr old)"
          description="Delete succeeded/failed/cancelled jobs older than an hour to keep the queue table small."
          variant="outline"
          action={() => pruneFinishedJobsAction(60)}
        />
        <ActionButton
          label="Delete all failed projects"
          description="Remove every failed project, cancel its jobs, and delete media on disk."
          variant="destructive"
          action={deleteFailedProjectsAction}
          confirm="Delete EVERY failed project? This permanently removes jobs, transcripts, highlights, clips, and downloaded media."
        />
      </CardContent>
    </Card>
  );
}
