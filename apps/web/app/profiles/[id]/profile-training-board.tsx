"use client";

import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type {
  HighlightProfileVersion,
  ProfileConfig,
  ProfileMetrics,
  ProfileTrainingRun,
} from "@/lib/db/schema";
import { cn } from "@/lib/utils";

import { ProfileTrainingLive } from "./profile-training-live";

const WEIGHT_LABELS: Record<string, string> = {
  audioPeak: "Audio peak",
  keyword: "Transcript keywords",
  semanticPhrase: "Semantic phrases",
  chatBurst: "Chat burst",
  scene: "Scene / motion",
  ocr: "OCR (game HUD)",
};

export function ProfileTrainingBoard({
  profileId,
  versions,
  runs,
}: {
  profileId: string;
  versions: HighlightProfileVersion[];
  runs: ProfileTrainingRun[];
}) {
  const defaultVersion =
    versions.find((v) => v.isActive) ?? versions[0] ?? null;
  const selected = defaultVersion;
  const config = selected?.configJson ?? null;
  const metrics = selected?.metricsJson ?? null;

  const previousRun = useMemo(() => runs[1] ?? null, [runs]);
  const previousConfig = previousRun?.resultConfigJson ?? null;

  return (
    <div id="training-board" className="scroll-mt-24 space-y-6">
      <ProfileTrainingLive profileId={profileId} />

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">Training display board</CardTitle>
              <CardDescription>
                Live score weights, keyword matrix, and metrics. Each training
                run updates this same config (revision bumps automatically).
              </CardDescription>
            </div>
            {selected && (
              <Badge variant="secondary">
                Revision{" "}
                {metrics?.trainingRevision ?? selected.versionNumber}
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {!config ? (
            <p className="text-sm text-muted-foreground">
              No trained version yet. Submit reference clips from{" "}
              <a href="/train" className="underline">
                Train
              </a>{" "}
              to generate the first config.
            </p>
          ) : (
            <>
              <MetricsPanel metrics={metrics} />
              <WeightMatrix
                weights={config.scoreWeights}
                previous={previousConfig?.scoreWeights}
              />
              <KeywordMatrix
                keywords={config.keywords}
                previous={previousConfig?.keywords}
              />
              {config.antiKeywords &&
                Object.keys(config.antiKeywords).length > 0 && (
                  <KeywordMatrix
                    title="Anti-keyword matrix"
                    description="Terms that down-rank windows (from negative feedback)."
                    keywords={config.antiKeywords}
                    previous={previousConfig?.antiKeywords}
                    variant="anti"
                  />
                )}
              <PhraseList phrases={config.phrases} />
              <div className="grid gap-4 lg:grid-cols-2">
                <KeyValueTable title="Thresholds" values={config.thresholds} />
                <KeyValueTable title="Penalties" values={config.penalties} />
              </div>
              <CandidateSources sources={config.candidateSources} />
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Training run history</CardTitle>
          <CardDescription>
            Optuna trials and objectives from each optimization run
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No runs recorded yet.</p>
          ) : (
            runs.map((run) => (
              <RunRow key={run.id} run={run} />
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function MetricsPanel({ metrics }: { metrics: ProfileMetrics | null }) {
  if (!metrics) {
    return (
      <p className="text-sm text-muted-foreground">
        Metrics appear after a training run completes.
      </p>
    );
  }

  const items = [
    {
      label: "Recall @ K",
      value: `${((metrics.recallAtK ?? 0) * 100).toFixed(1)}%`,
      hint: "Share of positive examples scoring above threshold in top-K",
    },
    {
      label: "Precision @ K",
      value: `${((metrics.precisionAtK ?? 0) * 100).toFixed(1)}%`,
      hint: "Share of top-K scores that are true positives",
    },
    {
      label: "Separation",
      value: (metrics.separation ?? 0).toFixed(3),
      hint: "Mean positive score minus mean negative score",
    },
    {
      label: "Mean positive",
      value: (metrics.meanPositiveScore ?? 0).toFixed(3),
    },
    {
      label: "Mean negative",
      value: (metrics.meanNegativeScore ?? 0).toFixed(3),
    },
    {
      label: "Optuna trials",
      value: String(metrics.trialCount ?? "—"),
    },
    {
      label: "Best objective",
      value:
        metrics.bestObjective != null
          ? metrics.bestObjective.toFixed(4)
          : "—",
    },
  ];

  return (
    <div>
      <h3 className="mb-3 text-sm font-medium">Evaluation metrics</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((item) => (
          <div key={item.label} className="rounded-md border p-3" title={item.hint}>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {item.label}
            </p>
            <p className="mt-1 text-xl font-semibold tabular-nums">{item.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function WeightMatrix({
  weights,
  previous,
}: {
  weights: ProfileConfig["scoreWeights"];
  previous?: ProfileConfig["scoreWeights"];
}) {
  const entries = Object.entries(weights).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, v]) => v), 0.01);

  return (
    <div>
      <h3 className="mb-3 text-sm font-medium">Signal weight matrix</h3>
      <p className="mb-3 text-xs text-muted-foreground">
        How much each signal contributes to the final profile score (sums to ~1.0).
        Optuna tunes these during training.
      </p>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[480px] text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">Signal</th>
              <th className="px-3 py-2 font-medium">Weight</th>
              <th className="px-3 py-2 font-medium">Share</th>
              <th className="px-3 py-2 font-medium">Δ vs prev</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, value]) => {
              const prev = previous?.[key as keyof typeof weights];
              const delta =
                prev != null ? value - prev : null;
              return (
                <tr key={key} className="border-b border-border/50">
                  <td className="px-3 py-2">{WEIGHT_LABELS[key] ?? key}</td>
                  <td className="px-3 py-2 tabular-nums">{value.toFixed(3)}</td>
                  <td className="px-3 py-2">
                    <Bar value={value} max={max} />
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2 tabular-nums text-xs",
                      delta != null && delta > 0.01 && "text-emerald-600",
                      delta != null && delta < -0.01 && "text-amber-600",
                    )}
                  >
                    {delta != null
                      ? `${delta >= 0 ? "+" : ""}${delta.toFixed(3)}`
                      : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function KeywordMatrix({
  keywords,
  previous,
  title = "Keyword matrix",
  description = "Transcript terms and their importance weights when scoring highlight windows.",
  variant = "positive",
}: {
  keywords: Record<string, number>;
  previous?: Record<string, number>;
  title?: string;
  description?: string;
  variant?: "positive" | "anti";
}) {
  const entries = Object.entries(keywords).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, v]) => v), 0.01);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No keywords configured.</p>
    );
  }

  return (
    <div>
      <h3 className="mb-3 text-sm font-medium">{title}</h3>
      <p className="mb-3 text-xs text-muted-foreground">{description}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {entries.map(([word, weight]) => {
          const prev = previous?.[word];
          const intensity = weight / max;
          return (
            <div
              key={word}
              className="rounded-md border px-3 py-2"
              style={{
                backgroundColor:
                  variant === "anti"
                    ? `color-mix(in oklab, var(--destructive) ${Math.round(intensity * 16)}%, transparent)`
                    : `color-mix(in oklab, var(--primary) ${Math.round(intensity * 18)}%, transparent)`,
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{word}</span>
                <span className="tabular-nums text-sm">{weight.toFixed(2)}</span>
              </div>
              <Bar value={weight} max={max} className="mt-2" />
              {prev != null && prev !== weight && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  was {prev.toFixed(2)}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PhraseList({ phrases }: { phrases: string[] }) {
  if (phrases.length === 0) return null;
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium">Semantic phrases</h3>
      <div className="flex flex-wrap gap-1.5">
        {phrases.map((p) => (
          <Badge key={p} variant="secondary">
            {p}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function KeyValueTable({
  title,
  values,
}: {
  title: string;
  values: Record<string, number>;
}) {
  return (
    <div className="rounded-md border p-3">
      <h3 className="mb-2 text-sm font-medium">{title}</h3>
      <dl className="space-y-1 text-sm">
        {Object.entries(values).map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="tabular-nums font-medium">{v.toFixed(3)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CandidateSources({
  sources,
}: {
  sources: ProfileConfig["candidateSources"];
}) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium">Candidate sources</h3>
      <div className="flex flex-wrap gap-2">
        {Object.entries(sources).map(([key, enabled]) => (
          <Badge key={key} variant={enabled ? "default" : "outline"}>
            {key.replace(/([A-Z])/g, " $1").trim()}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function Bar({
  value,
  max,
  className,
}: {
  value: number;
  max: number;
  className?: string;
}) {
  return (
    <div className={cn("h-2 overflow-hidden rounded-full bg-muted", className)}>
      <div
        className="h-full rounded-full bg-primary/80"
        style={{ width: `${(value / max) * 100}%` }}
      />
    </div>
  );
}

function RunRow({ run }: { run: ProfileTrainingRun }) {
  const m = run.metricsJson;
  return (
    <details className="rounded-md border p-3 text-sm">
      <summary className="cursor-pointer font-medium capitalize">
        {run.status} · {run.optimizer}
        {m?.trialCount != null && (
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            {m.trialCount} trials · objective{" "}
            {m.bestObjective?.toFixed(3) ?? "—"}
          </span>
        )}
      </summary>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 text-xs">
        {m && (
          <>
            <MetricChip label="recall@K" value={`${((m.recallAtK ?? 0) * 100).toFixed(1)}%`} />
            <MetricChip label="separation" value={(m.separation ?? 0).toFixed(3)} />
            <MetricChip
              label="mean pos"
              value={(m.meanPositiveScore ?? 0).toFixed(3)}
            />
            <MetricChip
              label="mean neg"
              value={(m.meanNegativeScore ?? 0).toFixed(3)}
            />
          </>
        )}
      </div>
      {run.resultConfigJson && (
        <pre className="mt-3 max-h-48 overflow-auto rounded bg-muted p-2 text-[10px]">
          {JSON.stringify(run.resultConfigJson.scoreWeights ?? run.resultConfigJson, null, 2)}
        </pre>
      )}
    </details>
  );
}

function MetricChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-muted/60 px-2 py-1">
      <span className="text-muted-foreground">{label}: </span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}
