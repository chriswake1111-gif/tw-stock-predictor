# Evidence Model V2 Phase 7 Acceptance Evidence

## Baseline

- Base main: `98c9b9962f17dd22a0079abb1470a9f597edb8a0`
- Main CI: `31310023141` success
- Baseline regression: 268 passed
- Baseline runtime cache gate: pass

## Implemented scope

- Immutable TGT-01 synthesis profile revisions and append-only approvals.
- Trusted VAL-01/FB-03 target and FB-04 support adapters.
- Deterministic maximal-overlap clusters and connected-component dependency counting.
- Profile-controlled Decimal tolerance, precision, and evidence-strength mapping.
- Side-effect-free GET analysis plus protected explicit refresh persistence.
- Exact immutable snapshot retrieval with semantic fingerprint and output SHA-256.
- SQLite UPDATE and DELETE rejection triggers for `analysis_snapshots`.
- Lossless contributing resource IDs/revisions/approval provenance for stored sections.

Phase 8 evaluation, prediction scoring, Phase 9 UI, automatic orders, and broker integration were
not implemented.

## Guardrail evidence

- A single target family produces no confluence, null strength, and no TGT-01 Rule Trace.
- Strength thresholds below two independent target components are rejected.
- `support_count` counts target families rather than matrix cells; FB-04 is cross-role only.
- Explicit old profile revisions cannot bypass a newer cutoff-visible draft or revoked revision.
- All disjoint clusters remain in output; summary policy is `maximum_cluster_strength` and no
  recommended/primary target field exists.
- Direct SQL UPDATE and DELETE tests both receive `analysis_snapshots are immutable`.

## Commands and results

```text
python -m pytest -q -p no:cacheprovider --basetemp <external-test-temp>
290 passed, 1 existing StarletteDeprecationWarning

python -m pytest -q -p no:cacheprovider --basetemp <external-test-temp> tests/test_api_golden.py
2 passed, 1 existing StarletteDeprecationWarning

git diff --check
PASS

git ls-files | Select-String -Pattern '(^|/)(__pycache__/|data/cache\.db$)|\.py[co]$'
no output; PASS
```

The local warning category is unchanged from the baseline local environment. GitHub Actions is
recorded after the Draft PR run.

## Workspace safety

The user-owned untracked specification package remains untracked, unstaged, and unchanged. Its
SHA-256 is:

```text
5787905643BD0C3D13822504BC251DC01E48103FAF482315C741D764DE6AAF22
```
