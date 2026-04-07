import Link from "next/link";

import { ActiveRunsStrip } from "@/components/ActiveRunsStrip";
import { SectionReveal } from "@/components/SectionReveal";

const quickTools = [
  {
    title: "Cases workspace",
    text: "Create and review repair cases with execution context.",
    href: "/cases",
    image: "/images/engineering-desk.jpg",
    imageAlt: "Engineering desk with a laptop and notes",
  },
  {
    title: "Live runs",
    text: "Watch active jobs and current phase in real time.",
    href: "/cases",
    image: "/images/server-room.jpg",
    imageAlt: "Server hardware and network infrastructure",
  },
  {
    title: "Reports",
    text: "Explore repair modes and metric comparisons.",
    href: "/reports",
    image: "/images/planning-wall.jpg",
    imageAlt: "Technical planning board in an office",
  },
];

export default function HomePage() {
  return (
    <div className="space-y-10">
      <SectionReveal>
        <section className="hero-panel relative overflow-hidden rounded-[2.4rem] p-6 sm:p-8 lg:p-10">
          <div className="hero-glow" aria-hidden />
          <div className="relative grid gap-7 lg:grid-cols-[1.05fr_0.95fr] lg:items-stretch">
            <div className="space-y-5">
              <p className="kicker">KMP Repair Pipeline</p>
              <h1 className="display-serif max-w-3xl text-5xl leading-[0.95] sm:text-6xl lg:text-[4rem]">
                Operate repair jobs from one workspace.
              </h1>
              <p className="max-w-2xl text-sm leading-relaxed text-muted sm:text-base">
                Start pipeline runs, watch the current phase, and review evidence with minimal overhead.
              </p>
              <div className="flex flex-wrap gap-3">
                <Link
                  href="/cases"
                  className="ring-focus rounded-full bg-terracotta px-6 py-3 text-sm font-semibold text-white no-underline shadow-warm transition-transform duration-300 hover:-translate-y-0.5 hover:brightness-95"
                >
                  New case
                </Link>
                <Link
                  href="/reports"
                  className="ring-focus rounded-full border border-[var(--color-border)] bg-white/70 px-6 py-3 text-sm font-semibold no-underline transition-colors duration-300 hover:border-terracotta"
                >
                  View reports
                </Link>
              </div>
            </div>

            <div className="grid gap-4">
              <figure className="photo-card rounded-3xl min-h-[20rem]">
                <img
                  src="/images/engineering-desk.jpg"
                  alt="Case workspace"
                  loading="eager"
                  decoding="async"
                />
                <figcaption>Cases workspace</figcaption>
              </figure>
            </div>
          </div>
        </section>
      </SectionReveal>

      <SectionReveal>
        <ActiveRunsStrip />
      </SectionReveal>

      <SectionReveal>
        <section className="grid gap-4 md:grid-cols-3">
          {quickTools.map((tool) => (
            <Link
              key={tool.title}
              href={tool.href}
              className="tool-link-card ring-focus rounded-3xl border border-[var(--color-border)] bg-white/86 no-underline shadow-warm"
            >
              <div className="tool-link-image">
                <img src={tool.image} alt={tool.imageAlt} loading="lazy" decoding="async" />
              </div>
              <div className="p-4">
                <h3 className="display-serif text-2xl leading-tight">{tool.title}</h3>
                <p className="mt-1 text-sm text-muted">{tool.text}</p>
                <span className="mt-3 inline-block text-xs font-semibold uppercase tracking-[0.1em] text-terracotta">
                  Open
                </span>
              </div>
            </Link>
          ))}
        </section>
      </SectionReveal>
    </div>
  );
}
