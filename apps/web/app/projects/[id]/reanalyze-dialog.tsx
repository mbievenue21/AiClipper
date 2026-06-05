"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Sparkles, Zap } from "lucide-react";
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
import { cn } from "@/lib/utils";

import { reanalyzeProjectAction } from "./actions";

/**
 * Manual "Re-analyze" trigger with a per-run model picker.
 *
 * The motivation is testing: the user can run Pro on a clip, see the
 * boundary adjustments + titles, then re-run with Flash on the same source
 * to compare quality vs. cost. Whichever model gets picked here is sent as
 * a per-job override — the project's saved `analyzeModel` setting stays
 * untouched unless the "Save as default" toggle is on.
 *
 * Highlights that have already been rendered to clips are preserved (FK
 * cascade safety) — only the unrendered candidate highlights are wiped.
 */
type ModelChoice = "" | "pro" | "flash";

export function ReanalyzeDialog({
  projectId,
  savedModel,
  savedVibe = "",
  disabled,
  disabledReason,
}: {
  projectId: string;
  savedModel: "pro" | "flash";
  savedVibe?: string;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const [choice, setChoice] = useState<ModelChoice>("");
  const [persist, setPersist] = useState(false);
  const [vibe, setVibe] = useState(savedVibe);
  const [persistVibe, setPersistVibe] = useState(false);

  const onSubmit = () => {
    startTransition(async () => {
      const res = await reanalyzeProjectAction({
        projectId,
        modelOverride: choice || undefined,
        vibeOverride: vibe.trim() || undefined,
        persistAsDefault: persist,
        persistVibeAsDefault: persistVibe,
      });
      if (res.ok) {
        toast.success(res.message);
        setOpen(false);
        setChoice("");
        setPersist(false);
        setPersistVibe(false);
        router.refresh();
      } else {
        toast.error(res.message);
      }
    });
  };

  const effectiveChoice = choice || savedModel;

  useEffect(() => {
    if (open) setVibe(savedVibe);
  }, [open, savedVibe]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="sm" variant="outline" disabled={disabled}>
            <Sparkles className="size-3.5" />
            Re-analyze
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Re-analyze highlights</DialogTitle>
          <DialogDescription>
            Re-runs highlight detection on the existing transcript. Tell Gemini
            what to look for, pick Pro or Flash, then compare results.
            Already-rendered clips stay; un-rendered candidates get replaced.
          </DialogDescription>
        </DialogHeader>

        {disabled ? (
          <div className="rounded-md border border-dashed p-3 text-sm">
            <p className="font-medium">Can't re-analyze right now.</p>
            <p className="mt-1 text-muted-foreground">
              {disabledReason ?? "Wait for the current pipeline step to finish."}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label
                htmlFor="reanalyze-vibe"
                className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                What to look for
              </label>
              <textarea
                id="reanalyze-vibe"
                value={vibe}
                onChange={(e) => setVibe(e.target.value)}
                rows={3}
                maxLength={500}
                placeholder='e.g. Funny, hype, crazy reaction moments from Sliggy or Tarik, insane Valorant clutches'
                className="flex min-h-[72px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              />
              <p className="text-[11px] text-muted-foreground">
                Creator brief for Gemini — names, vibes, game moments.{" "}
                {vibe.length}/500
              </p>
            </div>

            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Model for this run
            </p>
            <div className="grid grid-cols-2 gap-2">
              <ModelCard
                id="pro"
                title="Pro"
                subtitle="Gemini 3.1 Pro"
                tagline="Deepest reasoning"
                icon={Sparkles}
                accent="violet"
                selected={effectiveChoice === "pro"}
                isSaved={savedModel === "pro" && choice === ""}
                onClick={() => setChoice("pro")}
              />
              <ModelCard
                id="flash"
                title="Flash"
                subtitle="Gemini 3.5 Flash"
                tagline="Frontier, fast + cheap"
                icon={Zap}
                accent="amber"
                selected={effectiveChoice === "flash"}
                isSaved={savedModel === "flash" && choice === ""}
                onClick={() => setChoice("flash")}
              />
            </div>

            {(choice && choice !== savedModel) ||
            (vibe.trim() && vibe.trim() !== savedVibe.trim()) ? (
              <div className="space-y-2 rounded-md border bg-muted/30 p-2 text-xs">
                {choice && choice !== savedModel && (
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={persist}
                      onChange={(e) => setPersist(e.target.checked)}
                      className="mt-0.5 size-3.5 accent-foreground"
                    />
                    <span>
                      Save model{" "}
                      <code className="rounded bg-background px-1 py-0.5">
                        {choice}
                      </code>{" "}
                      as default
                    </span>
                  </label>
                )}
                {vibe.trim() && vibe.trim() !== savedVibe.trim() && (
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={persistVibe}
                      onChange={(e) => setPersistVibe(e.target.checked)}
                      className="mt-0.5 size-3.5 accent-foreground"
                    />
                    <span>Save this brief as the project's default</span>
                  </label>
                )}
              </div>
            ) : null}

            {choice === "" && (
              <p className="text-[11px] text-muted-foreground">
                No selection — will use the saved default (
                <code className="rounded bg-muted px-1">{savedModel}</code>).
              </p>
            )}

            <div className="rounded-md border bg-muted/30 p-2 text-[11px] text-muted-foreground">
              <p>Re-analysis takes ~5–30 s depending on transcript length.</p>
              <p className="mt-1">
                Already-rendered clips are kept; un-rendered candidate
                highlights are replaced with fresh picks.
              </p>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={pending || disabled}>
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {pending ? "Queuing…" : `Run with ${effectiveChoice.split("-").pop()}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModelCard({
  title,
  subtitle,
  tagline,
  icon: Icon,
  accent,
  selected,
  isSaved,
  onClick,
}: {
  id: string;
  title: string;
  subtitle: string;
  tagline: string;
  icon: typeof Sparkles;
  accent: "violet" | "amber";
  selected: boolean;
  isSaved: boolean;
  onClick: () => void;
}) {
  const accentClass = {
    violet:
      "border-violet-400/50 bg-gradient-to-br from-violet-500/10 to-transparent text-violet-700 dark:text-violet-300",
    amber:
      "border-amber-400/50 bg-gradient-to-br from-amber-500/10 to-transparent text-amber-700 dark:text-amber-300",
  }[accent];
  const accentIcon = {
    violet: "text-violet-500",
    amber: "text-amber-500",
  }[accent];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "relative rounded-md border p-3 text-left transition-colors",
        selected ? accentClass : "hover:bg-accent/50",
      )}
    >
      <div className="flex items-start gap-2">
        <Icon
          className={cn("mt-0.5 size-4 shrink-0", selected ? accentIcon : "")}
        />
        <div className="min-w-0">
          <p className="text-sm font-semibold">{title}</p>
          <p className="text-[11px] leading-tight text-muted-foreground">
            {subtitle}
          </p>
          <p className="mt-1 text-[10px] text-muted-foreground">{tagline}</p>
        </div>
      </div>
      {isSaved && (
        <span className="absolute top-1 right-1 rounded-full bg-foreground/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wide">
          Saved
        </span>
      )}
    </button>
  );
}
