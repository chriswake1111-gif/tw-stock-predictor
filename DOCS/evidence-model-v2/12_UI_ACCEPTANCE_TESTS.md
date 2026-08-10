# Phase 9 UI Acceptance Tests

Automated frontend gates:

```text
npm run lint
npm run typecheck
npm run test
npm run build
```

Tests cover all statuses plus unknown fail-closed, v1 endpoint isolation, GET-only calls, semantic separation, FB-04 support placement, reconstruction labeling, numerator/denominator/n/horizon/origin and axe audits for key routes.

Manual visual acceptance uses deterministic v2 fixtures at 360px, 768px and 1280px. Backend gates remain full pytest, v1 golden, `git diff --check` and runtime-cache rejection.
