#!/usr/bin/env python3
"""Validate gateway policy invariants (CI gate). Exit non-zero on any violation.

Invariants:
  1. Every endpoint policy references a known guardrail; endpoint has parent/destinations/judge.
  2. custom guardrails have a non-empty prompt_file + action; native (pii/jailbreak) have neither.
  3. mode in {enforce, annotate}; phases a non-empty subset of {input, output}.
  4. Prompt shields (enforcement: always_block) are NEVER annotate.
  5. Relaxing a guardrail flagged relaxation_requires_exemption (annotate) needs a
     matching, NON-EXPIRED entry in exceptions/register.yaml.
  6. Exemptions reference real endpoints and guardrails.

Usage: python tools/validate.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime

ALLOWED_MODES = {"enforce", "annotate"}
ALLOWED_PHASES = {"input", "output"}
ALLOWED_TYPES = {"custom", "pii", "jailbreak"}


def _parse_date(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        return None


def validate(policy, today=None):
    today = today or date.today()
    errors = []
    guardrails = policy["guardrails"]
    endpoints = policy["endpoints"]
    prompts = policy["prompts"]
    exmap = {(x.get("endpoint"), x.get("guardrail")): x for x in policy["exemptions"]}

    for name, gd in guardrails.items():
        t = gd.get("type")
        if t not in ALLOWED_TYPES:
            errors.append(f"guardrail '{name}': type must be in {sorted(ALLOWED_TYPES)}, got {t!r}")
        if t == "custom":
            pf = gd.get("prompt_file")
            if pf not in prompts or not prompts.get(pf, "").strip():
                errors.append(f"guardrail '{name}': custom guardrail needs a non-empty prompt_file (got {pf!r})")
            if gd.get("action") not in ("block", "transform"):
                errors.append(f"guardrail '{name}': custom guardrail action must be block|transform")
        else:  # native
            if gd.get("prompt_file") or gd.get("action"):
                errors.append(f"guardrail '{name}': native guardrail must NOT set prompt_file/action")

    for ename, ep in endpoints.items():
        for k in ("parent", "destinations", "judge"):
            if not ep.get(k):
                errors.append(f"endpoint '{ename}': missing required field '{k}'")
        for b in ep.get("policies", []):
            ref = b.get("ref")
            gd = guardrails.get(ref)
            if gd is None:
                errors.append(f"endpoint '{ename}': policy references unknown guardrail '{ref}'")
                continue
            if b.get("mode") not in ALLOWED_MODES:
                errors.append(f"endpoint '{ename}'/{ref}: mode must be in {sorted(ALLOWED_MODES)}, got {b.get('mode')!r}")
            phases = b.get("phases") or []
            if not phases or any(p not in ALLOWED_PHASES for p in phases):
                errors.append(f"endpoint '{ename}'/{ref}: phases must be a non-empty subset of {sorted(ALLOWED_PHASES)}, got {phases!r}")

            if gd.get("enforcement") == "always_block" and b.get("mode") != "enforce":
                errors.append(f"endpoint '{ename}'/{ref}: prompt-shield must be enforce, never annotate")

            if gd.get("relaxation_requires_exemption") and b.get("mode") == "annotate":
                ex = exmap.get((ename, ref))
                if ex is None:
                    errors.append(f"endpoint '{ename}'/{ref}: annotate relaxation requires an exemption in exceptions/register.yaml")
                else:
                    d = _parse_date(ex.get("expires_at"))
                    if d is None:
                        errors.append(f"exemption {ename}/{ref}: expires_at missing/invalid ({ex.get('expires_at')!r})")
                    elif d < today:
                        errors.append(f"exemption {ename}/{ref}: EXPIRED on {ex.get('expires_at')} — renew or remove the relaxation")

    for (en, gn), _ in exmap.items():
        if en not in endpoints:
            errors.append(f"exemption references unknown endpoint '{en}'")
        if gn not in guardrails:
            errors.append(f"exemption references unknown guardrail '{gn}'")

    return errors


def main():
    from _policy import load

    policy = load()
    errors = validate(policy)
    if errors:
        print("FAIL — guardrail policy violations:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(
        f"OK — {len(policy['endpoints'])} endpoints, "
        f"{len(policy['guardrails'])} guardrails, "
        f"{len(policy['exemptions'])} exemptions validated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
