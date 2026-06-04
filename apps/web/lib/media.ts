/**
 * Helpers for building URLs to media files served by /api/media.
 *
 * The DB stores POSIX-style paths relative to MEDIA_ROOT, e.g.
 * "<projectId>/clips/<clipId>/clip-captioned.mp4". We URL-encode each
 * segment so spaces and unusual characters work, but leave the slashes.
 */
export function mediaUrl(relPath: string | null | undefined): string | null {
  if (!relPath) return null;
  const cleaned = relPath.replace(/^\/+/, "").replace(/\\/g, "/");
  return "/api/media/" + cleaned.split("/").map(encodeURIComponent).join("/");
}

export function clipPreviewUrl(clip: {
  filePath: string;
  captionedFilePath?: string | null;
}): string | null {
  return mediaUrl(clip.captionedFilePath || clip.filePath);
}
