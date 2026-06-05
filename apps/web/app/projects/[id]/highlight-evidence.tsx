"use client";

import type { HighlightReason } from "@/lib/db/schema";

export function HighlightEvidenceDetails({
  reason,
}: {
  reason: HighlightReason;
}) {
  const scores = reason.scores;
  const tl = reason.twelvelabs;
  const penalties = reason.penalties;

  const rows: Array<{ label: string; value: string }> = [];
  if (reason.seedSource) rows.push({ label: "Seed source", value: reason.seedSource });
  if (reason.momentType) rows.push({ label: "Moment type", value: reason.momentType });
  if (reason.confidence != null)
    rows.push({ label: "Confidence", value: `${(reason.confidence * 100).toFixed(0)}%` });
  if (reason.visualScore != null)
    rows.push({ label: "Visual score", value: `${(reason.visualScore * 100).toFixed(0)}` });
  if (reason.fusionScore != null)
    rows.push({ label: "Fusion score", value: `${(reason.fusionScore * 100).toFixed(0)}` });
  if (scores?.providerAgreement != null)
    rows.push({
      label: "Provider agreement",
      value: `${(scores.providerAgreement * 100).toFixed(0)}`,
    });
  if (tl?.segmentType)
    rows.push({ label: "TwelveLabs segment", value: tl.segmentType });
  if (tl?.visualReason)
    rows.push({ label: "Visual reason", value: tl.visualReason });
  if (penalties?.commentaryHeavy)
    rows.push({
      label: "Commentary penalty",
      value: penalties.commentaryHeavy.toFixed(2),
    });
  if (penalties?.offCenterPeak)
    rows.push({
      label: "Off-center peak penalty",
      value: penalties.offCenterPeak.toFixed(2),
    });

  if (rows.length === 0) return null;

  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        Signal evidence
      </summary>
      <dl className="mt-2 space-y-1 rounded-md bg-muted/40 p-2">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[9rem_1fr] gap-2">
            <dt className="text-muted-foreground">{r.label}</dt>
            <dd className="text-foreground/90">{r.value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
