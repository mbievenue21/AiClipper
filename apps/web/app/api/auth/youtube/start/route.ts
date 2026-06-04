/**
 * GET /api/auth/youtube/start
 *
 * Kicks off the Google OAuth 2.0 flow for the YouTube Data API v3. We ask
 * for `youtube.upload` (write-only access for clip publishing) and
 * `youtube.readonly` (to fetch the channel's display name so we can label
 * the saved account in the UI).
 *
 * We use `access_type=offline` + `prompt=consent` so Google reliably
 * returns a refresh_token even on subsequent connections. Without
 * `prompt=consent`, Google may suppress the refresh token if the user has
 * previously granted access, which would silently break renewal later.
 */
import {
  generateState,
  getYouTubeConfig,
  missingYouTubeKeys,
  oauthErrorRedirect,
  setOAuthCookies,
} from "@/lib/oauth";

export const dynamic = "force-dynamic";

const SCOPES = [
  "https://www.googleapis.com/auth/youtube.upload",
  "https://www.googleapis.com/auth/youtube.readonly",
].join(" ");

export async function GET() {
  const missing = missingYouTubeKeys();
  if (missing.length > 0) {
    return oauthErrorRedirect(
      `YouTube OAuth not configured. Missing in .env: ${missing.join(", ")}`,
    );
  }
  const cfg = getYouTubeConfig();
  const state = generateState();
  await setOAuthCookies("youtube", state, null);

  const authorize = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authorize.searchParams.set("client_id", cfg.clientId);
  authorize.searchParams.set("redirect_uri", cfg.redirectUri);
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("scope", SCOPES);
  authorize.searchParams.set("access_type", "offline");
  authorize.searchParams.set("prompt", "consent");
  authorize.searchParams.set("include_granted_scopes", "true");
  authorize.searchParams.set("state", state);
  return Response.redirect(authorize.toString(), 302);
}
