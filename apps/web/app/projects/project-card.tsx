"use client";

import Link from "next/link";
import { formatDistanceToNow } from "date-fns";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { DeleteProjectDialog } from "./[id]/delete-project-dialog";

const statusVariants: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "secondary",
  ingesting: "secondary",
  transcribing: "secondary",
  analyzing: "secondary",
  ready: "default",
  failed: "destructive",
};

export function ProjectCard({
  id,
  name,
  status,
  sourceUrl,
  createdAt,
}: {
  id: string;
  name: string;
  status: string;
  sourceUrl: string | null;
  createdAt: Date | number;
}) {
  return (
    <Card className="transition-colors hover:border-foreground/30">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <Link href={`/projects/${id}`} className="min-w-0 flex-1 group">
            <CardTitle className="line-clamp-2 group-hover:underline">
              {name}
            </CardTitle>
          </Link>
          <div className="flex shrink-0 items-center gap-1">
            <Badge variant={statusVariants[status] ?? "secondary"}>
              {status}
            </Badge>
            <DeleteProjectDialog
              projectId={id}
              projectName={name}
              variant="ghost"
              compact
            />
          </div>
        </div>
        {sourceUrl && (
          <CardDescription className="line-clamp-1">
            <Link href={`/projects/${id}`} className="hover:underline">
              {sourceUrl}
            </Link>
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">
        <Link href={`/projects/${id}`} className="hover:underline">
          Created {formatDistanceToNow(createdAt, { addSuffix: true })}
        </Link>
      </CardContent>
    </Card>
  );
}
