"""NFR3 latency report over the `request_log` table.

    .venv/bin/python nfr3_report.py             # report on what has been logged
    .venv/bin/python nfr3_report.py --bench 10  # generate samples first, then report

NFR3 targets: mean response time within 10s, 95th percentile within 20s.

The report separates total time from model time so a regression can be attributed:
if the mean climbs but model time is flat, the cost is in tools or orchestration.
Rows are also grouped by model, because a fallback answer is slower by construction and
should be visible rather than quietly skewing the headline figure.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RequestLog  # noqa: E402

MEAN_TARGET_S = 10.0
P95_TARGET_S = 20.0

BENCH_QUERIES = [
    ("Hello!", []),
    # Phase 3a: the retrieval route, which costs two model calls plus a SPARQL query.
    ("Has my child missed any school?", []),
    ("What homework is set this week?", []),
    ("What is 15% of 320?", ["calculator"]),
    ("What is 450 minus 200, then multiplied by 0.15?", ["calculator"]),
    ("What does the handbook say about half term?", ["search", "file_reader"]),
    ("Work out the first 10 square numbers", ["code_runner"]),
]


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile — no interpolation, so small samples stay honest."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
    return ordered[index]


def run_benchmark(count: int) -> None:
    """Send `count` turns through the runtime, writing a request_log row for each."""
    from app.agent.local_runtime import LocalModelRuntime
    from app.agent.runtime import AgentInvocation, RoleContext
    from app.models import RequestLog as Log

    db = SessionLocal()

    # Attach the graph so retrieval queries actually measure retrieval. Without this the
    # runtime short-circuits to "unavailable" and the branch most likely to be slow never
    # appears in the figures.
    from app.graph import FusekiClient
    from app.graph.retrieval import RetrievalService
    from app.models import AppUser
    from sqlalchemy import select as _select

    fuseki = FusekiClient()
    retrieval = RetrievalService(fuseki, db) if fuseki.is_available() else None
    if retrieval is None:
        print("  (Fuseki not running: retrieval queries will be skipped)\n")

    # Benchmark as a real signed-in teacher, so the role scope is applied as in production.
    bench_user = db.scalar(
        _select(AppUser).where(AppUser.email == "teacher@greenfield.edu")
    )

    runtime = LocalModelRuntime(retrieval=retrieval)
    role = RoleContext(
        user_id=bench_user.id if bench_user else 0,
        full_name=bench_user.full_name if bench_user else "Benchmark",
        role=bench_user.role.value if bench_user else "teacher",
        email=bench_user.email if bench_user else "",
    )

    print(f"Running {count} benchmark queries...\n")
    try:
        for index in range(count):
            message, tools = BENCH_QUERIES[index % len(BENCH_QUERIES)]
            invocation = AgentInvocation(
                agent_id=0,
                agent_name="Benchmark agent",
                role_instructions="Answer school staff questions.",
                tools=tools,
            )
            started = time.perf_counter()
            reply = runtime.generate(
                invocation=invocation, history=[], message=message, role=role
            )
            total_s = time.perf_counter() - started
            meta = reply.meta

            db.add(
                Log(
                    session_id=None,
                    agent_id=None,
                    user_id=None,
                    role=role.role,
                    route=str(meta.get("route", "unknown"))[:20],
                    model_requested=str(meta.get("model_requested", ""))[:100],
                    model_used=str(meta.get("model_used", ""))[:100],
                    used_fallback=bool(meta.get("used_fallback", False)),
                    total_ms=int(total_s * 1000),
                    model_ms=int(float(meta.get("model_s", 0.0)) * 1000),
                    tool_ms=int(float(meta.get("tool_s", 0.0)) * 1000),
                    iterations=int(meta.get("iterations", 0)),
                    tool_calls=len(meta.get("tool_calls", []) or []),
                    stop_reason=str(meta.get("stop_reason", ""))[:50] or None,
                    succeeded=bool(meta.get("succeeded", True)),
                )
            )
            db.commit()
            print(f"  {index + 1:2}/{count}  {total_s:5.1f}s  {meta.get('route'):9} "
                  f"{meta.get('model_used')}  {message[:40]}")
    finally:
        db.close()
    print()


def report() -> int:
    db = SessionLocal()
    try:
        rows = db.scalars(select(RequestLog)).all()
    finally:
        db.close()

    # Only requests that actually invoked a model belong in a latency figure.
    #
    # Phase 2 excluded the whole retrieval route because it was an instant placeholder.
    # Phase 3a made it two real model calls (choose a template, then phrase the rows), so
    # excluding it now would understate latency in exactly the branch most likely to be
    # slow. The test is therefore "did a model answer this", not "which route was it".
    def is_model_backed(row) -> bool:
        return (row.model_used or "") not in ("", "none")

    measured = [r for r in rows if is_model_backed(r)]

    if not measured:
        print("No model-backed requests logged yet. Run with --bench 10 to generate some.")
        return 0

    totals = [r.total_ms / 1000 for r in measured]
    models = [r.model_ms / 1000 for r in measured]
    tools = [r.tool_ms / 1000 for r in measured]

    mean_total = statistics.fmean(totals)
    p95_total = percentile(totals, 0.95)

    print("=" * 66)
    print(f"NFR3 latency report — {len(measured)} model-backed requests")
    print(f"({len(rows) - len(measured)} excluded: no model call — refusals, stub runtime)")
    print("=" * 66)
    print(f"{'':16}{'mean':>9}{'median':>9}{'p95':>9}{'max':>9}")
    for label, series in (("total", totals), ("model", models), ("tools", tools)):
        print(
            f"{label:16}{statistics.fmean(series):8.2f}s{statistics.median(series):8.2f}s"
            f"{percentile(series, 0.95):8.2f}s{max(series):8.2f}s"
        )

    print("\nAgainst NFR3:")
    mean_ok = mean_total <= MEAN_TARGET_S
    p95_ok = p95_total <= P95_TARGET_S
    print(f"  mean {mean_total:5.2f}s <= {MEAN_TARGET_S:.0f}s   {'PASS' if mean_ok else 'FAIL'}")
    print(f"  p95  {p95_total:5.2f}s <= {P95_TARGET_S:.0f}s   {'PASS' if p95_ok else 'FAIL'}")

    by_model: dict[str, list[float]] = {}
    for row in measured:
        by_model.setdefault(row.model_used or "(none)", []).append(row.total_ms / 1000)
    print("\nBy model:")
    for name, series in sorted(by_model.items()):
        print(f"  {name:16} n={len(series):4}  mean {statistics.fmean(series):5.2f}s  "
              f"p95 {percentile(series, 0.95):5.2f}s")

    by_route: dict[str, list[float]] = {}
    for row in measured:
        by_route.setdefault(row.route, []).append(row.total_ms / 1000)
    print("\nBy route:")
    for name, series in sorted(by_route.items()):
        print(f"  {name:16} n={len(series):4}  mean {statistics.fmean(series):5.2f}s")

    fallbacks = sum(1 for r in measured if r.used_fallback)
    failures = sum(1 for r in measured if not r.succeeded)
    print(f"\nFallback used: {fallbacks}/{len(measured)}   Failed turns: {failures}")

    return 0 if (mean_ok and p95_ok) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bench", type=int, metavar="N", help="run N benchmark queries before reporting"
    )
    args = parser.parse_args()

    if args.bench:
        run_benchmark(args.bench)
    return report()


if __name__ == "__main__":
    sys.exit(main())
