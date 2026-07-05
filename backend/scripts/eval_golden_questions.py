"""
Golden-question eval harness — measures retrieval accuracy offline.

Runs the Graph RAG retriever against cached schemas for a fixed set of
questions with known-correct tables, and reports top-1 / top-3 accuracy.
Run this after ANY change to graph_rag_retriever.py, schema_cache.py, or the
retrieval weights, so regressions are caught before users hit them.

Usage (from backend/):
    ./venv/bin/python scripts/eval_golden_questions.py
    ./venv/bin/python scripts/eval_golden_questions.py --verbose
Zero LLM calls, zero DB connections — runs entirely from cached schema files.
Exit code 1 when top-3 accuracy drops below the threshold.
"""
import argparse
import glob
import json
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from agent_service.agents.schema_cache import _deserialize_enriched  # noqa: E402
from agent_service.agents import graph_rag_retriever as grr          # noqa: E402

TOP3_PASS_THRESHOLD = 0.80


class _FakeIntent:
    metrics: list = []
    entities: list = []


def run_suite(suite: dict, verbose: bool) -> tuple[int, int, int]:
    """Returns (n_questions, top1_hits, top3_hits)."""
    matches = glob.glob(str(BACKEND_DIR / suite["cache_glob"]))
    if not matches:
        print(f"  ⚠ SKIP — no cache file matches {suite['cache_glob']}")
        return 0, 0, 0
    with open(matches[0]) as f:
        enriched = _deserialize_enriched(f.read())

    top1 = top3 = 0
    questions = suite["questions"]
    for item in questions:
        q, expected = item["q"], {t.lower() for t in item["expected_tables"]}
        ctx = grr.retrieve(q, _FakeIntent(), enriched, top_k=5)
        got = [t.lower() for t in (ctx.primary_tables or [])]
        hit1 = bool(got) and got[0] in expected
        hit3 = any(t in expected for t in got[:3])
        top1 += hit1
        top3 += hit3
        mark = "✓" if hit3 else "✗"
        if verbose or not hit3:
            print(f"  {mark} [{'top1' if hit1 else ('top3' if hit3 else 'MISS')}] {q}")
            if not hit3:
                print(f"      expected: {sorted(expected)}")
                print(f"      got:      {got[:3]}")
    return len(questions), top1, top3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--golden", default=str(BACKEND_DIR / "scripts" / "golden_questions.json"))
    args = parser.parse_args()

    with open(args.golden) as f:
        golden = json.load(f)

    total = t1 = t3 = 0
    for suite in golden["suites"]:
        print(f"\n=== suite: {suite['name']} ===")
        n, s1, s3 = run_suite(suite, args.verbose)
        total += n
        t1 += s1
        t3 += s3

    if total == 0:
        print("\nNo questions evaluated (no cache files found).")
        return 1

    acc1, acc3 = t1 / total, t3 / total
    print(f"\n{'=' * 50}")
    print(f"TOTAL: {total} questions  |  top-1: {t1} ({acc1:.0%})  |  top-3: {t3} ({acc3:.0%})")
    if acc3 < TOP3_PASS_THRESHOLD:
        print(f"FAIL — top-3 accuracy below {TOP3_PASS_THRESHOLD:.0%} threshold")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
