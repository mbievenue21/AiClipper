import Link from "next/link";
import { Scissors, Wrench } from "lucide-react";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <Scissors className="size-5" />
          <span>AiClipper</span>
        </Link>
        <nav className="flex items-center gap-1 text-sm text-muted-foreground">
          <Link
            href="/"
            className="rounded-md px-3 py-1.5 hover:bg-accent hover:text-foreground"
          >
            Projects
          </Link>
          <Link
            href="/accounts"
            className="rounded-md px-3 py-1.5 hover:bg-accent hover:text-foreground"
          >
            Accounts
          </Link>
          <Link
            href="/admin"
            className="flex items-center gap-1.5 rounded-md px-3 py-1.5 hover:bg-accent hover:text-foreground"
          >
            <Wrench className="size-3.5" />
            Worker
          </Link>
        </nav>
      </div>
    </header>
  );
}
