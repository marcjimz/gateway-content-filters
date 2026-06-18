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

- **Output-phase (`post_call`) policy evaluation is flaky on the target workspace Beta.** Longer clinical
  generations intermittently fail with `Response evaluation failed for output policy '<name>'`
  — observed across `pii` (native `mask_pii`), `phi`, and `healthcare-safety` (LLM judges),
  interchangeably across retries. It's an output-evaluation **infra** failure, not a guardrail
  decision or a config issue. **Input-phase is reliable** (blocks + redaction all correct).
  Workaround for clean demos: bind content policies **input-only**. Trade-off: loses output-side
  moderation (catching harmful *model output*). **Logged as product feedback.**
- (Earlier, on BUILDER w/ `gpt-5-nano` judge) the native Jailbreak guardrail also non-deterministically
  false-positived on clinical content — mitigated by using a stronger judge (`gpt-5-2` on the target workspace).

## Productionization levers (deferred — for the customer's team)

Reliability is **not** a PoC goal; these are the knobs to pull for production:
1. **Stronger judge model** — swap evaluator from `gpt-5-nano` → e.g. `gpt-5-mini` / `claude-haiku-4-5` / larger. Per-guardrail `judge_endpoint` is already version-controlled in our specs.
2. **Threshold tuning** — if the native Jailbreak guardrail exposes a sensitivity threshold, raise it; if not tunable, consider a **custom prompt-shield judge** we can tune to ignore clinical framing.
3. **Prompt refinement** — iterate the custom judge prompts against real examples.
4. **Quantified eval** — `tools/measure.py` runs N reps and reports false-positive / false-negative / flakiness per guardrail; run it on a larger corpus to pick the judge model with data.
