"use client";

import { useState, useTransition } from "react";
import { Link2, Loader2, ThumbsDown, ThumbsUp, Upload } from "lucide-react";

import { submitTrainingFeedbackAction } from "@/app/train/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FeedbackNotesForm } from "@/components/profiles/feedback-notes-form";
import { Input } from "@/components/ui/input";
import type { HighlightProfile, TrainingExample } from "@/lib/db/schema";
import {
  editorNotesFromInput,
  EMPTY_EDITOR_NOTES_INPUT,
  type EditorNotesInput,
} from "@/lib/profiles/editor-notes";

type ClipDraft = {
  id: string;
  file: File;
  title: string;
  vote: "positive" | "negative";
  notes: EditorNotesInput;
  showNotes: boolean;
};

type UrlDraft = {
  id: string;
  url: string;
  title: string;
  vote: "positive" | "negative";
  notes: EditorNotesInput;
  showNotes: boolean;
};

type InputMode = "upload" | "url";

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const base64 = result.split(",")[1] ?? "";
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function TrainStudio({
  profiles,
  feedbackByProfile,
}: {
  profiles: HighlightProfile[];
  feedbackByProfile: Record<string, TrainingExample[]>;
}) {
  const [profileId, setProfileId] = useState(profiles[0]?.id ?? "");
  const [inputMode, setInputMode] = useState<InputMode>("upload");
  const [clips, setClips] = useState<ClipDraft[]>([]);
  const [urlDrafts, setUrlDrafts] = useState<UrlDraft[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [includeFeedback, setIncludeFeedback] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const pendingFeedback = feedbackByProfile[profileId] ?? [];
  const totalItems = clips.length + urlDrafts.length;

  const onFiles = (files: FileList | null) => {
    if (!files) return;
    const next: ClipDraft[] = [];
    Array.from(files).forEach((file, i) => {
      next.push({
        id: `${Date.now()}-${i}`,
        file,
        title: file.name.replace(/\.[^.]+$/, ""),
        vote: "positive",
        notes: { ...EMPTY_EDITOR_NOTES_INPUT },
        showNotes: false,
      });
    });
    setClips((prev) => [...prev, ...next]);
  };

  const addUrl = () => {
    const url = urlInput.trim();
    if (!url) return;
    setUrlDrafts((prev) => [
      ...prev,
      {
        id: `${Date.now()}`,
        url,
        title: url.replace(/^https?:\/\//, "").slice(0, 60),
        vote: "positive",
        notes: { ...EMPTY_EDITOR_NOTES_INPUT },
        showNotes: false,
      },
    ]);
    setUrlInput("");
  };

  const submit = () => {
    if (!profileId) {
      setMessage("Select a profile first");
      return;
    }
    if (totalItems === 0) {
      setMessage("Add at least one reference clip (upload or URL)");
      return;
    }

    startTransition(async () => {
      setMessage(null);
      const encoded = await Promise.all(
        clips.map(async (c) => ({
          fileName: c.file.name,
          base64: await fileToBase64(c.file),
          title: c.title,
          vote: c.vote,
          editorNotes: editorNotesFromInput(c.notes),
        })),
      );

      const candidateFeedback =
        includeFeedback && pendingFeedback.length > 0
          ? pendingFeedback.map((ex) => ({
              startSeconds: ex.startSeconds,
              endSeconds: ex.endSeconds,
              vote:
                ex.label === "accepted" || ex.label === "positive"
                  ? ("accepted" as const)
                  : ("rejected" as const),
              projectId: ex.projectId ?? undefined,
            }))
          : undefined;

      const result = await submitTrainingFeedbackAction({
        profileId,
        referenceClips: encoded.length > 0 ? encoded : undefined,
        referenceUrls:
          urlDrafts.length > 0
            ? urlDrafts.map((u) => ({
                url: u.url,
                title: u.title,
                vote: u.vote,
                editorNotes: editorNotesFromInput(u.notes),
              }))
            : undefined,
        candidateFeedback,
      });

      if (result.ok) {
        setMessage(result.message);
        setClips([]);
        setUrlDrafts([]);
      } else {
        setMessage(result.message ?? "Training submission failed");
      }
    });
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Reference clips</CardTitle>
          <CardDescription>
            Upload local shorts or paste YouTube / Twitch links. The worker uses
            the same yt-dlp downloader as production ingest. Mark each example
            good or bad, then submit to train the profile.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="profileId" className="text-sm font-medium">
                Profile
              </label>
              <select
                id="profileId"
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs"
              >
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <span className="text-sm font-medium">Add references</span>
              <div className="flex gap-1 rounded-md border p-1">
                <Button
                  type="button"
                  size="sm"
                  variant={inputMode === "upload" ? "default" : "ghost"}
                  className="flex-1"
                  onClick={() => setInputMode("upload")}
                >
                  <Upload className="size-3.5" />
                  Upload
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={inputMode === "url" ? "default" : "ghost"}
                  className="flex-1"
                  onClick={() => setInputMode("url")}
                >
                  <Link2 className="size-3.5" />
                  URL
                </Button>
              </div>
            </div>
          </div>

          {inputMode === "upload" ? (
            <div className="space-y-2">
              <label className="text-sm font-medium">Upload clips</label>
              <Input
                type="file"
                accept="video/*"
                multiple
                onChange={(e) => onFiles(e.target.files)}
              />
            </div>
          ) : (
            <div className="space-y-2">
              <label htmlFor="refUrl" className="text-sm font-medium">
                YouTube or Twitch URL
              </label>
              <div className="flex gap-2">
                <Input
                  id="refUrl"
                  placeholder="https://youtube.com/shorts/… or https://twitch.tv/…"
                  value={urlInput}
                  onChange={(e) => setUrlInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addUrl();
                    }
                  }}
                />
                <Button type="button" variant="secondary" onClick={addUrl}>
                  Add
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Shorts, clips, and VOD segments work. Downloads run on the worker
                via yt-dlp (same as new projects).
              </p>
            </div>
          )}

          {pendingFeedback.length > 0 && (
            <div className="rounded-md border border-dashed p-3 text-sm">
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={includeFeedback}
                  onChange={(e) => setIncludeFeedback(e.target.checked)}
                />
                <span>
                  Include <strong>{pendingFeedback.length}</strong> editor feedback
                  example{pendingFeedback.length === 1 ? "" : "s"} from project
                  pages in this training run.
                </span>
              </label>
            </div>
          )}

          {clips.length > 0 && (
            <div className="space-y-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Uploads
              </p>
              {clips.map((clip) => (
                <div
                  key={clip.id}
                  className="flex flex-wrap items-center gap-3 rounded-md border p-3"
                >
                  <Upload className="size-4 text-muted-foreground" />
                  <Input
                    className="max-w-xs"
                    value={clip.title}
                    onChange={(e) =>
                      setClips((prev) =>
                        prev.map((c) =>
                          c.id === clip.id ? { ...c, title: e.target.value } : c,
                        ),
                      )
                    }
                  />
                  <Badge variant="secondary">{clip.file.name}</Badge>
                  <VoteButtons
                    vote={clip.vote}
                    onVote={(vote) =>
                      setClips((prev) =>
                        prev.map((c) => (c.id === clip.id ? { ...c, vote } : c)),
                      )
                    }
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setClips((prev) =>
                        prev.map((c) =>
                          c.id === clip.id
                            ? { ...c, showNotes: !c.showNotes }
                            : c,
                        ),
                      )
                    }
                  >
                    Notes
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setClips((prev) => prev.filter((c) => c.id !== clip.id))
                    }
                  >
                    Remove
                  </Button>
                  {clip.showNotes && (
                    <div className="w-full basis-full">
                      <FeedbackNotesForm
                        value={clip.notes}
                        onChange={(notes) =>
                          setClips((prev) =>
                            prev.map((c) =>
                              c.id === clip.id ? { ...c, notes } : c,
                            ),
                          )
                        }
                        vote={clip.vote}
                        compact
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {urlDrafts.length > 0 && (
            <div className="space-y-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                URLs to download
              </p>
              {urlDrafts.map((item) => (
                <div
                  key={item.id}
                  className="flex flex-wrap items-center gap-3 rounded-md border p-3"
                >
                  <Link2 className="size-4 text-muted-foreground" />
                  <Input
                    className="max-w-xs"
                    value={item.title}
                    onChange={(e) =>
                      setUrlDrafts((prev) =>
                        prev.map((u) =>
                          u.id === item.id ? { ...u, title: e.target.value } : u,
                        ),
                      )
                    }
                  />
                  <Badge variant="secondary" className="max-w-md truncate">
                    {item.url}
                  </Badge>
                  <VoteButtons
                    vote={item.vote}
                    onVote={(vote) =>
                      setUrlDrafts((prev) =>
                        prev.map((u) => (u.id === item.id ? { ...u, vote } : u)),
                      )
                    }
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setUrlDrafts((prev) =>
                        prev.map((u) =>
                          u.id === item.id
                            ? { ...u, showNotes: !u.showNotes }
                            : u,
                        ),
                      )
                    }
                  >
                    Notes
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setUrlDrafts((prev) => prev.filter((u) => u.id !== item.id))
                    }
                  >
                    Remove
                  </Button>
                  {item.showNotes && (
                    <div className="w-full basis-full">
                      <FeedbackNotesForm
                        value={item.notes}
                        onChange={(notes) =>
                          setUrlDrafts((prev) =>
                            prev.map((u) =>
                              u.id === item.id ? { ...u, notes } : u,
                            ),
                          )
                        }
                        vote={item.vote}
                        compact
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <Button onClick={submit} disabled={pending || totalItems === 0}>
            {pending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Submitting…
              </>
            ) : (
              "Submit training feedback"
            )}
          </Button>

          {message && (
            <p className="text-sm text-muted-foreground">{message}</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function VoteButtons({
  vote,
  onVote,
}: {
  vote: "positive" | "negative";
  onVote: (vote: "positive" | "negative") => void;
}) {
  return (
    <div className="flex gap-1">
      <Button
        size="sm"
        variant={vote === "positive" ? "default" : "outline"}
        onClick={() => onVote("positive")}
      >
        <ThumbsUp className="size-3.5" />
        Good
      </Button>
      <Button
        size="sm"
        variant={vote === "negative" ? "destructive" : "outline"}
        onClick={() => onVote("negative")}
      >
        <ThumbsDown className="size-3.5" />
        Bad
      </Button>
    </div>
  );
}
