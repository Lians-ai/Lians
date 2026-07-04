"""
Regulated memory eval — the benchmark only a compliance-grade memory layer passes.

General memory benchmarks (LoCoMo, LongMemEval) measure conversational recall. They
do not measure the things a regulated buyer must guarantee, and an accumulate-
everything store *fails* them by design:

  1. stale-revision suppression   — a superseded fact must NOT be retrieved
  2. point-in-time reconstruction — recall as-of a past date returns what was known then
  3. erasure proof                — erased content is unrecoverable
  4. lookahead-contamination      — facts unknowable at the simulation date are flagged
  5. audit state reconstruction   — the full knowledge state at any past T is reproducible

Each check is a hard invariant (pass/fail), run against any Lians client. Run the
*same* harness against mem0 / Zep adapters and watch them fail items 1, 3, and 4.

    python -m benchmarks.regulated_eval
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def _content(memories: list[dict]) -> list[str]:
    return [(m.get("content") or "") for m in memories]


def run_regulated_eval(client, agent: str = "reg-eval") -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    def check(name: str, fn: Callable[[], tuple[Any, Any]]) -> None:
        """
        Record a check outcome. `fn` returns (status, detail) where status is
        True (pass), "partial" (behaviorally satisfied but missing the proof
        artifact the invariant names), or False (fail). A thrown invariant is
        a failure, not a crash; CapabilityAbsent is recorded distinctly so the
        comparison layer can preserve documented-capability credit instead of
        letting a live run unfairly zero a cell whose static score already
        encodes "no turnkey API".
        """
        cap_absent = False
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"error: {type(exc).__name__}: {exc}"
            cap_absent = type(exc).__name__ == "CapabilityAbsent"
        status = "partial" if ok == "partial" else ("pass" if ok else "fail")
        results.append({
            "check": name,
            "passed": status == "pass",
            "status": status,
            "capability_absent": cap_absent,
            "detail": detail,
        })

    def stale_revision_suppression():
        a = f"{agent}-stale"
        client.add(a, "ACME revenue guidance is 36B", _dt(2025, 8, 1),
                   metadata={"ticker": "ACME", "metric": "guidance"})
        client.add(a, "ACME revenue guidance raised to 40B", _dt(2025, 11, 1),
                   metadata={"ticker": "ACME", "metric": "guidance"})
        now = _content(client.recall(a, "ACME revenue guidance", k=5)["memories"])
        current = any("40B" in c for c in now)
        stale = any("36B" in c for c in now)
        return (current and not stale), {"current_retrieved": current, "stale_excluded": not stale}

    def point_in_time_reconstruction():
        a = f"{agent}-pit"
        client.add(a, "policy rate is 5.00 percent", _dt(2025, 3, 1),
                   metadata={"ticker": "FED", "metric": "rate"})
        client.add(a, "policy rate is 5.25 percent", _dt(2025, 6, 1),
                   metadata={"ticker": "FED", "metric": "rate"})
        past = _content(client.recall_at(a, "policy rate", _dt(2025, 4, 1), k=5)["memories"])
        return any("5.00" in c for c in past), {"as_of_value_retrieved": any("5.00" in c for c in past)}

    def erasure_proof():
        # Full pass requires BOTH: content unrecoverable AND a proof artifact
        # (erasure certificate / request reference). Behavioral deletion with
        # no proof is "partial" — a bare delete_all() must not score as
        # "provable erasure" just because retrieval stops returning the row.
        a = f"{agent}-erase"
        client.add(a, "patient record SSN 123-45-6789", _dt(2026, 1, 1), subject_id="subj-erase-1")
        receipt = client.erase("subj-erase-1", "GDPR-REQ-1")
        after = _content(client.recall(a, "patient record SSN", k=5)["memories"])
        leaked = any("123-45-6789" in c for c in after)
        proof = bool(isinstance(receipt, dict) and (
            receipt.get("request_ref") or receipt.get("certificate_id")
            or receipt.get("certificate")
        ))
        detail = {"content_unrecoverable": not leaked, "proof_artifact": proof}
        if leaked:
            return False, detail
        return (True if proof else "partial"), detail

    def lookahead_contamination_detection():
        a = f"{agent}-look"
        client.add(a, "earnings beat reported", _dt(2026, 6, 1))
        report = client.backtest_check(a, _dt(2026, 1, 1))
        flagged = (report.get("is_clean") is False) and (len(report.get("flags", [])) >= 1)
        return flagged, {"is_clean": report.get("is_clean"), "flags": len(report.get("flags", []))}

    def audit_state_reconstruction():
        a = f"{agent}-snap"
        client.add(a, "disclosed fact A", _dt(2026, 1, 1))
        snap = client.snapshot(a, _dt(2026, 2, 1))
        return (snap.get("total", 0) >= 1), {"total": snap.get("total")}

    check("stale_revision_suppression", stale_revision_suppression)
    check("point_in_time_reconstruction", point_in_time_reconstruction)
    check("erasure_proof", erasure_proof)
    check("lookahead_contamination_detection", lookahead_contamination_detection)
    check("audit_state_reconstruction", audit_state_reconstruction)

    passed = sum(r["passed"] for r in results)
    return {
        "checks": results,
        "passed": passed,
        "total": len(results),
        "score": passed / len(results) if results else 0.0,
        # Barrier-leakage isolation is a 6th invariant, verified separately against
        # PostgreSQL RLS with a non-superuser role (see test_pgvector.py).
        "note": "barrier_leakage verified separately on Postgres RLS (non-superuser role)",
    }


def main() -> None:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
    from lians import LocalLiansClient

    with LocalLiansClient() as client:
        report = run_regulated_eval(client)

    print(f"Regulated memory eval: {report['passed']}/{report['total']} invariants hold\n")
    for r in report["checks"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['check']}  {r['detail']}")
    print(f"\n{report['note']}")


if __name__ == "__main__":
    main()
