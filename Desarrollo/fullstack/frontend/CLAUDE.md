# kmp-repair-web — CLAUDE.md

## What this is

The Next.js 14 frontend for the KMP Repair Pipeline web UI.  It is a pure client
that consumes the REST + SSE API exposed by `fullstack/backend`.

---

## Rules

### Preserve UI and layout

- Do NOT change the overall visual design, color scheme, or layout without explicit instruction.
- Components in `components/` encapsulate the existing UI — modify only what is needed.
- Tailwind utility classes in `globals.css` and component files define the design system — do not reset or override them.
- The design system uses three font variables: `--font-display` (Epilogue), `--font-body` (Inter), `--font-technical` (Space Grotesk).

### API consumption

All backend calls go through `lib/api.ts`. Rules:
- Never call the API directly from page or component files — always use `lib/api.ts` functions.
- The API base URL is `process.env.NEXT_PUBLIC_API_BASE_URL` (set in `.env.local`).
- Do not hardcode `localhost:8000` — it must stay configurable.
- SSE endpoints are accessed via `jobSseUrl(jobId)` and `activeJobsSseUrl()` from `lib/api.ts`.

### TypeScript types

All API response shapes are typed in `lib/types.ts`. Keep them in sync with
the backend schemas. Do not use `any` for API responses.

### Vocabulary constants

All stage names, repair modes, targets, and providers are defined in `lib/constants.ts`.
All thesis-facing labels and context descriptions are in `lib/thesis-framework.ts`.
Do not hardcode these strings in page or component files.

### Adding new pages/features

1. Add new page under `app/`.
2. Add new API functions to `lib/api.ts`.
3. Add types to `lib/types.ts`.
4. Add display constants to `lib/constants.ts` or `lib/thesis-framework.ts` as appropriate.
5. Create components in `components/` if reusable.
6. Do not duplicate API call logic across files.
7. Add the new page to `SiteHeader.tsx` `NAV_ITEMS` if it should appear in navigation.

### Testing

- Unit tests use Vitest + @testing-library/react.
- E2E tests use Playwright (requires a running server).
- Run unit tests with `npm test` before committing UI changes.

---

## Navigation structure

`components/chrome/SiteHeader.tsx` defines `NAV_ITEMS`:

```
/           → Home
/process    → Process (ingest + run + live console)
/cases      → Case listing
/results    → Results / metrics dashboard
/environment → Environment readiness
/about      → Thesis framing (static)
```

`/reports` redirects to `/results` (legacy route).

---

## Backend API contract

The backend exposes these endpoints (defined in `fullstack/backend/src/kmp_repair_webapi/app.py`):

### REST endpoints

- `GET /api/health` — service health check
- `GET /api/environment` — runtime snapshot (DB, Python, Java, Android SDK, LLM, defaults)
- `POST /api/cases` — body: `{ pr_url, artifact_dir?, detection_source? }`
- `GET /api/cases` — query: `status, update_class, repo, repair_mode`
- `GET /api/cases/{case_id}` — full case detail + evidence
- `GET /api/cases/{case_id}/history` — job + status transition history
- `POST /api/cases/{case_id}/jobs/stage` — body: `{ stage, params, requested_by? }`
- `POST /api/cases/{case_id}/jobs/pipeline` — body: `{ start_from_stage?, params_by_stage?, requested_by? }`
- `GET /api/jobs/{job_id}` — job status
- `POST /api/jobs/{job_id}/cancel` — request cancellation
- `GET /api/jobs/{job_id}/logs` — tail job log file
- `GET /api/cases/{case_id}/artifact-content?path=...` — read artifact file content
- `GET /api/reports/compare?modes=...&case_id=...` — aggregated metrics

### SSE endpoints

- `GET /api/jobs/{job_id}/stream` — live status + log stream for a single job
- `GET /api/stream/active` — stream of currently QUEUED/RUNNING jobs

---

## Component responsibilities

### `components/chrome/SiteHeader.tsx`

Fixed navigation bar.  Consumes `usePathname` for active link highlighting.
Contains a fullscreen mobile menu overlay.  Client component.

### `components/LiveJobConsole.tsx`

SSE-based log viewer.  Props:
- `jobId: string | null` — job to stream; null shows empty state
- `onStatus?: (job: Job | null) => void` — called on every status SSE event
- `onStageDetected?: (stage: string) => void` — called when a log line reveals a stage

Parses log lines matching `YYYY-MM-DDTHH:mm:ss.sss [STAGE_NAME] message`.
Emits heartbeat keep-alive events from the backend are handled silently.

### `components/reports/ResultsD3Panel.tsx`

D3 bar-chart visualization panel.  Receives pre-fetched `ReportsComparisonRow[]`.
Does not call the API — parent page provides data.

---

## `lib/thesis-framework.ts` vs `lib/constants.ts`

| File | Contains |
|------|---------|
| `constants.ts` | Machine vocabulary: stage names, mode keys, targets, providers — must match backend exactly |
| `thesis-framework.ts` | Human-facing thesis labels, context window descriptions, retry budgets — safe to edit for thesis presentation |

---

## Development commands

```bash
npm install          # install deps
npm run dev          # dev server at http://localhost:3000
npm run build        # production build
npm run start        # serve production build
npm test             # unit tests
npm run test:e2e     # e2e tests (requires server)
```
