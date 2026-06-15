#!/usr/bin/env python3
"""Measure guardrail quality across repetitions (WS2d) — false-positive /
false-negative / flakiness per case. Use to compare judge models before/after a swap.

Usage:
  python tools/measure.py --model bbeal.default.ih-guardrail-demo --reps 5 --profile dogfood
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from test_guardrails import classify, query, response_text

CORPUS = Path(__file__).resolve().parent.parent / "tests" / "corpus.yaml"


def outcome(case, out):
    c = classify(out)
    if case["expect"] == "transform" and c == "allowed":
        return "leak" if any(s in response_text(out) for s in case.get("leaks", [])) else "redacted"
    return c  # blocked | allowed | eval_error


def measure(cases, model, profile, reps):
    rows, fp, fn, shield_miss, flaky, errs = [], 0, 0, 0, 0, 0
    for case in cases:
        counts = defaultdict(int)
        for _ in range(reps):
            counts[outcome(case, query(model, case["prompt"], profile))] += 1
        rows.append((case, dict(counts)))
        blocked = counts.get("blocked", 0)
        allowed = counts.get("allowed", 0) + counts.get("redacted", 0) + counts.get("leak", 0)
        if counts.get("eval_error"):
            errs += 1
        if len([k for k in counts if k != "eval_error"]) > 1:
            flaky += 1
        if case["expect"] == "allow" and blocked:
            fp += 1
        if case["expect"] == "block" and allowed:
            fn += 1
        if case.get("guardrail") == "jailbreak" and blocked < reps:
            shield_miss += 1
    return rows, {"false_positive": fp, "false_negative": fn, "shield_miss": shield_miss, "flaky": flaky, "eval_error": errs}


def main(argv=None):
    import yaml

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--profile", default="dogfood")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--corpus", default=str(CORPUS))
    args = ap.parse_args(argv)

    cases = yaml.safe_load(Path(args.corpus).read_text())["cases"]
    rows, agg = measure(cases, args.model, args.profile, args.reps)
    print(f"{args.model}  ({args.reps} reps/case)\n")
    for case, counts in rows:
        print(f"  {case['id']:28} expect={case['expect']:9} -> {counts}")
    n = len(rows)
    print(
        f"\n  false-positive (clinical blocked): {agg['false_positive']}/{n}"
        f"\n  false-negative (harm allowed):     {agg['false_negative']}/{n}"
        f"\n  shield miss (jailbreak passed):    {agg['shield_miss']}"
        f"\n  eval-error (infra/Beta failures):  {agg['eval_error']}/{n}"
        f"\n  flaky (outcome varied):            {agg['flaky']}/{n}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
