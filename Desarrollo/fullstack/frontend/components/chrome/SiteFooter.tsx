import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-[var(--line-quiet)] bg-[var(--surface-low)]">
      <div className="page-shell flex flex-col items-start justify-between gap-4 py-10 md:flex-row md:items-center">
        <p className="technical-font text-[0.58rem] text-[var(--muted)]">
          © 2026 KMP-REPAIR THESIS. ALL RIGHTS RESERVED.
        </p>

        <div className="flex flex-wrap items-center gap-8">
          <Link href="/results" className="technical-font focus-ring text-[0.58rem] text-[var(--muted)] hover:text-[var(--ink)]">
            Documentation
          </Link>
          <Link href="/process" className="technical-font focus-ring text-[0.58rem] text-[var(--muted)] hover:text-[var(--ink)]">
            Changelog
          </Link>
          <Link href="/about" className="technical-font focus-ring text-[0.58rem] text-[var(--muted)] hover:text-[var(--ink)]">
            Privacy
          </Link>
        </div>
      </div>
    </footer>
  );
}
