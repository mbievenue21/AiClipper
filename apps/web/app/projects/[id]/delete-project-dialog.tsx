"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trash2 } from "lucide-react";
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

import { deleteProjectPermanentlyAction } from "./actions";

export function DeleteProjectDialog({
  projectId,
  projectName,
  variant = "outline",
  compact = false,
}: {
  projectId: string;
  projectName: string;
  variant?: "outline" | "destructive" | "ghost";
  compact?: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [pending, startTransition] = useTransition();

  const canDelete = confirm.trim().toUpperCase() === "DELETE";

  const onDelete = () => {
    if (!canDelete) return;
    startTransition(async () => {
      const res = await deleteProjectPermanentlyAction({
        projectId,
        confirm: confirm.trim(),
      });
      if (res.ok) {
        toast.success(res.message);
        setOpen(false);
        setConfirm("");
        router.push("/");
        router.refresh();
      } else {
        toast.error(res.message);
      }
    });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            size="sm"
            variant={variant}
            title="Delete project permanently"
            aria-label={`Delete project ${projectName}`}
          >
            <Trash2 className="size-3.5" />
            {!compact && " Delete project"}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete project permanently?</DialogTitle>
          <DialogDescription>
            This removes <strong>{projectName}</strong> and everything tied to
            it: jobs (including stuck analysis), transcripts, highlights, clips,
            TwelveLabs index data, and all files under{" "}
            <code className="rounded bg-muted px-1">data/videos/{projectId}/</code>.
            This cannot be undone.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <label
            htmlFor="delete-confirm"
            className="text-xs font-medium text-muted-foreground"
          >
            Type <span className="font-mono text-foreground">DELETE</span> to
            confirm
          </label>
          <input
            id="delete-confirm"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="DELETE"
            autoComplete="off"
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={onDelete}
            disabled={pending || !canDelete}
          >
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {pending ? "Deleting…" : "Delete permanently"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
