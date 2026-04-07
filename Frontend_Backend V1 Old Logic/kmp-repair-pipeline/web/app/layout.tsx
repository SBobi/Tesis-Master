import type { Metadata } from "next";
import Link from "next/link";
import { Manrope, Space_Grotesk } from "next/font/google";
import "./globals.css";

const display = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["500", "600", "700"],
});

const body = Manrope({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "KMP Repair Pipeline Web",
  description: "Minimal tool UI to run repair pipelines, monitor jobs, and review evidence.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${display.variable} ${body.variable} antialiased`}>
        <div className="mx-auto w-full max-w-[1280px] px-5 pb-24 pt-6 sm:px-8">
          <header className="mb-8 flex flex-wrap items-center justify-between gap-5 rounded-2xl border border-[var(--color-border)] bg-[var(--color-frost)] px-4 py-3 shadow-warm backdrop-blur-md">
            <Link href="/" className="ring-focus display-serif text-2xl font-semibold text-ink no-underline">
              KMP Repair Pipeline
            </Link>
            <nav className="flex items-center gap-2 text-sm text-muted">
              <Link href="/" className="ring-focus rounded-full border border-[var(--color-border)] bg-white/70 px-4 py-2 no-underline transition-colors duration-200 hover:border-terracotta hover:text-ink">
                Home
              </Link>
              <Link href="/cases" className="ring-focus rounded-full border border-[var(--color-border)] bg-white/70 px-4 py-2 no-underline transition-colors duration-200 hover:border-terracotta hover:text-ink">
                Cases
              </Link>
              <Link href="/reports" className="ring-focus rounded-full border border-[var(--color-border)] bg-white/70 px-4 py-2 no-underline transition-colors duration-200 hover:border-terracotta hover:text-ink">
                Reports
              </Link>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
