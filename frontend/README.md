# Phase 9 Evidence Workspace

Read-only React + TypeScript + Vite client for Evidence Model V2.

```powershell
npm ci
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
```

Development proxies `/api` to `http://127.0.0.1:8000`. Production output is served by FastAPI from `frontend/dist` with SPA deep-link fallback. No Vite environment secret is required or supported.

The client may format server values, but must not calculate financial results, infer statuses, select approvals, call writes, use current-price fallback, fetch external financial data, register service workers or consume legacy `/api/analysis/*` DTOs.
