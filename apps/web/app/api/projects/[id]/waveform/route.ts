import { eq } from "drizzle-orm";
import { NextResponse } from "next/server";

import { db, schema } from "@/lib/db/client";

type RouteContext = { params: Promise<{ id: string }> };

export async function GET(_req: Request, ctx: RouteContext) {
  const { id: projectId } = await ctx.params;

  const video = db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.projectId, projectId))
    .limit(1)
    .all()[0];

  if (!video) {
    return NextResponse.json({ peaks: [] });
  }

  const audioFeat = db
    .select()
    .from(schema.audioFeatures)
    .where(eq(schema.audioFeatures.videoId, video.id))
    .limit(1)
    .all()[0];

  if (!audioFeat?.samplesJson?.length) {
    return NextResponse.json({ peaks: [], durationSeconds: video.durationSeconds });
  }

  const peaks = audioFeat.samplesJson.map((s) => ({
    t: s.t,
    v: s.excitement,
  }));

  return NextResponse.json({
    peaks,
    durationSeconds: video.durationSeconds ?? peaks.length,
  });
}
