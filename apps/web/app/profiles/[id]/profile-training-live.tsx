"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ProfileMetrics } from "@/lib/db/schema";

type TrainingStatus = {
  isTraining: boolean;
  activeJobs: Array<{
    id: string;
    type: string;
    status: string;
    progress: number;
    progressMessage: string | null;
  }>;
  activeRun: {
    id: string;
    status: string;
    optimizer: string;
    metricsJson: ProfileMetrics | null;
  } | null;
  clipCount: number;
  exampleCount: number;
  positiveExamples: number;
  negativeExamples: number;
};

const JOB_LABELS: Record<string, string> = {
  reference_clip_import: "Importing reference clip",
  reference_feature_extract: "Extracting clip features",
  profile_train: "Optimizing profile (Optuna)",
  profile_evaluate: "Evaluating new version",
  profile_retrain_from_feedback: "Preparing feedback retrain",
  enrich_training_feedback: "Gemini enriching editor notes",
};

export function ProfileTrainingLive({ profileId }: { profileId: string }) {
  const [status, setStatus] = useState<TrainingStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const res = await fetch(`/api/profiles/${profileId}/training-status`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as TrainingStatus;
        if (!cancelled) setStatus(data);
      } catch {
        // ignore polling errors
      }
    };

    load();
    const id = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [profileId]);

  if (!status) return null;

  return (
    <div className="rounded-lg border border-dashed p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Live training status</span>
        {status.isTraining ? (
          <Badge variant="default" className="gap-1">
            <Loader2 className="size-3 animate-spin" />
            In progress
          </Badge>
        ) : (
          <Badge variant="secondary">Idle</Badge>
        )}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-4 text-sm">
        <Stat label="Reference clips" value={String(status.clipCount)} />
        <Stat label="Training examples" value={String(status.exampleCount)} />
        <Stat label="Positive labels" value={String(status.positiveExamples)} />
        <Stat label="Negative labels" value={String(status.negativeExamples)} />
      </div>

      {status.activeJobs.length > 0 && (
        <div className="mt-4 space-y-2">
          {status.activeJobs.map((job) => (
            <div
              key={job.id}
              className="rounded-md bg-muted/50 px-3 py-2 text-xs"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">
                  {JOB_LABELS[job.type] ?? job.type}
                </span>
                <span className="text-muted-foreground">
                  {(job.progress * 100).toFixed(0)}%
                </span>
              </div>
              {job.progressMessage && (
                <p className="mt-1 text-muted-foreground">{job.progressMessage}</p>
              )}
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${Math.min(100, job.progress * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {status.activeRun && (
        <p className="mt-3 text-xs text-muted-foreground">
          Run {status.activeRun.id.slice(0, 8)}… · {status.activeRun.optimizer} ·{" "}
          {status.activeRun.status}
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}
