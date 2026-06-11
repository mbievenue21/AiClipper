"use client";

import { useActionState, useState } from "react";
import Link from "next/link";
import { ChevronDown, Loader2 } from "lucide-react";

import { createProject, type CreateProjectState } from "./actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DEFAULT_PROJECT_SETTINGS, type HighlightProfile } from "@/lib/db/schema";

const initial: CreateProjectState = {};

export function CreateProjectForm({
  defaultPreRollSeconds = DEFAULT_PROJECT_SETTINGS.preRollSeconds,
  defaultTailPaddingSeconds = DEFAULT_PROJECT_SETTINGS.tailPaddingSeconds,
  feedbackCount = 0,
  profiles = [],
}: {
  defaultPreRollSeconds?: number;
  defaultTailPaddingSeconds?: number;
  feedbackCount?: number;
  profiles?: HighlightProfile[];
}) {
  const [state, action, pending] = useActionState(createProject, initial);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  return (
    <Card className="mx-auto max-w-lg">
      <CardHeader>
        <CardTitle>New project</CardTitle>
        <CardDescription>
          Paste a public YouTube or Twitch VOD URL. The worker will download it
          with yt-dlp into <code className="text-xs">data/videos/</code>,
          transcribe it, and pick {DEFAULT_PROJECT_SETTINGS.topN} highlight clips
          by default.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form action={action} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="sourceUrl" className="text-sm font-medium">
              Source URL
            </label>
            <Input
              id="sourceUrl"
              name="sourceUrl"
              type="url"
              placeholder="https://www.youtube.com/watch?v=..."
              required
              disabled={pending}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="name" className="text-sm font-medium">
              Name <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <Input
              id="name"
              name="name"
              placeholder="Auto-generated from URL if empty"
              disabled={pending}
            />
          </div>

          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="flex w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm font-medium hover:bg-accent"
            aria-expanded={advancedOpen}
          >
            <span>Advanced — clip settings</span>
            <ChevronDown
              className={`size-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`}
            />
          </button>

          {advancedOpen && (
            <div className="space-y-3 rounded-md border border-dashed border-border p-3">
              <div className="space-y-2">
                <label htmlFor="topN" className="text-sm font-medium">
                  Number of highlight clips
                </label>
                <Input
                  id="topN"
                  name="topN"
                  type="number"
                  min={1}
                  max={20}
                  defaultValue={DEFAULT_PROJECT_SETTINGS.topN}
                  disabled={pending}
                />
                <p className="text-xs text-muted-foreground">
                  How many candidate clips to extract from the video (1–20).
                </p>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-2">
                  <label htmlFor="minClipSeconds" className="text-sm font-medium">
                    Min length (s)
                  </label>
                  <Input
                    id="minClipSeconds"
                    name="minClipSeconds"
                    type="number"
                    min={5}
                    max={120}
                    defaultValue={DEFAULT_PROJECT_SETTINGS.minClipSeconds}
                    disabled={pending}
                  />
                </div>
                <div className="space-y-2">
                  <label htmlFor="maxClipSeconds" className="text-sm font-medium">
                    Max length (s)
                  </label>
                  <Input
                    id="maxClipSeconds"
                    name="maxClipSeconds"
                    type="number"
                    min={10}
                    max={180}
                    defaultValue={DEFAULT_PROJECT_SETTINGS.maxClipSeconds}
                    disabled={pending}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label htmlFor="aspect" className="text-sm font-medium">
                  Output aspect
                </label>
                <select
                  id="aspect"
                  name="aspect"
                  defaultValue={DEFAULT_PROJECT_SETTINGS.aspect}
                  disabled={pending}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="9:16">9:16 — vertical (Reels / Shorts)</option>
                  <option value="16:9">16:9 — horizontal</option>
                  <option value="1:1">1:1 — square</option>
                </select>
              </div>

              <div className="space-y-2">
                <label htmlFor="vibe" className="text-sm font-medium">
                  What to look for{" "}
                  <span className="font-normal text-muted-foreground">(optional)</span>
                </label>
                <Input
                  id="vibe"
                  name="vibe"
                  placeholder="e.g. funny hype reactions from Tarik, insane Valorant clutches"
                  disabled={pending}
                />
                <p className="text-xs text-muted-foreground">
                  Creator brief for Gemini — names, vibes, game moments. You can
                  change this later via Re-analyze.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-2">
                  <label htmlFor="preRollSeconds" className="text-sm font-medium">
                    Buildup pre-roll (s)
                  </label>
                  <Input
                    id="preRollSeconds"
                    name="preRollSeconds"
                    type="number"
                    min={0}
                    max={20}
                    defaultValue={defaultPreRollSeconds}
                    disabled={pending}
                  />
                  <p className="text-xs text-muted-foreground">
                    Seconds of context before the climax. 5–15s helps game
                    clips & punchlines land. 0 disables.
                    {feedbackCount > 0 && (
                      <>
                        {" "}
                        Learned from {feedbackCount} clip edit
                        {feedbackCount === 1 ? "" : "s"}.
                      </>
                    )}
                  </p>
                </div>
                <div className="space-y-2">
                  <label htmlFor="tailPaddingSeconds" className="text-sm font-medium">
                    Reaction tail (s)
                  </label>
                  <Input
                    id="tailPaddingSeconds"
                    name="tailPaddingSeconds"
                    type="number"
                    min={0}
                    max={10}
                    defaultValue={defaultTailPaddingSeconds}
                    disabled={pending}
                  />
                  <p className="text-xs text-muted-foreground">
                    Seconds after the climax for the reaction.
                  </p>
                </div>
              </div>

              {profiles.length > 0 && (
                <div className="space-y-2">
                  <label htmlFor="highlightProfileId" className="text-sm font-medium">
                    Highlight profile
                  </label>
                  <select
                    id="highlightProfileId"
                    name="highlightProfileId"
                    defaultValue={
                      profiles.find((p) => p.slug === "valorant_reaction_shorts")
                        ?.id ?? profiles[0]?.id
                    }
                    disabled={pending}
                    className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {profiles.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-muted-foreground">
                    Profile config drives candidate scoring — weights, keywords,
                    and timing rules.
                  </p>
                </div>
              )}

              <div className="space-y-2">
                <label htmlFor="analyzeModel" className="text-sm font-medium">
                  Analysis model
                </label>
                <select
                  id="analyzeModel"
                  name="analyzeModel"
                  defaultValue={DEFAULT_PROJECT_SETTINGS.analyzeModel}
                  disabled={pending}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="flash">
                    Flash — Gemini 3.5 Flash (frontier, fast + cheap)
                  </option>
                  <option value="pro">
                    Pro — Gemini 3.1 Pro (deepest reasoning)
                  </option>
                </select>
                <p className="text-xs text-muted-foreground">
                  Both shift clip start/end to capture buildup & reaction. Pro
                  reasons deeper on tricky boundaries; Flash is faster + cheaper.
                </p>
              </div>
            </div>
          )}

          {state.error && (
            <p className="text-sm text-destructive" role="alert">
              {state.error}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={pending}>
              {pending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Starting download…
                </>
              ) : (
                "Download & create"
              )}
            </Button>
            <Button variant="outline" asChild disabled={pending}>
              <Link href="/">Cancel</Link>
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
