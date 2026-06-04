"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { addAccountAction, deleteAccountAction } from "./actions";

export type ManagedAccount = {
  id: string;
  platform: "youtube" | "instagram";
  label: string;
  refreshToken: string | null;
  expiresAt: number | null;
  createdAt: number;
};

export function AccountsManager({
  accounts,
}: {
  accounts: ManagedAccount[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [platform, setPlatform] = useState<"youtube" | "instagram">("youtube");

  const submit = (fd: FormData) => {
    startTransition(async () => {
      const res = await addAccountAction(fd);
      (res.ok ? toast.success : toast.error)(res.message);
      if (res.ok) router.refresh();
    });
  };

  const remove = (id: string) => {
    if (
      !confirm(
        "Remove this account? Existing scheduled uploads will be cancelled.",
      )
    ) {
      return;
    }
    const fd = new FormData();
    fd.set("id", id);
    startTransition(async () => {
      const res = await deleteAccountAction(fd);
      (res.ok ? toast.success : toast.error)(res.message);
      if (res.ok) router.refresh();
    });
  };

  return (
    <div className="space-y-3">
      {accounts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No accounts yet — click a Connect button above to authenticate.
        </p>
      ) : (
        <div className="space-y-2">
          {accounts.map((a) => (
            <div
              key={a.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm"
            >
              <div className="min-w-0">
                <p className="flex items-center gap-2">
                  <Badge variant="outline">{a.platform}</Badge>
                  <span className="truncate font-medium">{a.label}</span>
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {a.refreshToken ? "Refresh token saved" : "No refresh token"}
                  {a.expiresAt
                    ? ` · expires ${new Date(a.expiresAt).toLocaleString()}`
                    : ""}
                  {" · added "}
                  {new Date(a.createdAt).toLocaleDateString()}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => remove(a.id)}
                disabled={pending}
              >
                <Trash2 className="size-3.5" />
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowAdvanced((v) => !v)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {showAdvanced ? (
          <ChevronDown className="size-3.5" />
        ) : (
          <ChevronRight className="size-3.5" />
        )}
        Advanced: paste tokens manually
      </button>

      {showAdvanced && (
        <div className="rounded-md border bg-muted/30 p-3">
          <p className="mb-2 text-xs text-muted-foreground">
            Use this only when the OAuth redirect can&apos;t reach this app
            (e.g. running over an SSH tunnel). For local development, prefer
            the Connect buttons above.
          </p>
          <form action={submit} className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2 flex gap-2">
              {(["youtube", "instagram"] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPlatform(p)}
                  className={`flex-1 rounded-md border p-2 text-sm transition-colors ${
                    platform === p
                      ? "border-foreground bg-accent"
                      : "border-border hover:bg-accent/50"
                  }`}
                >
                  {p.charAt(0).toUpperCase() + p.slice(1)}
                </button>
              ))}
              <input type="hidden" name="platform" value={platform} />
            </div>
            <Input
              name="label"
              placeholder='Label (e.g. "Main channel")'
              required
              maxLength={80}
            />
            <Input
              name="expiresAt"
              type="datetime-local"
              placeholder="Token expires at (optional)"
            />
            <Input
              name="accessToken"
              type="password"
              placeholder="Access token"
              required
              minLength={8}
              className="sm:col-span-2 font-mono text-xs"
            />
            <Input
              name="refreshToken"
              type="password"
              placeholder="Refresh token (optional but recommended)"
              className="sm:col-span-2 font-mono text-xs"
            />
            <div className="sm:col-span-2 flex justify-end">
              <Button type="submit" disabled={pending}>
                {pending && <Loader2 className="size-3.5 animate-spin" />}
                <Plus className="size-3.5" />
                Add account
              </Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
