# kmp-repair-web — Frontend

Next.js 14 frontend for the KMP Repair Pipeline. Connects to the backend adapter
at `fullstack/backend` via a configurable API base URL.

---

## Stack

- **Framework:** Next.js 14 (App Router)
- **UI:** React 18 + Tailwind CSS
- **Charts:** @observablehq/plot
- **Testing:** Vitest (unit) + Playwright (e2e)
- **Language:** TypeScript

---

## Installation

```bash
cd fullstack/frontend
npm install
```

---

## Configuration

The only required configuration is the backend URL. Set it in `.env.local`:

```bash
# .env.local (already created with default)
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

For production or Docker deployments, set the environment variable to the actual backend host.

---

## Development

```bash
# Start dev server (hot reload)
npm run dev
# → http://localhost:3000
```

The backend must be running at `NEXT_PUBLIC_API_BASE_URL` (default: `http://localhost:8000`).

---

## Build

```bash
npm run build
npm run start
```

---

## Tests

```bash
# Unit tests (Vitest)
npm test

# E2E tests (Playwright — requires running dev or prod server)
npm run test:e2e
```

---

## End-to-end local stack

Start everything in order:

```bash
# 1. Infrastructure
cd ../backend && docker compose up -d

# 2. Canonical pipeline migrations
cd ../../kmp-repair-pipeline && alembic upgrade head

# 3. Backend API (terminal 1)
cd ../fullstack/backend
source .venv/bin/activate
kmp-repair-api

# 4. Backend worker (terminal 2)
kmp-repair-worker

# 5. Frontend (terminal 3)
cd ../frontend
npm run dev
```

Open http://localhost:3000.

---

## Key files

| File | Purpose |
|------|---------|
| `lib/api.ts` | All API calls to the backend |
| `lib/types.ts` | Shared TypeScript types |
| `app/page.tsx` | Home/dashboard page |
| `app/cases/[caseId]/page.tsx` | Case detail page |
| `app/reports/page.tsx` | Reports comparison page |
| `components/LiveJobConsole.tsx` | SSE-based live log viewer |
| `components/ActiveRunsStrip.tsx` | Active jobs strip (SSE) |
| `components/RunComposer.tsx` | UI to start pipeline/stage jobs |
