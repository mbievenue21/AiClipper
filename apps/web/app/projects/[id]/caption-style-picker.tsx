"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import type {
  CaptionFont,
  CaptionStyle,
  ClipCaptionSettings,
} from "@/lib/db/schema";
import { DEFAULT_CAPTION_SETTINGS } from "@/lib/db/schema";

export type CaptionStyleState = ClipCaptionSettings;

export function defaultCaptionStyleState(): CaptionStyleState {
  return { ...DEFAULT_CAPTION_SETTINGS };
}

const FONTS: {
  id: CaptionFont;
  label: string;
  className: string;
  preview: string;
}[] = [
  { id: "anton", label: "Anton", className: "font-[Anton,Impact,sans-serif]", preview: "BOLD CONDENSED" },
  { id: "bebas", label: "Bebas", className: "font-[\"Bebas_Neue\",Impact,sans-serif]", preview: "CLEAN CAPS" },
  { id: "inter", label: "Inter", className: "font-sans", preview: "Modern & clean" },
  { id: "montserrat", label: "Montserrat", className: "font-[Montserrat,system-ui,sans-serif]", preview: "Geometric" },
  { id: "marker", label: "Marker", className: "font-[\"Permanent_Marker\",Impact,cursive]", preview: "Hand-drawn" },
  { id: "mono", label: "Mono", className: "font-mono", preview: "code_style()" },
];

const STYLES: {
  id: CaptionStyle;
  label: string;
  blurb: string;
}[] = [
  {
    id: "highlight",
    label: "Highlight",
    blurb: "Current word pops in bright color, others stay muted",
  },
  {
    id: "popup",
    label: "Popup",
    blurb: "Each word springs in with a tiny scale-up",
  },
  {
    id: "karaoke",
    label: "Karaoke",
    blurb: "Full line on screen, sweeps through words as spoken",
  },
  {
    id: "minimal",
    label: "Minimal",
    blurb: "Clean static block, no per-word animation",
  },
];

export function CaptionStylePicker({
  value,
  onChange,
}: {
  value: CaptionStyleState;
  onChange: (next: CaptionStyleState) => void;
}) {
  const set = <K extends keyof CaptionStyleState>(
    key: K,
    v: CaptionStyleState[K],
  ) => onChange({ ...value, [key]: v });

  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Style
        </p>
        <div className="grid grid-cols-2 gap-2">
          {STYLES.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => set("style", s.id)}
              className={cn(
                "rounded-md border p-2 text-left transition-colors",
                value.style === s.id
                  ? "border-foreground bg-accent text-foreground"
                  : "border-border hover:bg-accent/50",
              )}
            >
              <p className="text-sm font-medium">{s.label}</p>
              <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                {s.blurb}
              </p>
            </button>
          ))}
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Font
        </p>
        <div className="grid grid-cols-3 gap-2">
          {FONTS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => set("font", f.id)}
              className={cn(
                "rounded-md border p-2 transition-colors",
                value.font === f.id
                  ? "border-foreground bg-accent"
                  : "border-border hover:bg-accent/50",
              )}
            >
              <p className="text-xs font-medium">{f.label}</p>
              <p className={cn("mt-1 text-base leading-tight", f.className)}>
                {f.preview}
              </p>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <label className="flex items-center justify-between rounded-md border p-2 text-xs">
          <div>
            <p className="font-medium">Auto-color from clip</p>
            <p className="text-muted-foreground">
              Gradient picks contrasting hues based on the clip&apos;s dominant
              frame color.
            </p>
          </div>
          <input
            type="checkbox"
            checked={value.autoColor}
            onChange={(e) => set("autoColor", e.target.checked)}
            className="size-4 accent-foreground"
          />
        </label>
        <label className="flex items-center justify-between rounded-md border p-2 text-xs">
          <p className="font-medium">UPPERCASE</p>
          <input
            type="checkbox"
            checked={value.uppercase}
            onChange={(e) => set("uppercase", e.target.checked)}
            className="size-4 accent-foreground"
          />
        </label>
        {!value.autoColor && (
          <div className="grid grid-cols-2 gap-2">
            <label className="rounded-md border p-2 text-xs">
              <p className="font-medium">Primary</p>
              <input
                type="color"
                value={value.primaryColor}
                onChange={(e) => set("primaryColor", e.target.value)}
                className="mt-1 h-8 w-full cursor-pointer rounded"
              />
            </label>
            <label className="rounded-md border p-2 text-xs">
              <p className="font-medium">Accent</p>
              <input
                type="color"
                value={value.accentColor}
                onChange={(e) => set("accentColor", e.target.value)}
                className="mt-1 h-8 w-full cursor-pointer rounded"
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
