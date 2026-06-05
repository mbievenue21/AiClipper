"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { FileText, Loader2, Upload } from "lucide-react";
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

import { uploadTranscriptAction } from "./actions";

/**
 * Upload-your-own-transcript dialog.
 *
 * Why this exists: auto-transcription is good but not perfect (proper
 * nouns, mumbled lines, specialty jargon, multilingual VODs). Letting the
 * user paste a corrected SRT/VTT trumps any algorithmic improvement we
 * could make on the worker side.
 *
 * The flow:
 *   1. User drags or picks an .srt / .vtt / .json file (≤ 5 MB).
 *   2. We read it client-side and post the text content to the server
 *      action (we don't upload a File blob — simpler + no multipart).
 *   3. Server parses, replaces the transcript + highlights, optionally
 *      re-enqueues analyze.
 *
 * Plain .txt is intentionally NOT supported here — without timestamps the
 * server would need forced alignment which is a much bigger lift. We tell
 * the user that clearly so they know to convert.
 */
export function TranscriptUploadDialog({
  projectId,
  hasExistingTranscript,
}: {
  projectId: string;
  hasExistingTranscript: boolean;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const [filename, setFilename] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [rerunAnalyze, setRerunAnalyze] = useState(true);
  const [dragging, setDragging] = useState(false);

  const accepted = ".srt,.vtt,.json,text/vtt,application/x-subrip,application/json";

  const handleFile = (file: File | null) => {
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (
      !lower.endsWith(".srt") &&
      !lower.endsWith(".vtt") &&
      !lower.endsWith(".json")
    ) {
      toast.error("Only .srt, .vtt, or .json files are supported.");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast.error("File is over the 5 MB limit.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      setFilename(file.name);
      setContent(text);
    };
    reader.onerror = () => toast.error("Could not read file.");
    reader.readAsText(file);
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const onSubmit = () => {
    if (!filename || !content) {
      toast.error("Pick a transcript file first.");
      return;
    }
    startTransition(async () => {
      const res = await uploadTranscriptAction({
        projectId,
        filename,
        content,
        rerunAnalyze,
      });
      if (res.ok) {
        toast.success(res.message);
        setOpen(false);
        setFilename(null);
        setContent(null);
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
          <Button size="sm" variant="outline">
            <FileText className="size-3.5" />
            {hasExistingTranscript
              ? "Replace transcript"
              : "Upload transcript"}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {hasExistingTranscript
              ? "Replace transcript"
              : "Upload your own transcript"}
          </DialogTitle>
          <DialogDescription>
            Paste your own captions to fix transcription errors or use a
            higher-quality transcript. SRT, WebVTT, or Whisper-style JSON.
          </DialogDescription>
        </DialogHeader>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          className={
            "flex cursor-pointer flex-col items-center justify-center rounded-md border border-dashed p-6 text-center transition-colors " +
            (dragging ? "border-foreground bg-accent" : "hover:bg-accent/50")
          }
        >
          <Upload className="mb-2 size-5 text-muted-foreground" />
          {filename ? (
            <>
              <p className="text-sm font-medium">{filename}</p>
              <p className="text-[11px] text-muted-foreground">
                {(content?.length ?? 0).toLocaleString()} characters · click
                to choose a different file
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-medium">
                Drop a transcript file here
              </p>
              <p className="text-[11px] text-muted-foreground">
                or click to pick (.srt, .vtt, .json — up to 5 MB)
              </p>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            accept={accepted}
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
          />
        </div>

        <div className="space-y-2 rounded-md border bg-muted/30 p-2 text-[11px]">
          <p>
            <strong>Supported:</strong> SRT (SubRip), WebVTT (YouTube
            export), and JSON in Whisper's segments format.
          </p>
          <p>
            <strong>Not supported:</strong> plain .txt without timestamps.
            Add timing in a tool like{" "}
            <a
              className="underline"
              href="https://www.descript.com/"
              target="_blank"
              rel="noopener noreferrer"
            >
              Descript
            </a>{" "}
            or{" "}
            <a
              className="underline"
              href="https://otter.ai/"
              target="_blank"
              rel="noopener noreferrer"
            >
              Otter
            </a>{" "}
            first, then export as VTT.
          </p>
        </div>

        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={rerunAnalyze}
            onChange={(e) => setRerunAnalyze(e.target.checked)}
            className="size-4 accent-foreground"
          />
          Re-run highlight analysis with the new transcript
        </label>

        {hasExistingTranscript && (
          <p className="text-[11px] text-amber-600">
            This will replace the existing transcript and clear any highlights
            built from the old text.
          </p>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={pending || !content}>
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            {pending ? "Saving…" : "Replace transcript"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
