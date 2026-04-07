"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";

type NavItem = {
  href: string;
  label: string;
};

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/process", label: "Process" },
  { href: "/cases", label: "Cases" },
  { href: "/results", label: "Results" },
  { href: "/environment", label: "Environment" },
  { href: "/about", label: "About" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  const activeItem = useMemo(
    () => NAV_ITEMS.find((item) => isActive(pathname, item.href)),
    [pathname],
  );

  return (
    <>
      <header className={clsx("fixed inset-x-0 top-0 z-50 border-b border-[var(--line-quiet)]", pathname === "/" ? "bg-[rgba(253,248,247,0.55)] backdrop-blur-xl" : "bg-[rgba(253,248,247,0.82)] backdrop-blur-xl")}
      >
        <div className="page-shell flex h-[4.5rem] items-center justify-between">
          <div className="flex items-center gap-8">
            <Link href="/" className="focus-ring display-font text-[1.65rem] font-extrabold tracking-tighter text-[var(--ink)]">
              kmp-repair
            </Link>

            <nav className="hidden items-center gap-7 md:flex">
              {NAV_ITEMS.map((item) => {
                const active = isActive(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={clsx(
                      "focus-ring link-underline pb-1 text-[0.82rem] font-medium tracking-tight",
                      active ? "active text-[var(--ink)]" : "text-[var(--muted)] hover:text-[var(--ink)]",
                    )}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              aria-label="Abrir menu"
              onClick={() => setMenuOpen((prev) => !prev)}
              className="focus-ring rounded-md border border-[var(--line)] bg-white/70 p-2.5 text-[var(--ink)]"
            >
              <span className="block h-[1.5px] w-4 bg-current" />
              <span className="mt-1 block h-[1.5px] w-4 bg-current" />
              <span className="mt-1 block h-[1.5px] w-4 bg-current" />
            </button>
          </div>
        </div>
      </header>

      {menuOpen ? (
        <div className="glass-overlay fixed inset-0 z-[60] flex items-center justify-center px-6 py-10">
          <div className="relative w-full max-w-3xl rounded-xl border border-white/20 bg-[rgba(16,17,17,0.85)] p-8 shadow-[0_24px_70px_rgba(0,0,0,0.45)]">
            <button
              type="button"
              onClick={() => setMenuOpen(false)}
              className="focus-ring absolute right-4 top-4 rounded-md border border-white/20 px-3 py-1.5 text-xs uppercase tracking-[0.2em] text-white/85"
            >
              Close
            </button>

            <p className="technical-font mb-8 text-[0.65rem] text-white/55">kmp-repair navigation</p>

            <div className="grid gap-4 sm:grid-cols-2">
              {NAV_ITEMS.map((item) => {
                const active = item.href === activeItem?.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMenuOpen(false)}
                    className={clsx(
                      "focus-ring rounded-lg border px-5 py-4 text-2xl font-bold tracking-tight transition",
                      active
                        ? "border-white/70 bg-white/10 text-white"
                        : "border-white/20 bg-white/[0.04] text-white/80 hover:bg-white/[0.09]",
                    )}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </div>

          </div>
        </div>
      ) : null}
    </>
  );
}
