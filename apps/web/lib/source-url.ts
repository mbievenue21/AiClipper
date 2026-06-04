export type SourceType = "youtube" | "twitch" | "upload";

export function detectSourceType(url: string): SourceType {
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    throw new Error("Enter a valid http(s) URL.");
  }

  const host = parsed.hostname.replace(/^www\./, "").toLowerCase();

  if (
    host === "youtube.com" ||
    host === "youtu.be" ||
    host === "m.youtube.com" ||
    host.endsWith(".youtube.com")
  ) {
    return "youtube";
  }

  if (
    host === "twitch.tv" ||
    host.endsWith(".twitch.tv") ||
    host === "clips.twitch.tv"
  ) {
    return "twitch";
  }

  throw new Error(
    "Unsupported URL. Use a YouTube or Twitch VOD link (file upload comes later).",
  );
}

export function defaultProjectName(url: string): string {
  try {
    const parsed = new URL(url.trim());
    const host = parsed.hostname.replace(/^www\./, "");
    const id =
      parsed.searchParams.get("v") ??
      parsed.pathname.split("/").filter(Boolean).pop();
    return id ? `${host} — ${id}` : host;
  } catch {
    return "New project";
  }
}
