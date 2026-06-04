"use client";

import { useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

/**
 * Reads `?oauth_connected=...&oauth_label=...` (or `?oauth_error=...`)
 * after we redirect back from the provider's callback, fires a Sonner
 * toast, then strips the params from the URL so reloading the page
 * doesn't re-trigger them.
 */
export function OAuthToast() {
  const router = useRouter();
  const params = useSearchParams();
  const firedRef = useRef(false);

  useEffect(() => {
    if (firedRef.current) return;
    const connected = params.get("oauth_connected");
    const label = params.get("oauth_label");
    const error = params.get("oauth_error");

    if (!connected && !error) return;
    firedRef.current = true;

    if (connected) {
      const platform =
        connected === "youtube"
          ? "YouTube"
          : connected === "instagram"
            ? "Instagram"
            : connected;
      toast.success(`Connected ${platform}${label ? `: ${label}` : ""}`);
    } else if (error) {
      toast.error(error);
    }

    router.replace("/accounts");
  }, [params, router]);

  return null;
}
