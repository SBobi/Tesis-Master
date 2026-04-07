# kmp-repair-web — Frontend

Next.js 14 editorial frontend for the KMP Repair Pipeline.  Connects to the
backend adapter at `fullstack/backend` via a configurable API base URL.

---

## Stack

- **Framework:** Next.js 14 (App Router)
- **UI:** React 18 + Tailwind CSS (custom design system)
- **Charts:** @observablehq/plot + D3
- **Testing:** Vitest (unit) + Playwright (e2e)
- **Language:** TypeScript (strict)
- **Fonts:** Epilogue (display), Inter (body), Space Grotesk (technical)

---

## Installation

```bash
cd fullstack/frontend
npm install
```

---

## Configuration

```bash
# .env.local (already created with default)
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

For production or Docker deployments, set this to the actual backend host.

---

## Development

```bash
npm run dev      # hot-reload dev server → http://localhost:3000
npm run build    # production build
npm run start    # serve production build
```

The backend must be running at `NEXT_PUBLIC_API_BASE_URL`.

---

## Tests

```bash
npm test              # unit tests (Vitest)
npm run test:e2e      # e2e tests (Playwright — requires running server)
```

---

## End-to-end local stack

```bash
# 1. Infrastructure
cd ../backend && docker compose up -d

# 2. DB migrations
cd ../../kmp-repair-pipeline && alembic upgrade head

# 3. Backend API (terminal 1)
cd fullstack/backend && kmp-repair-api

# 4. Worker (terminal 2)
kmp-repair-worker

# 5. Frontend (terminal 3)
cd fullstack/frontend && npm run dev
```

Open http://localhost:3000.

---

## Navigation structure

| Route | Page | Purpose |
|-------|------|---------|
| `/` | Home | Hero + active pipeline status (SSE) + 3 recent cases + repair-mode cards |
| `/process` | Process | Ingest PR URL, auto-select new ingested case in selector, run pipeline, live console (SSE), 9-step process timeline |
| `/cases` | Cases | Full case listing with update-class filter presets |
| `/cases/[caseId]` | Case Detail | Evidence timeline, patch diffs, validation matrix, agent logs |
| `/results` | Results | Aggregated metrics comparison (D3 charts), CSV/JSON/MD export |
| `/environment` | Environment | Runtime health checks + backend configuration snapshot |
| `/about` | About | Static thesis framing (problem, contribution, methodology, scope) |
| `/reports` | Redirect | Redirects to `/results` |

### Process page ingest UX

- `Ingest PR URL` keeps the user on `/process` (no auto-redirect to case detail).
- After ingest succeeds, the new case is inserted at the top of the case selector and auto-selected.
- Case selector labels prioritize `pr_title`; fallback is `owner/repo - PR #N`.

---

## Key source files

### Pages (`app/`)

| File | Description |
|------|-------------|
| `app/layout.tsx` | Root layout: fonts, `SiteHeader`, global CSS |
| `app/page.tsx` | Home: hero with live pipeline status, recent cases, repair-mode section |
| `app/process/page.tsx` | Operational center: ingest + run forms, pipeline step timeline, live console |
| `app/cases/page.tsx` | Case listing with update-class filter presets |
| `app/cases/[caseId]/page.tsx` | Case detail: full evidence, timeline, diffs, jobs |
| `app/results/page.tsx` | Metrics dashboard: benchmark-mode cards, metric framework, aggregated table, D3 charts, export |
| `app/environment/page.tsx` | Runtime snapshot: health checks, path/LLM config, execution defaults |
| `app/about/page.tsx` | Static thesis framing page |
| `app/reports/page.tsx` | Redirects to `/results` |

### Components (`components/`)

| File | Description |
|------|-------------|
| `components/chrome/SiteHeader.tsx` | Fixed-top navigation bar: 6 nav items, blur background, mobile fullscreen menu |
| `components/chrome/SiteFooter.tsx` | Site footer |
| `components/LiveJobConsole.tsx` | SSE-based live log viewer: parses log lines, detects stage transitions, emits `onStatus` + `onStageDetected` callbacks |
| `components/ActiveRunsStrip.tsx` | SSE strip of QUEUED/RUNNING jobs (listens to `/api/stream/active`) |
| `components/RunComposer.tsx` | UI to enqueue pipeline or single-stage jobs |
| `components/Timeline.tsx` | Case execution timeline component |
| `components/case/UnifiedDiffViewer.tsx` | Patch diff viewer |
| `components/reports/ResultsD3Panel.tsx` | D3 bar chart panel for metric comparison across repair modes |
| `components/reports/ReportsPlots.tsx` | Observable Plot charts |
| `components/SectionReveal.tsx` | Scroll-reveal animation wrapper |

### Library (`lib/`)

| File | Description |
|------|-------------|
| `lib/api.ts` | All backend API calls — never call the backend from pages/components directly |
| `lib/types.ts` | TypeScript types for all API response shapes |
| `lib/constants.ts` | Vocabulary constants: `PIPELINE_STAGES`, `REPAIR_MODES`, `TARGETS`, `PROVIDERS`, etc. |
| `lib/thesis-framework.ts` | Thesis-facing constants: repair mode labels, context descriptions, retry budgets, core principle |
| `lib/format.ts` | Display formatters: `formatDate`, `shortId`, `metric` |
| `lib/ui.ts` | Display label helpers: `stageLabel`, `stageStatusTone`, `caseStatusLabel`, `validationLabel` |

---

## Design system

Custom Tailwind CSS design system defined in `globals.css` and `tailwind.config.ts`.

Key utility classes:
- `.page-shell` — max-width container with horizontal padding
- `.editorial-title` — large display title font
- `.technical-font` — monospace / technical uppercase labels
- `.display-font` — Epilogue display font
- `.surface-card` / `.surface-card-dark` — card surfaces
- `.button-primary` / `.button-ghost` — action buttons
- `.pill` — status/category chip
- `.dot` / `.dot-ok` / `.dot-warn` / `.dot-bad` — status indicator dots
- `.focus-ring` — accessible focus ring
- `.eyebrow` — section label (small uppercase tracking)
- `.metric-track` / `.metric-fill` — metric progress bar

---

## SSE (Server-Sent Events)

Two SSE streams are consumed:

| Stream | URL | Used in |
|--------|-----|---------|
| Job stream | `/api/jobs/{job_id}/stream` | `LiveJobConsole`, case detail page |
| Active jobs | `/api/stream/active` | Home page hero, `ActiveRunsStrip` |

URLs come from `lib/api.ts` helpers `jobSseUrl(jobId)` and `activeJobsSseUrl()`.
Never hardcode SSE URLs.
