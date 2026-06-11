import { NextResponse } from "next/server";

import { getProfileTrainingStatus } from "@/lib/profiles/training-status";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  const status = await getProfileTrainingStatus(id);
  return NextResponse.json(status);
}
