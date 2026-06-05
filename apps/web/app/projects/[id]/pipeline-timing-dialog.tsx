"use client";

import Link from "next/link";
import { BarChart3 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PipelineTimingBreakdownView } from "@/components/pipeline/pipeline-timing-breakdown";
import type { ProjectTimingBreakdown } from "@/lib/pipeline/analytics-types";

export function PipelineTimingDialog({
  data,
}: {
  data: ProjectTimingBreakdown | null;
}) {
  if (!data || data.projectStatus !== "ready") return null;

  return (
    <Dialog>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm">
            <BarChart3 className="size-3.5" />
            Pipeline timing
          </Button>
        }
      />
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Pipeline timing</DialogTitle>
          <DialogDescription>
            Wall-clock time per stage for the latest run. Use this to find
            bottlenecks and timeouts.
          </DialogDescription>
        </DialogHeader>
        <PipelineTimingBreakdownView data={data} />
        <DialogFooter className="border-t-0 bg-transparent p-0 pt-2">
          <Button variant="link" size="sm" className="h-auto px-0" asChild>
            <Link href="/analytics">Compare all projects →</Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
