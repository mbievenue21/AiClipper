"use server";

import { revalidatePath } from "next/cache";
import { eq } from "drizzle-orm";
import { z } from "zod";

import { db, schema } from "@/lib/db/client";

const addAccountSchema = z.object({
  platform: z.enum(["youtube", "instagram"]),
  label: z.string().min(1).max(80),
  accessToken: z.string().min(8).max(4096),
  refreshToken: z.string().max(4096).optional(),
  expiresAt: z.string().optional(),
});

export async function addAccountAction(formData: FormData) {
  const raw = {
    platform: formData.get("platform"),
    label: formData.get("label"),
    accessToken: formData.get("accessToken"),
    refreshToken: formData.get("refreshToken") || undefined,
    expiresAt: formData.get("expiresAt") || undefined,
  };
  const parsed = addAccountSchema.safeParse(raw);
  if (!parsed.success) {
    return {
      ok: false as const,
      message: parsed.error.issues
        .map((i) => `${i.path.join(".")}: ${i.message}`)
        .join("; "),
    };
  }
  const data = parsed.data;
  const expiresAtMs = data.expiresAt ? new Date(data.expiresAt).getTime() : null;

  try {
    db.insert(schema.accounts)
      .values({
        platform: data.platform,
        label: data.label,
        accessToken: data.accessToken,
        refreshToken: data.refreshToken || null,
        expiresAt: expiresAtMs ? new Date(expiresAtMs) : null,
      })
      .run();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("UNIQUE")) {
      return {
        ok: false as const,
        message: `An account named "${data.label}" already exists for ${data.platform}.`,
      };
    }
    return { ok: false as const, message: msg };
  }

  revalidatePath("/accounts");
  return { ok: true as const, message: `Connected ${data.platform} account.` };
}

export async function deleteAccountAction(formData: FormData) {
  const id = String(formData.get("id") || "");
  if (!id) return { ok: false as const, message: "Missing id." };
  db.delete(schema.accounts).where(eq(schema.accounts.id, id)).run();
  revalidatePath("/accounts");
  return { ok: true as const, message: "Account removed." };
}
