# AGENTS.md 建議追加段落

## Evidence-Based Model Rules

The Repository contains legacy implementations that were created before source verification. Their presence in code does not prove they are part of Du Jinlong's verified public method.

### Required evidence modes

Every model rule must be classified as:

- `verified_core`: A-level evidence and reproducible.
- `parameterized_support`: B-level evidence or incomplete decision rule.
- `project_operationalization`: C-level engineering definition.
- `legacy_experimental`: U-level or unverified existing behavior.
- `unsupported`: insufficient public formula or data.

### Forbidden promotions

The following must not be represented as verified Du Jinlong rules:

- Fibonacci MA periods 8/13/21/55/144/233.
- Fixed moving-average resonance.
- Fixed 20/30/50 allocation.
- Universal 7%–11% pullback buy rule.
- Fixed TWD 2.5 trillion market-top volume.
- Automatic EVA floor valuation.
- Fixed two-times-volume long-black delay rule.
- Automatic wave counting as a unique answer.

### Forward EPS

Historical TTM EPS must never be relabeled or silently converted into Forward EPS. A constant growth fallback must not produce a verified Forward EPS value.

### Human-in-the-loop

Wave anchors, wave degree, market phase, multiplier selection, company-specific PE and invalidation conditions require an explicit approval record unless a later verified specification removes that requirement.

### Versioning

Model re-labeling, anchor changes, EPS revisions and PE changes must create new immutable analysis versions. Never overwrite the original prediction.

### Output language

Use scenarios, ranges, evidence strength and invalidation conditions. Do not use guaranteed or official-affiliation language.

### Development priority

Complete evidence registry, data contracts, as-of correctness, model versioning and acceptance tests before adding outer report, news, PWA or multi-user features.
