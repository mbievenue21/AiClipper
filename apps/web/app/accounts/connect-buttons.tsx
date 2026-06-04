/**
 * Server component that renders one "Connect with X" card per platform.
 * Disabled state explains exactly which env vars are still missing so the
 * user doesn't have to dig through README.md to figure it out.
 */
import { Camera, CheckCircle2, ExternalLink, PlayCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type Platform = "youtube" | "instagram";

const META: Record<
  Platform,
  { label: string; helpUrl: string; missingHint: string; note: string }
> = {
  youtube: {
    label: "YouTube",
    helpUrl: "https://console.cloud.google.com/apis/credentials",
    missingHint:
      "Open Google Cloud Console, enable YouTube Data API v3, create an OAuth client (Web application), and add http://localhost:3000/api/auth/youtube/callback as an authorized redirect URI.",
    note: "Uploads as private by default. Flip to public from YouTube Studio.",
  },
  instagram: {
    label: "Instagram",
    helpUrl: "https://developers.facebook.com/apps/",
    missingHint:
      "Open Meta for Developers, create a Business app, add the Instagram product (API setup with Instagram login), then copy the Instagram app ID + secret. Your IG account must be Business or Creator and added as a tester.",
    note: "Posts as a public Reel — Instagram has no private/unlisted option.",
  },
};

export function ConnectCard({
  platform,
  oauthReady,
  missingKeys,
  connectedCount,
}: {
  platform: Platform;
  oauthReady: boolean;
  missingKeys: string[];
  connectedCount: number;
}) {
  const Icon = platform === "youtube" ? PlayCircle : Camera;
  const meta = META[platform];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <Icon className="size-4" />
            {meta.label}
          </span>
          {connectedCount > 0 ? (
            <Badge variant="default" className="gap-1">
              <CheckCircle2 className="size-3" />
              {connectedCount} connected
            </Badge>
          ) : (
            <Badge variant="outline">Not connected</Badge>
          )}
        </CardTitle>
        <CardDescription className="text-xs">
          {oauthReady
            ? `Sign in once — tokens are saved locally and auto-refreshed on upload.`
            : `OAuth client is not configured.`}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3 text-xs">
        <p className="text-[11px] text-muted-foreground">{meta.note}</p>

        {oauthReady ? (
          // Plain <a> (not next/link) because the destination is an API
          // route that immediately 302s to a third-party origin. Using
          // <Link> would make the App Router try to prefetch the RSC
          // payload first, which fails the cross-origin redirect with a
          // "Failed to fetch RSC payload" warning before falling back to
          // a real browser navigation. A regular anchor skips the dance.
          <Button asChild size="sm" className="w-full">
            <a href={`/api/auth/${platform}/start`} rel="nofollow">
              Connect with {meta.label}
            </a>
          </Button>
        ) : (
          <>
            <Button disabled size="sm" className="w-full">
              Connect with {meta.label}
            </Button>
            <div className="rounded-md border border-dashed border-amber-500/40 bg-amber-50 p-2 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              <p className="font-medium">
                Missing in .env: {missingKeys.join(", ")}
              </p>
              <p className="mt-1 text-[11px] leading-snug">{meta.missingHint}</p>
              <a
                href={meta.helpUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex items-center gap-1 underline"
              >
                Open {meta.label} developer console
                <ExternalLink className="size-3" />
              </a>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
