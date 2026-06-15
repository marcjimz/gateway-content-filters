# PoC Validation Results

**Date:** 2026-06-12 · **Endpoint:** `ih-guardrail-demo` (BUILDER) · **Judge:** `databricks-gpt-5-nano`

## What we tested

The full content-guardrail stack on a live v2 Unity AI Gateway endpoint (native Jailbreak + native PII + custom `healthcare-safety` + custom `phi`), via `tools/test_guardrails.py` against a 9-case corpus.

## Result (directional — PoC quality, not tuned)

| Behavior | Outcome |
|---|---|
| Harmful content (violence/hate/self-harm encouragement) | ✅ blocked |
| Prompt injection (direct + indirect) | ✅ blocked |
| Clinical content (surgery, suicide-risk *assessment*, repro health) | ✅ allowed (mostly — see flakiness) |
| PHI (name/MRN/DOB/phone) | ✅ redacted to `[NAME]/[MRN]/[DATE]/[PHONE]` |

The custom **`healthcare-safety` judge performed well** — it allowed clinical content and blocked actual violence, which is the differentiating "fewer exceptions" behavior.

## Known issues (accepted for PoC)

- **Native Jailbreak guardrail is non-deterministic and intermittently false-positives on clinical content** (~1 in 5–10 runs blocked legitimate appendectomy / suicide-assessment prompts). It also acts as a broad catch-all (blocked hate/self-harm before `healthcare-safety` evaluated).
- Root cause is almost certainly the small **`gpt-5-nano`** evaluator.

## Productionization levers (deferred — for IH's team)

Reliability is **not** a PoC goal; these are the knobs to pull for production:
1. **Stronger judge model** — swap evaluator from `gpt-5-nano` → e.g. `gpt-5-mini` / `claude-haiku-4-5` / larger. Per-guardrail `judge_endpoint` is already version-controlled in our specs.
2. **Threshold tuning** — if the native Jailbreak guardrail exposes a sensitivity threshold, raise it; if not tunable, consider a **custom prompt-shield judge** we can tune to ignore clinical framing.
3. **Prompt refinement** — iterate the custom judge prompts against real examples.
4. **Quantified eval** — `tools/measure.py` runs N reps and reports false-positive / false-negative / flakiness per guardrail; run it on a larger corpus to pick the judge model with data.
