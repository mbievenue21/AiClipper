/**
 * GET /api/media/<rel-path-under-MEDIA_ROOT>
 *
 * Streams downloaded videos, rendered clips, and other media files that the
 * Python worker writes under `data/videos/<project>/...`. The path is
 * normalized and verified to live inside MEDIA_ROOT — any attempt to escape
 * (e.g. `../../etc/passwd`) returns 400.
 *
 * Supports HTTP Range requests so the browser <video> tag can scrub the clip
 * without downloading the whole file first.
 */
import { createReadStream, statSync } from "node:fs";
import path from "node:path";
import { NextRequest } from "next/server";

import { Readable } from "node:stream";

function resolveMediaRoot(): string {
  const raw = process.env.MEDIA_ROOT ?? "./data/videos";
  const repoRoot = path.resolve(process.cwd(), "..", "..");
  return path.isAbsolute(raw) ? raw : path.resolve(repoRoot, raw);
}

const MEDIA_ROOT = path.resolve(resolveMediaRoot());

const MIME_BY_EXT: Record<string, string> = {
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".mov": "video/quicktime",
  ".mkv": "video/x-matroska",
  ".m4a": "audio/mp4",
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
  ".ass": "text/plain; charset=utf-8",
  ".srt": "text/plain; charset=utf-8",
  ".vtt": "text/vtt",
  ".json": "application/json",
};

function mimeFor(file: string): string {
  return MIME_BY_EXT[path.extname(file).toLowerCase()] ?? "application/octet-stream";
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path: parts } = await ctx.params;
  if (!parts || parts.length === 0) {
    return new Response("missing path", { status: 400 });
  }

  // Reject path traversal *before* resolution.
  if (parts.some((p) => p.includes("..") || p.includes("\\"))) {
    return new Response("invalid path", { status: 400 });
  }

  const relPosix = parts.join("/");
  const abs = path.resolve(MEDIA_ROOT, relPosix);

  // Final guard: resolved path must still be inside MEDIA_ROOT.
  if (!abs.startsWith(MEDIA_ROOT + path.sep) && abs !== MEDIA_ROOT) {
    return new Response("outside media root", { status: 400 });
  }

  let stat: ReturnType<typeof statSync>;
  try {
    stat = statSync(abs);
  } catch {
    return new Response("not found", { status: 404 });
  }
  if (!stat.isFile()) {
    return new Response("not a file", { status: 404 });
  }

  const total = stat.size;
  const mime = mimeFor(abs);
  const range = req.headers.get("range");

  if (range) {
    const m = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!m) {
      return new Response("invalid range", { status: 416 });
    }
    const start = m[1] ? parseInt(m[1], 10) : 0;
    const end = m[2] ? parseInt(m[2], 10) : total - 1;
    if (start >= total || end >= total || start > end) {
      return new Response("range not satisfiable", {
        status: 416,
        headers: { "Content-Range": `bytes */${total}` },
      });
    }
    const chunkSize = end - start + 1;
    const stream = createReadStream(abs, { start, end });
    return new Response(
      Readable.toWeb(stream) as unknown as ReadableStream<Uint8Array>,
      {
        status: 206,
        headers: {
          "Content-Type": mime,
          "Content-Length": String(chunkSize),
          "Content-Range": `bytes ${start}-${end}/${total}`,
          "Accept-Ranges": "bytes",
          "Cache-Control": "private, max-age=0, no-store",
        },
      },
    );
  }

  const stream = createReadStream(abs);
  return new Response(
    Readable.toWeb(stream) as unknown as ReadableStream<Uint8Array>,
    {
      status: 200,
      headers: {
        "Content-Type": mime,
        "Content-Length": String(total),
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=0, no-store",
      },
    },
  );
}
