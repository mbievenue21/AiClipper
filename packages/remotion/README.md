# @aiclipper/remotion

React-based Remotion compositions for caption overlays.

**Note on production status**: the default caption pipeline in
`apps/worker/worker/render/captions.py` uses ffmpeg + libass (Advanced
SubStation Alpha subtitles burned in via the `subtitles` filter). That
path renders in **2–5 seconds** per clip, supports the same per-word
highlight/karaoke/popup styles, and ships with every ffmpeg install — no
Chromium needed.

This package is here as a forward-looking option for richer Remotion-only
animations (3D word transforms, motion blur, particle effects). To enable
it, wire `apps/worker/worker/render/captions.py` to call `npx remotion render`
on this package's `CaptionedClip` composition instead of producing an `.ass`
file. See the worker comment block in `captions.py` for the swap point.

## Compositions

- `CaptionedClip` — overlays animated word-level captions on top of a
  pre-rendered clip MP4 passed in via the `clipPath` prop.

## Props

```ts
{
  clipPath: string;             // absolute path to clip-source.mp4
  durationSeconds: number;
  width: number;                // typically 1080
  height: number;               // typically 1920
  segments: Array<{
    start: number;              // seconds, clip-relative
    end: number;
    words: Array<{ text: string; start: number; end: number }>;
  }>;
  style: {
    font: "inter" | "bebas" | "anton" | "marker" | "mono" | "montserrat";
    style: "highlight" | "popup" | "karaoke" | "minimal";
    primaryColor: string;       // "#FFD700"
    accentColor: string;        // "#FFFFFF"
    uppercase: boolean;
  };
}
```

## Run

```bash
pnpm --filter @aiclipper/remotion install
pnpm --filter @aiclipper/remotion preview          # interactive
pnpm --filter @aiclipper/remotion render --props=./props.json
```
