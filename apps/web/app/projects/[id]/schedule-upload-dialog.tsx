"use client";

import {
  useEffect,
  useMemo,
  useState,
  useTransition,
  type ChangeEvent,
} from "react";
import { useRouter } from "next/navigation";
import {
  CalendarClock,
  CheckCircle2,
  Flame,
  Loader2,
  Plus,
  RefreshCw,
  Rocket,
  Sparkles,
  Tag,
  X,
  XCircle,
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
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { GeneratedUploadMetadata } from "@/lib/db/schema";

import {
  generateMetadataAction,
  getTrendingTagSuggestionsAction,
  scheduleUploadAction,
} from "./actions";

type TrendingTagSuggestion = { tag: string; count: number };
type SuggestionSource = "ai" | "trending";

export type AccountOption = {
  id: string;
  platform: "youtube" | "instagram";
  label: string;
};

const TIMEZONE_OPTIONS = [
  { id: "America/Chicago", label: "Central (Chicago) — default" },
  { id: "America/New_York", label: "Eastern (New York)" },
  { id: "America/Denver", label: "Mountain (Denver)" },
  { id: "America/Los_Angeles", label: "Pacific (Los Angeles)" },
  { id: "America/Phoenix", label: "Arizona (Phoenix, no DST)" },
  { id: "America/Anchorage", label: "Alaska" },
  { id: "Pacific/Honolulu", label: "Hawaii" },
  { id: "Etc/UTC", label: "UTC" },
  { id: "Europe/London", label: "London" },
  { id: "Europe/Paris", label: "Paris / Berlin / Madrid" },
  { id: "Asia/Tokyo", label: "Tokyo" },
  { id: "Asia/Singapore", label: "Singapore" },
  { id: "Australia/Sydney", label: "Sydney" },
];

const VISIBILITY_OPTIONS = [
  { id: "private" as const, label: "Private", note: "Only you (recommended)" },
  { id: "unlisted" as const, label: "Unlisted", note: "Anyone with the link" },
  { id: "public" as const, label: "Public", note: "Visible to everyone" },
];

// YouTube's hard limits, used for character counters.
const TITLE_LIMIT = 100;
const TITLE_OPTIMAL = 70; // YouTube truncates ~70 chars on mobile feeds
const DESCRIPTION_LIMIT = 5000;
const DESCRIPTION_OPTIMAL = 600; // anything past this is hidden behind "more"
const TAG_LIMIT = 15;

/**
 * Build epoch-ms for "a wall-clock time YYYY-MM-DD HH:MM in tz <tz>".
 * Works without external libs by formatting the date in the chosen
 * timezone, then back-solving the UTC offset.
 */
function wallclockToEpochMs(
  dateStr: string,
  timeStr: string,
  timezone: string,
): number {
  if (!dateStr || !timeStr) return Number.NaN;
  const [Y, M, D] = dateStr.split("-").map(Number);
  const [hh, mm] = timeStr.split(":").map(Number);
  const guess = Date.UTC(Y, M - 1, D, hh, mm, 0, 0);
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(new Date(guess));
  const lookup: Record<string, string> = {};
  for (const part of fmt) lookup[part.type] = part.value;
  const asUTC = Date.UTC(
    Number(lookup.year),
    Number(lookup.month) - 1,
    Number(lookup.day),
    Number(lookup.hour) % 24,
    Number(lookup.minute),
    Number(lookup.second),
  );
  const offsetMs = asUTC - guess;
  return guess - offsetMs;
}

function defaultDateTime(now: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  const t = new Date(now.getTime() + 60 * 60 * 1000);
  t.setMinutes(Math.ceil(t.getMinutes() / 5) * 5);
  return {
    date: `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`,
    time: `${pad(t.getHours())}:${pad(t.getMinutes())}`,
  };
}

function timeAgo(ms: number): string {
  const diff = Date.now() - ms;
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Counter pill: green under optimal, amber over optimal, red over hard limit. */
function CharCounter({
  value,
  optimal,
  limit,
}: {
  value: number;
  optimal: number;
  limit: number;
}) {
  const color =
    value > limit
      ? "text-destructive"
      : value > optimal
        ? "text-amber-600 dark:text-amber-400"
        : "text-emerald-600 dark:text-emerald-400";
  return (
    <span className={cn("text-[10px] tabular-nums", color)}>
      {value}/{limit}
    </span>
  );
}

export function ScheduleUploadDialog({
  projectId,
  highlightId,
  clipId,
  accounts,
  defaultTitle,
  defaultDescription,
  initialMetadata,
}: {
  projectId: string;
  highlightId: string;
  clipId: string;
  accounts: AccountOption[];
  defaultTitle: string;
  defaultDescription: string;
  initialMetadata: GeneratedUploadMetadata | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const [generating, startGenerating] = useTransition();
  const initial = useMemo(() => defaultDateTime(new Date()), []);

  // Per-platform selection — supports posting to BOTH at once.
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>(() => {
    const seen = new Set<string>();
    const picked: string[] = [];
    for (const a of accounts) {
      if (!seen.has(a.platform)) {
        seen.add(a.platform);
        picked.push(a.id);
      }
    }
    return picked;
  });

  const [when, setWhen] = useState<"now" | "later">("now");
  const [date, setDate] = useState(initial.date);
  const [time, setTime] = useState(initial.time);
  const [timezone, setTimezone] = useState("America/Chicago");
  const [metadata, setMetadata] = useState<GeneratedUploadMetadata | null>(
    initialMetadata,
  );
  const [title, setTitle] = useState(initialMetadata?.title || defaultTitle);
  const [description, setDescription] = useState(
    initialMetadata?.description || defaultDescription,
  );
  const [tags, setTags] = useState<string[]>(initialMetadata?.tags ?? []);
  const [tagDraft, setTagDraft] = useState("");
  const [visibility, setVisibility] =
    useState<"private" | "unlisted" | "public">("private");

  // Trending tags — lazy-loaded on first dialog open + on Regenerate.
  const [trendingRelevant, setTrendingRelevant] = useState<
    TrendingTagSuggestion[]
  >([]);
  const [trendingGeneral, setTrendingGeneral] = useState<
    TrendingTagSuggestion[]
  >([]);
  const [trendingState, setTrendingState] = useState<
    "idle" | "loading" | "loaded" | "error"
  >("idle");
  const [trendingError, setTrendingError] = useState<string | null>(null);

  const fetchTrending = (force = false) => {
    if (trendingState === "loading" && !force) return;
    setTrendingState("loading");
    setTrendingError(null);
    const seed = [
      title,
      ...(metadata?.tags ?? []),
      ...(metadata?.hashtags ?? []),
      ...tags,
    ]
      .join(" ")
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter((w) => w.length >= 3);
    (async () => {
      const res = await getTrendingTagSuggestionsAction({
        seedWords: seed,
      });
      if (res.ok) {
        setTrendingRelevant(res.relevant);
        setTrendingGeneral(res.general);
        setTrendingState("loaded");
      } else {
        setTrendingError(res.message);
        setTrendingState("error");
      }
    })();
  };

  // Fetch once the first time the dialog opens. We hold off on initial load
  // so users who never open the dialog don't burn a quota unit.
  useEffect(() => {
    if (open && trendingState === "idle") fetchTrending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep the visible form in sync if the parent passes us a freshly
  // generated metadata blob (e.g. from another tab via SSE/refresh).
  useEffect(() => {
    if (
      initialMetadata &&
      (metadata?.generatedAt ?? 0) < initialMetadata.generatedAt
    ) {
      setMetadata(initialMetadata);
      setTitle(initialMetadata.title);
      setDescription(initialMetadata.description);
      setTags(initialMetadata.tags);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMetadata?.generatedAt]);

  const accountsByPlatform = useMemo(() => {
    const out: Record<"youtube" | "instagram", AccountOption[]> = {
      youtube: [],
      instagram: [],
    };
    for (const a of accounts) out[a.platform].push(a);
    return out;
  }, [accounts]);

  const toggleAccount = (id: string) => {
    setSelectedAccountIds((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  };

  const addTagFromDraft = () => {
    const raw = tagDraft.trim().replace(/^#+/, "").toLowerCase();
    if (!raw) return;
    const cleaned = raw.replace(/[^a-z0-9 _-]/g, "").trim();
    if (!cleaned) return;
    if (tags.includes(cleaned)) {
      setTagDraft("");
      return;
    }
    if (tags.length >= TAG_LIMIT) {
      toast.error(`Max ${TAG_LIMIT} tags. Remove one first.`);
      return;
    }
    setTags([...tags, cleaned]);
    setTagDraft("");
  };

  const handleTagKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTagFromDraft();
    } else if (e.key === "Backspace" && tagDraft === "" && tags.length > 0) {
      // Quick-erase last tag with backspace on an empty input.
      setTags(tags.slice(0, -1));
    }
  };

  const handleTagPaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text");
    if (!text.includes(",")) return;
    e.preventDefault();
    const parts = text.split(",").map((p) => p.trim()).filter(Boolean);
    const next = [...tags];
    for (const p of parts) {
      const cleaned = p
        .replace(/^#+/, "")
        .toLowerCase()
        .replace(/[^a-z0-9 _-]/g, "")
        .trim();
      if (cleaned && !next.includes(cleaned) && next.length < TAG_LIMIT) {
        next.push(cleaned);
      }
    }
    setTags(next);
    setTagDraft("");
  };

  const removeTag = (t: string) => setTags(tags.filter((x) => x !== t));

  const addTagFromSuggestion = (raw: string) => {
    const cleaned = raw
      .trim()
      .toLowerCase()
      .replace(/^#+/, "")
      .replace(/[^a-z0-9 _-]/g, "")
      .trim();
    if (!cleaned) return;
    if (tags.includes(cleaned)) return; // silent — chip will disappear anyway
    if (tags.length >= TAG_LIMIT) {
      toast.error(`Max ${TAG_LIMIT} tags. Remove one first.`);
      return;
    }
    setTags([...tags, cleaned]);
  };

  // Build the AI-suggestion list: AI tags + hashtags from saved metadata
  // that the user hasn't already added. Dedupe across both sources.
  const aiSuggestions: string[] = useMemo(() => {
    if (!metadata) return [];
    const set = new Set(tags);
    const out: string[] = [];
    for (const t of [...(metadata.tags ?? []), ...(metadata.hashtags ?? [])]) {
      const norm = t.trim().toLowerCase();
      if (!norm || set.has(norm)) continue;
      set.add(norm);
      out.push(norm);
      if (out.length >= 15) break;
    }
    return out;
  }, [metadata, tags]);

  // Trending suggestions filtered to remove anything already in tags or
  // already shown in the AI list (avoid duplicate chips).
  const visibleTrendingRelevant = useMemo(() => {
    const owned = new Set([...tags, ...aiSuggestions]);
    return trendingRelevant.filter((t) => !owned.has(t.tag));
  }, [trendingRelevant, tags, aiSuggestions]);
  const visibleTrendingGeneral = useMemo(() => {
    const owned = new Set([
      ...tags,
      ...aiSuggestions,
      ...visibleTrendingRelevant.map((t) => t.tag),
    ]);
    return trendingGeneral.filter((t) => !owned.has(t.tag));
  }, [trendingGeneral, tags, aiSuggestions, visibleTrendingRelevant]);

  const onGenerate = () => {
    startGenerating(async () => {
      const res = await generateMetadataAction({ highlightId, projectId });
      if (res.ok) {
        toast.success(res.message);
        const m = res.metadata;
        setMetadata(m);
        setTitle(m.title);
        setDescription(m.description);
        setTags(m.tags);
        router.refresh();
      } else {
        toast.error(res.message);
      }
    });
  };

  const onSubmit = () => {
    if (selectedAccountIds.length === 0) {
      toast.error("Pick at least one connected account.");
      return;
    }
    if (!title.trim()) {
      toast.error("Title is required.");
      return;
    }
    if (title.length > TITLE_LIMIT) {
      toast.error(`Title exceeds ${TITLE_LIMIT} characters.`);
      return;
    }
    const scheduledAtMs =
      when === "now" ? Date.now() : wallclockToEpochMs(date, time, timezone);
    if (!Number.isFinite(scheduledAtMs)) {
      toast.error("Pick a valid date and time.");
      return;
    }
    if (when === "later" && scheduledAtMs < Date.now() - 60_000) {
      toast.error("Scheduled time is in the past.");
      return;
    }

    const tagsCsv = tags.join(",");

    startTransition(async () => {
      let success = 0;
      let failure = 0;
      for (const accountId of selectedAccountIds) {
        const account = accounts.find((a) => a.id === accountId);
        if (!account) continue;
        const res = await scheduleUploadAction({
          clipId,
          projectId,
          accountId,
          platform: account.platform,
          title: title.trim(),
          description: description.trim() || undefined,
          tags: tagsCsv || undefined,
          visibility,
          scheduledAtMs,
          timezone,
        });
        if (res.ok) success++;
        else {
          failure++;
          toast.error(`${account.platform}: ${res.message}`);
        }
      }
      if (success > 0) {
        toast.success(
          when === "now"
            ? `Uploading to ${success} destination(s) now.`
            : `Scheduled ${success} upload(s).`,
        );
        setOpen(false);
        router.refresh();
      } else if (failure === 0) {
        toast.error("Nothing was scheduled.");
      }
    });
  };

  const hasAnyAccount = accounts.length > 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="sm" variant="default">
            <Rocket className="size-3.5" />
            Schedule upload
          </Button>
        }
      />
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Schedule upload</DialogTitle>
          <DialogDescription>
            Post the captioned clip now, or queue it for later. Times below are
            interpreted in the timezone you pick.
          </DialogDescription>
        </DialogHeader>

        {!hasAnyAccount ? (
          <div className="rounded-md border border-dashed p-4 text-sm">
            <p className="font-medium">No accounts connected yet.</p>
            <p className="mt-1 text-muted-foreground">
              Go to{" "}
              <a className="underline" href="/accounts">
                /accounts
              </a>{" "}
              to add a YouTube or Instagram account, then come back here.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {/* AI metadata panel — sits up top because it changes everything below */}
            <AiMetadataPanel
              metadata={metadata}
              generating={generating}
              onGenerate={onGenerate}
            />

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Destinations
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {(["youtube", "instagram"] as const).map((platform) => (
                  <div key={platform} className="rounded-md border p-2">
                    <p className="mb-1 flex items-center gap-1 text-xs font-medium uppercase tracking-wide">
                      <Badge variant="outline">{platform}</Badge>
                    </p>
                    {accountsByPlatform[platform].length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">
                        No account connected.{" "}
                        <a className="underline" href="/accounts">
                          Add one
                        </a>
                        .
                      </p>
                    ) : (
                      accountsByPlatform[platform].map((a) => (
                        <label
                          key={a.id}
                          className="flex items-center justify-between gap-2 py-1 text-xs"
                        >
                          <span className="truncate">{a.label}</span>
                          <input
                            type="checkbox"
                            checked={selectedAccountIds.includes(a.id)}
                            onChange={() => toggleAccount(a.id)}
                            className="size-4 accent-foreground"
                          />
                        </label>
                      ))
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                When
              </p>
              <div className="flex gap-2">
                {([
                  { id: "now", label: "Post now", icon: Rocket },
                  { id: "later", label: "Schedule for", icon: CalendarClock },
                ] as const).map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setWhen(opt.id)}
                    className={cn(
                      "flex flex-1 items-center justify-center gap-1.5 rounded-md border p-2 text-sm transition-colors",
                      when === opt.id
                        ? "border-foreground bg-accent"
                        : "border-border hover:bg-accent/50",
                    )}
                  >
                    <opt.icon className="size-3.5" />
                    {opt.label}
                  </button>
                ))}
              </div>
              {when === "later" && (
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    type="date"
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                  />
                  <Input
                    type="time"
                    value={time}
                    step={300}
                    onChange={(e) => setTime(e.target.value)}
                  />
                  <select
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                    className="col-span-2 h-9 rounded-md border bg-background px-2 text-sm"
                  >
                    {TIMEZONE_OPTIONS.map((tz) => (
                      <option key={tz.id} value={tz.id}>
                        {tz.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Post content
                </p>
                {metadata && (
                  <p className="text-[10px] text-muted-foreground">
                    From AI · {timeAgo(metadata.generatedAt)}
                  </p>
                )}
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    Title
                  </label>
                  <CharCounter
                    value={title.length}
                    optimal={TITLE_OPTIMAL}
                    limit={TITLE_LIMIT}
                  />
                </div>
                <Input
                  placeholder="Punchy, under 70 chars"
                  value={title}
                  maxLength={TITLE_LIMIT + 10} // allow brief over-typing, validate on submit
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    Description
                  </label>
                  <CharCounter
                    value={description.length}
                    optimal={DESCRIPTION_OPTIMAL}
                    limit={DESCRIPTION_LIMIT}
                  />
                </div>
                <textarea
                  placeholder="Hook first. Then context. Then a CTA."
                  value={description}
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    setDescription(e.target.value)
                  }
                  maxLength={DESCRIPTION_LIMIT}
                  rows={4}
                  className="w-full rounded-md border bg-background p-2 text-sm leading-relaxed"
                />
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] uppercase tracking-wide text-muted-foreground flex items-center gap-1">
                    <Tag className="size-3" /> Tags
                  </label>
                  <CharCounter
                    value={tags.length}
                    optimal={TAG_LIMIT - 2}
                    limit={TAG_LIMIT}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-1.5 rounded-md border bg-background p-2 text-xs">
                  {tags.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[11px]"
                    >
                      {t}
                      <button
                        type="button"
                        onClick={() => removeTag(t)}
                        className="opacity-60 hover:opacity-100"
                        aria-label={`Remove ${t}`}
                      >
                        <X className="size-2.5" />
                      </button>
                    </span>
                  ))}
                  <input
                    value={tagDraft}
                    onChange={(e) => setTagDraft(e.target.value)}
                    onKeyDown={handleTagKey}
                    onBlur={addTagFromDraft}
                    onPaste={handleTagPaste}
                    placeholder={tags.length === 0 ? "type, press Enter…" : ""}
                    className="min-w-[8ch] flex-1 bg-transparent text-[11px] outline-none placeholder:text-muted-foreground"
                  />
                </div>
              </div>

              <SuggestionChips
                aiTags={aiSuggestions}
                trendingRelevant={visibleTrendingRelevant}
                trendingGeneral={visibleTrendingGeneral}
                trendingState={trendingState}
                trendingError={trendingError}
                onAdd={addTagFromSuggestion}
                onRefreshTrending={() => fetchTrending(true)}
              />

              {metadata?.hashtags && metadata.hashtags.length > 0 && (
                <div className="rounded-md border bg-muted/30 p-2 text-[11px]">
                  <p className="mb-1 text-muted-foreground">
                    Suggested hashtags (auto-appended to IG caption):
                  </p>
                  <p className="font-mono">
                    {metadata.hashtags.map((h) => `#${h}`).join(" ")}
                  </p>
                </div>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Visibility
              </p>
              <div className="grid grid-cols-3 gap-2">
                {VISIBILITY_OPTIONS.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setVisibility(opt.id)}
                    className={cn(
                      "rounded-md border p-2 text-left",
                      visibility === opt.id
                        ? "border-foreground bg-accent"
                        : "border-border hover:bg-accent/50",
                    )}
                  >
                    <p className="text-sm font-medium">{opt.label}</p>
                    <p className="text-[10px] leading-tight text-muted-foreground">
                      {opt.note}
                    </p>
                  </button>
                ))}
              </div>
              {visibility === "public" && (
                <p className="mt-1 flex items-start gap-1 text-[11px] text-amber-600">
                  <XCircle className="size-3 shrink-0 translate-y-0.5" />
                  YouTube uploads are PRIVATE by default if your channel
                  isn&apos;t verified. Flip after the upload succeeds.
                </p>
              )}
              {visibility === "private" && (
                <p className="mt-1 flex items-start gap-1 text-[11px] text-emerald-600">
                  <CheckCircle2 className="size-3 shrink-0 translate-y-0.5" />
                  Safe default for YouTube. Instagram has no private toggle —
                  Reels always post publicly to your followers.
                </p>
              )}
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
          <Button onClick={onSubmit} disabled={pending || !hasAnyAccount}>
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {pending
              ? "Working..."
              : when === "now"
                ? "Post now"
                : "Schedule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Two-row chip list shown directly below the tag input. Designed to feel
 * like the chip strips Twitter/IG use for hashtag suggestions: one row of
 * AI-generated tags (lavender, sparkles icon), one or two rows of trending
 * tags (amber, flame icon). Clicking a chip moves it into the user's tag
 * list and removes it from the suggestion strip.
 */
function SuggestionChips({
  aiTags,
  trendingRelevant,
  trendingGeneral,
  trendingState,
  trendingError,
  onAdd,
  onRefreshTrending,
}: {
  aiTags: string[];
  trendingRelevant: TrendingTagSuggestion[];
  trendingGeneral: TrendingTagSuggestion[];
  trendingState: "idle" | "loading" | "loaded" | "error";
  trendingError: string | null;
  onAdd: (tag: string) => void;
  onRefreshTrending: () => void;
}) {
  const [showAllGeneral, setShowAllGeneral] = useState(false);
  const visibleGeneral = showAllGeneral
    ? trendingGeneral
    : trendingGeneral.slice(0, 8);

  const hasAnything =
    aiTags.length > 0 ||
    trendingRelevant.length > 0 ||
    trendingGeneral.length > 0 ||
    trendingState === "loading" ||
    trendingState === "error";

  if (!hasAnything) return null;

  return (
    <div className="space-y-2 rounded-md border bg-muted/20 p-2">
      {aiTags.length > 0 && (
        <ChipRow
          source="ai"
          label="AI suggestions"
          tags={aiTags.map((t) => ({ tag: t }))}
          onAdd={onAdd}
        />
      )}

      {trendingState === "loading" && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          Fetching trending tags from YouTube…
        </div>
      )}

      {trendingState === "error" && (
        <div className="space-y-1">
          <p className="flex items-start gap-1 text-[11px] text-amber-600">
            <XCircle className="size-3 shrink-0 translate-y-0.5" />
            <span className="line-clamp-2">{trendingError}</span>
          </p>
          <button
            type="button"
            onClick={onRefreshTrending}
            className="text-[10px] underline opacity-70 hover:opacity-100"
          >
            Retry
          </button>
        </div>
      )}

      {trendingState === "loaded" && trendingRelevant.length > 0 && (
        <ChipRow
          source="trending"
          label="Trending now · matches your clip"
          tags={trendingRelevant}
          onAdd={onAdd}
        />
      )}

      {trendingState === "loaded" && visibleGeneral.length > 0 && (
        <ChipRow
          source="trending"
          label="Trending now"
          tags={visibleGeneral}
          onAdd={onAdd}
          dim
          footer={
            trendingGeneral.length > 8 ? (
              <button
                type="button"
                onClick={() => setShowAllGeneral((v) => !v)}
                className="ml-1 text-[10px] underline opacity-70 hover:opacity-100"
              >
                {showAllGeneral ? "Show fewer" : `+${trendingGeneral.length - 8} more`}
              </button>
            ) : null
          }
        />
      )}

      {trendingState === "loaded" && (
        <button
          type="button"
          onClick={onRefreshTrending}
          className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
        >
          <RefreshCw className="size-2.5" />
          Refresh trending (cached 1 hr)
        </button>
      )}
    </div>
  );
}

function ChipRow({
  source,
  label,
  tags,
  onAdd,
  dim,
  footer,
}: {
  source: SuggestionSource;
  label: string;
  tags: TrendingTagSuggestion[] | { tag: string }[];
  onAdd: (tag: string) => void;
  dim?: boolean;
  footer?: React.ReactNode;
}) {
  const isAi = source === "ai";
  const Icon = isAi ? Sparkles : Flame;
  return (
    <div>
      <p
        className={cn(
          "mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide",
          isAi
            ? "text-violet-600 dark:text-violet-400"
            : "text-amber-600 dark:text-amber-400",
        )}
      >
        <Icon className="size-2.5" />
        {label}
      </p>
      <div className="flex flex-wrap gap-1">
        {tags.map((t) => {
          const tag = t.tag;
          const count = "count" in t ? t.count : undefined;
          return (
            <button
              key={`${source}:${tag}`}
              type="button"
              onClick={() => onAdd(tag)}
              title={
                count
                  ? `Used in ${count} of the top 50 trending videos`
                  : isAi
                    ? "AI suggestion"
                    : undefined
              }
              className={cn(
                "group inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                isAi
                  ? "border-violet-400/40 bg-violet-500/5 text-violet-700 hover:bg-violet-500/10 dark:text-violet-300"
                  : dim
                    ? "border-amber-400/30 bg-amber-500/5 text-amber-700/80 hover:bg-amber-500/10 dark:text-amber-300/80"
                    : "border-amber-400/40 bg-amber-500/10 text-amber-800 hover:bg-amber-500/20 dark:text-amber-300",
              )}
            >
              <Icon className="size-2.5 opacity-70" />
              <span>{tag}</span>
              {typeof count === "number" && (
                <span className="text-[9px] opacity-60">×{count}</span>
              )}
              <Plus className="size-2.5 opacity-0 transition-opacity group-hover:opacity-70" />
            </button>
          );
        })}
        {footer}
      </div>
    </div>
  );
}

function AiMetadataPanel({
  metadata,
  generating,
  onGenerate,
}: {
  metadata: GeneratedUploadMetadata | null;
  generating: boolean;
  onGenerate: () => void;
}) {
  const hasMeta = !!metadata;
  return (
    <div
      className={cn(
        "rounded-md border p-3",
        hasMeta
          ? "border-foreground/10 bg-gradient-to-br from-violet-500/5 via-fuchsia-500/5 to-amber-500/5"
          : "border-dashed",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <Sparkles className="size-3.5" />
            AI-written metadata
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {hasMeta ? (
              <>
                Generated {timeAgo(metadata!.generatedAt)} using{" "}
                <code className="rounded bg-muted px-1">{metadata!.model}</code>
                . Edit anything below before publishing.
              </>
            ) : (
              <>
                Hit Generate to produce an eye-catching title, optimized
                description, and discoverable tags based on this clip&apos;s
                transcript.
              </>
            )}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant={hasMeta ? "outline" : "default"}
          onClick={onGenerate}
          disabled={generating}
          className="shrink-0"
        >
          {generating ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : hasMeta ? (
            <RefreshCw className="size-3.5" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          {generating
            ? "Generating…"
            : hasMeta
              ? "Regenerate"
              : "Generate with AI"}
        </Button>
      </div>
      {hasMeta && metadata!.hook && (
        <p className="mt-2 text-xs italic text-muted-foreground">
          “{metadata!.hook}”
        </p>
      )}
    </div>
  );
}
