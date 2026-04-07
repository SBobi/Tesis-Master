# kmp-repair-web — CLAUDE.md

## What this is

The Next.js frontend for the KMP Repair Pipeline web UI. It is a pure client
that consumes the REST + SSE API exposed by `fullstack/backend`.

---

## Rules

### Preserve UI and layout

- Do NOT change the overall visual design, color scheme, or layout without explicit instruction.
- Components in `components/` encapsulate the existing UI — modify only what is needed.
- Tailwind utility classes in `globals.css` and component files define the design — do not reset them.

### API consumption

All backend calls go through `lib/api.ts`. Rules:
- Never call the API directly from page or component files — always use `lib/api.ts` functions.
- The API base URL is `process.env.NEXT_PUBLIC_API_BASE_URL` (set in `.env.local`).
- Do not hardcode `localhost:8000` — it must stay configurable.
- SSE endpoints are `jobSseUrl()` and `activeJobsSseUrl()` from `lib/api.ts`.

### TypeScript types

All API response shapes are typed in `lib/types.ts`. Keep them in sync with
the backend schemas. Do not use `any` for API responses.

### Adding new pages/features

1. Add new page under `app/`.
2. Add new API functions to `lib/api.ts`.
3. Add types to `lib/types.ts`.
4. Create components in `components/` if reusable.
5. Do not duplicate API call logic across files.

### Testing

- Unit tests use Vitest + @testing-library/react.
- E2E tests use Playwright (requires a running server).
- Run unit tests with `npm test` before committing UI changes.

---

## Backend API contract

The backend exposes these endpoints (defined in `fullstack/backend/src/kmp_repair_webapi/app.py`):

- `GET /api/health`
- `POST /api/cases` — body: `{ pr_url, artifact_dir?, detection_source? }`
- `GET /api/cases` — query: `status, update_class, repo, repair_mode`
- `GET /api/cases/{case_id}`
- `GET /api/cases/{case_id}/history`
- `POST /api/cases/{case_id}/jobs/stage` — body: `{ stage, params, requested_by? }`
- `POST /api/cases/{case_id}/jobs/pipeline` — body: `{ start_from_stage?, params_by_stage?, requested_by? }`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/jobs/{job_id}/logs`
- `GET /api/jobs/{job_id}/stream` (SSE)
- `GET /api/stream/active` (SSE)
- `GET /api/cases/{case_id}/artifact-content?path=...`
- `GET /api/reports/compare?modes=...`

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
