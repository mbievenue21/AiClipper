import { Badge } from "@/components/ui/badge";

export type AnalysisBadgeState = {
  twelvelabs: "disabled" | "skipped" | "used" | "failed_open" | "running";
  geminiRerank: boolean;
  geminiMultimodal: boolean;
  localSignals: boolean;
};

export function AnalysisBadges({ state }: { state: AnalysisBadgeState }) {
  const tlLabel = {
    disabled: "TwelveLabs: disabled",
    skipped: "TwelveLabs: skipped",
    used: "TwelveLabs video analysis",
    failed_open: "TwelveLabs: failed, local used",
    running: "TwelveLabs: running",
  }[state.twelvelabs];

  const tlVariant =
    state.twelvelabs === "used"
      ? "default"
      : state.twelvelabs === "failed_open"
        ? "outline"
        : "secondary";

  return (
    <div className="flex flex-wrap gap-1.5">
      {state.localSignals && (
        <Badge variant="secondary">Local signals</Badge>
      )}
      {state.geminiRerank && (
        <Badge variant="secondary">Gemini rerank</Badge>
      )}
      <Badge variant={tlVariant}>{tlLabel}</Badge>
      {state.geminiMultimodal && (
        <Badge variant="outline">Gemini multimodal refine</Badge>
      )}
    </div>
  );
}
