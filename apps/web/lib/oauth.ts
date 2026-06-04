/**
 * OAuth helpers shared by the YouTube + Instagram routes under
 * `app/api/auth/{platform}`.
 *
 * Security model
 * --------------
 * - `state` is a 256-bit random token persisted in an HttpOnly cookie scoped
 *   to `/api/auth/{platform}`. The provider echoes it back on the callback;
 *   we compare with the cookie to defeat CSRF.
 * - Cookies live 10 minutes (plenty for a human to click "Allow") and are
 *   `secure` in production. They're deleted on read (single-use).
 * - PKCE helpers are kept in this module — Instagram doesn't currently
 *   require PKCE for the Business Login flow, but if/when YouTube enforces
 *   it on web clients (already required for mobile), the helpers are ready.
 * - We trim env values at the boundary so accidental trailing spaces in
 *   `.env` don't silently break OAuth.
 */
import "server-only";

import { createHash, randomBytes } from "node:crypto";
import { cookies } from "next/headers";

const COOKIE_TTL_SECONDS = 600;

export type OAuthPlatform = "youtube" | "instagram";

export function generateState(): string {
  return randomBytes(32).toString("base64url");
}

export function generateCodeVerifier(): string {
  // 64 URL-safe chars = 384 bits of entropy. Well above the RFC 7636 floor.
  return randomBytes(48).toString("base64url");
}

export function codeChallengeFromVerifier(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}

function stateCookieName(platform: OAuthPlatform) {
  return `oauth_${platform}_state`;
}
function verifierCookieName(platform: OAuthPlatform) {
  return `oauth_${platform}_verifier`;
}

export async function setOAuthCookies(
  platform: OAuthPlatform,
  state: string,
  verifier: string | null,
): Promise<void> {
  const jar = await cookies();
  const baseOpts = {
    httpOnly: true,
    sameSite: "lax" as const,
    path: `/api/auth/${platform}`,
    maxAge: COOKIE_TTL_SECONDS,
    secure: process.env.NODE_ENV === "production",
  };
  jar.set(stateCookieName(platform), state, baseOpts);
  if (verifier) {
    jar.set(verifierCookieName(platform), verifier, baseOpts);
  }
}

export async function consumeOAuthCookies(
  platform: OAuthPlatform,
): Promise<{ state: string | null; verifier: string | null }> {
  const jar = await cookies();
  const state = jar.get(stateCookieName(platform))?.value ?? null;
  const verifier = jar.get(verifierCookieName(platform))?.value ?? null;
  jar.delete(stateCookieName(platform));
  jar.delete(verifierCookieName(platform));
  return { state, verifier };
}

export function getYouTubeConfig() {
  return {
    clientId: process.env.YOUTUBE_CLIENT_ID?.trim() || "",
    clientSecret: process.env.YOUTUBE_CLIENT_SECRET?.trim() || "",
    redirectUri:
      process.env.YOUTUBE_REDIRECT_URI?.trim() ||
      `${appUrl()}/api/auth/youtube/callback`,
  };
}

export function getInstagramConfig() {
  return {
    // Meta calls this "Instagram App ID" in the Business Login flow.
    appId: process.env.INSTAGRAM_APP_ID?.trim() || "",
    appSecret: process.env.INSTAGRAM_APP_SECRET?.trim() || "",
    redirectUri:
      process.env.INSTAGRAM_REDIRECT_URI?.trim() ||
      `${appUrl()}/api/auth/instagram/callback`,
  };
}

export function appUrl(): string {
  return process.env.NEXT_PUBLIC_APP_URL?.trim() || "http://localhost:3000";
}

function makeAccountsUrl(): URL {
  return new URL("/accounts", appUrl());
}

export function oauthErrorRedirect(message: string): Response {
  const url = makeAccountsUrl();
  url.searchParams.set("oauth_error", message.slice(0, 400));
  return Response.redirect(url.toString(), 303);
}

export function oauthSuccessRedirect(
  platform: OAuthPlatform,
  label: string,
): Response {
  const url = makeAccountsUrl();
  url.searchParams.set("oauth_connected", platform);
  url.searchParams.set("oauth_label", label.slice(0, 120));
  return Response.redirect(url.toString(), 303);
}

export function youtubeOAuthReady(): boolean {
  const c = getYouTubeConfig();
  return !!(c.clientId && c.clientSecret && c.redirectUri);
}

export function instagramOAuthReady(): boolean {
  const c = getInstagramConfig();
  return !!(c.appId && c.appSecret && c.redirectUri);
}

export function missingYouTubeKeys(): string[] {
  const c = getYouTubeConfig();
  const missing: string[] = [];
  if (!c.clientId) missing.push("YOUTUBE_CLIENT_ID");
  if (!c.clientSecret) missing.push("YOUTUBE_CLIENT_SECRET");
  if (!c.redirectUri) missing.push("YOUTUBE_REDIRECT_URI");
  return missing;
}

export function missingInstagramKeys(): string[] {
  const c = getInstagramConfig();
  const missing: string[] = [];
  if (!c.appId) missing.push("INSTAGRAM_APP_ID");
  if (!c.appSecret) missing.push("INSTAGRAM_APP_SECRET");
  if (!c.redirectUri) missing.push("INSTAGRAM_REDIRECT_URI");
  return missing;
}
