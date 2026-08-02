"""
AgentMem demo seed script
─────────────────────────
Populates a local AgentMem instance with realistic financial data to demonstrate:
  1. Stale-fact suppression  — 5 revisions of NVDA guidance; only the latest is returned
  2. Point-in-time recall    — 4 quarters of TSLA deliveries; query any past date
  3. Supersession chains     — 6 Fed rate decisions; each supersedes the previous

Usage:
    # Start the stack first:
    cd agentmem && docker compose up --build -d
    # Wait ~15 seconds for migrations to complete, then:
    python scripts/seed_demo.py

    # Or point at a different host:
    python scripts/seed_demo.py --api-url https://agentmem.fly.dev --admin-secret <secret>

    # Export a read key to a new owner-only file (never printed to stdout):
    python scripts/seed_demo.py --read-key-output .demo-read-key
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


# ── CLI ───────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed AgentMem demo data")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Base URL of the AgentMem API")
    parser.add_argument("--admin-secret", default="demo-admin-secret", help="X-Admin-Secret header value")
    parser.add_argument("--namespace", default="demo", help="Namespace to seed")
    parser.add_argument(
        "--read-key-output",
        type=Path,
        help="Write a read bearer key to a new owner-only file (the key is never logged)",
    )
    return parser


def _ts(year: int, month: int, day: int) -> str:
    return datetime(year, month, day, 16, 0, 0, tzinfo=UTC).isoformat()


# ── Demo data ─────────────────────────────────────────────────────────────────

NVDA_GUIDANCE = [
    {
        "agent_id": "market-analyst",
        "content": "NVDA FY2026 revenue guidance: $28B — initial estimate from Q3 FY2025 earnings call",
        "event_time": _ts(2024, 11, 20),
        "source": "nvda_q3_fy2025_earnings",
        "metadata": {"ticker": "NVDA", "metric": "revenue_guidance", "fiscal_year": 2026, "value_bn": 28},
        "importance": 0.9,
    },
    {
        "agent_id": "market-analyst",
        "content": "NVDA FY2026 revenue guidance raised to $32B — Blackwell ramp ahead of schedule (Q4 FY2025 earnings)",
        "event_time": _ts(2025, 2, 26),
        "source": "nvda_q4_fy2025_earnings",
        "metadata": {"ticker": "NVDA", "metric": "revenue_guidance", "fiscal_year": 2026, "value_bn": 32},
        "importance": 0.9,
    },
    {
        "agent_id": "market-analyst",
        "content": "NVDA FY2026 revenue guidance raised to $36B — hyperscaler CapEx expansion driving demand (Q1 FY2026 earnings)",
        "event_time": _ts(2025, 5, 28),
        "source": "nvda_q1_fy2026_earnings",
        "metadata": {"ticker": "NVDA", "metric": "revenue_guidance", "fiscal_year": 2026, "value_bn": 36},
        "importance": 0.9,
    },
    {
        "agent_id": "market-analyst",
        "content": "NVDA FY2026 revenue guidance raised to $38B — data center demand remains robust (Q2 FY2026 earnings)",
        "event_time": _ts(2025, 8, 27),
        "source": "nvda_q2_fy2026_earnings",
        "metadata": {"ticker": "NVDA", "metric": "revenue_guidance", "fiscal_year": 2026, "value_bn": 38},
        "importance": 0.9,
    },
    {
        "agent_id": "market-analyst",
        "content": "NVDA FY2026 revenue guidance raised to $40B — beat consensus by $2B, Blackwell GB200 NVL72 sold out through FY2027 (Q3 FY2026 earnings)",
        "event_time": _ts(2025, 11, 19),
        "source": "nvda_q3_fy2026_earnings",
        "metadata": {"ticker": "NVDA", "metric": "revenue_guidance", "fiscal_year": 2026, "value_bn": 40},
        "importance": 0.9,
    },
]

TSLA_DELIVERIES = [
    {
        "agent_id": "market-analyst",
        "content": "TSLA Q1 2025 deliveries: 336,681 vehicles (Model Y: 324,130; other: 12,551). Missed Street estimate of 360,000. YoY -13%.",
        "event_time": _ts(2025, 4, 2),
        "source": "tsla_q1_2025_delivery_report",
        "metadata": {"ticker": "TSLA", "metric": "deliveries", "quarter": "Q1 2025", "value": 336681},
        "importance": 0.85,
    },
    {
        "agent_id": "market-analyst",
        "content": "TSLA Q2 2025 deliveries: 384,120 vehicles. Recovery quarter, beat estimate of 370,000. Cybertruck volume ramping.",
        "event_time": _ts(2025, 7, 2),
        "source": "tsla_q2_2025_delivery_report",
        "metadata": {"ticker": "TSLA", "metric": "deliveries", "quarter": "Q2 2025", "value": 384120},
        "importance": 0.85,
    },
    {
        "agent_id": "market-analyst",
        "content": "TSLA Q3 2025 deliveries: 462,890 vehicles. New quarterly record. Full self-driving subscription attach rate reached 18%.",
        "event_time": _ts(2025, 10, 2),
        "source": "tsla_q3_2025_delivery_report",
        "metadata": {"ticker": "TSLA", "metric": "deliveries", "quarter": "Q3 2025", "value": 462890},
        "importance": 0.85,
    },
    {
        "agent_id": "market-analyst",
        "content": "TSLA Q4 2025 deliveries: 495,570 vehicles. Full-year 2025 total: 1,679,261 vehicles. Guidance for 2026: 2.0M+ units.",
        "event_time": _ts(2026, 1, 2),
        "source": "tsla_q4_2025_delivery_report",
        "metadata": {"ticker": "TSLA", "metric": "deliveries", "quarter": "Q4 2025", "value": 495570},
        "importance": 0.85,
    },
]

FED_RATES = [
    {
        "agent_id": "macro-analyst",
        "content": "FOMC Sep 2024: cut fed funds rate 50bp to 4.75%–5.00% target range. First cut since 2020. 11-1 vote (Bowman dissented).",
        "event_time": _ts(2024, 9, 18),
        "source": "fomc_sep_2024_statement",
        "metadata": {"instrument": "fed_funds_rate", "metric": "target_rate", "upper_bound": 5.00, "lower_bound": 4.75},
        "importance": 0.95,
    },
    {
        "agent_id": "macro-analyst",
        "content": "FOMC Nov 2024: cut fed funds rate 25bp to 4.50%–4.75% target range. Unanimous vote. Powell: 'We are not on a preset course'.",
        "event_time": _ts(2024, 11, 7),
        "source": "fomc_nov_2024_statement",
        "metadata": {"instrument": "fed_funds_rate", "metric": "target_rate", "upper_bound": 4.75, "lower_bound": 4.50},
        "importance": 0.95,
    },
    {
        "agent_id": "macro-analyst",
        "content": "FOMC Dec 2024: cut fed funds rate 25bp to 4.25%–4.50% target range. Dot plot revised: only 2 cuts projected for 2025 (down from 4).",
        "event_time": _ts(2024, 12, 18),
        "source": "fomc_dec_2024_statement",
        "metadata": {"instrument": "fed_funds_rate", "metric": "target_rate", "upper_bound": 4.50, "lower_bound": 4.25},
        "importance": 0.95,
    },
    {
        "agent_id": "macro-analyst",
        "content": "FOMC Jan 2025: held fed funds rate at 4.25%–4.50%. Statement removed 'progress toward 2% inflation' language. Hawkish pause.",
        "event_time": _ts(2025, 1, 29),
        "source": "fomc_jan_2025_statement",
        "metadata": {"instrument": "fed_funds_rate", "metric": "target_rate", "upper_bound": 4.50, "lower_bound": 4.25},
        "importance": 0.95,
    },
    {
        "agent_id": "macro-analyst",
        "content": "FOMC Mar 2025: held fed funds rate at 4.25%–4.50%. GDP forecast cut to 1.7% for 2025; PCE forecast raised to 2.7%. Stagflation watch.",
        "event_time": _ts(2025, 3, 19),
        "source": "fomc_mar_2025_statement",
        "metadata": {"instrument": "fed_funds_rate", "metric": "target_rate", "upper_bound": 4.50, "lower_bound": 4.25},
        "importance": 0.95,
    },
    {
        "agent_id": "macro-analyst",
        "content": "FOMC May 2025: held fed funds rate at 4.25%–4.50%. Cited tariff-related uncertainty. QT pace unchanged at $25B/month.",
        "event_time": _ts(2025, 5, 7),
        "source": "fomc_may_2025_statement",
        "metadata": {"instrument": "fed_funds_rate", "metric": "target_rate", "upper_bound": 4.50, "lower_bound": 4.25},
        "importance": 0.95,
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_for_api(
    client: httpx.Client,
    api: str,
    retries: int = 30,
    delay: float = 2.0,
) -> None:
    print(f"Waiting for API at {api} ...", end="", flush=True)
    for _ in range(retries):
        try:
            r = client.get(f"{api}/health", timeout=3)
            if r.status_code == 200:
                print(" ready.")
                return
        except httpx.RequestError:
            pass
        print(".", end="", flush=True)
        time.sleep(delay)
    print()
    print("ERROR: API did not become healthy. Is docker compose up?", file=sys.stderr)
    sys.exit(1)


def _provision_key(
    client: httpx.Client,
    *,
    api: str,
    admin_secret: str,
    namespace: str,
    label: str,
    scopes: list[str],
) -> str:
    r = client.post(
        f"{api}/v1/admin/api-keys",
        json={"namespace": namespace, "scopes": scopes, "label": label},
        headers={"X-Admin-Secret": admin_secret},
    )
    r.raise_for_status()
    return r.json()["key"]


def _ingest(
    client: httpx.Client,
    key: str,
    memories: list[dict],
    *,
    api: str,
) -> None:
    for mem in memories:
        r = client.post(
            f"{api}/v1/memories",
            json=mem,
            headers={"X-API-Key": key},
        )
        if r.status_code not in (200, 201):
            print(f"  WARNING: {r.status_code} {r.text[:120]}", file=sys.stderr)
        else:
            data = r.json()
            superseded = data.get("superseded_by")
            tag = f" [supersedes {str(superseded)[:8]}]" if superseded else ""
            print(f"  ✓ {data['event_time'][:10]}  {data['content'][:70]}{tag}")


# ── Main ──────────────────────────────────────────────────────────────────────

def _write_read_key(path: Path, key: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = -1
            output.write(f"{key}\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _demo_instructions(*, api: str, namespace: str, read_key_path: Path | None) -> str:
    if read_key_path is None:
        key_notice = (
            "No read key was provisioned. Use --read-key-output PATH to create one "
            "in a new owner-only file."
        )
    else:
        key_notice = f"Read key written to {read_key_path} (contents never logged)."

    return f"""{key_notice}

Try it with curl:

  # Present-time recall - should return ONLY the latest NVDA guidance ($40B)
  curl -s -X POST {api}/v1/recall \\
    -H "X-API-Key: <read-key>" \\
    -H "Content-Type: application/json" \\
    -d '{{"agent_id":"market-analyst","query":"NVDA FY2026 revenue guidance","k":5}}' \\
    | python -m json.tool

  # Point-in-time - what did we know about NVDA guidance on 2025-03-01?
  curl -s -X POST {api}/v1/recall \\
    -H "X-API-Key: <read-key>" \\
    -H "Content-Type: application/json" \\
    -d '{{"agent_id":"market-analyst","query":"NVDA FY2026 revenue guidance","k":5,"as_of":"2025-03-01T00:00:00Z"}}' \\
    | python -m json.tool

  # Audit chain verification (substitute the admin secret without echoing it here)
  curl -s "{api}/v1/admin/audit/verify?namespace={namespace}" \\
    -H "X-Admin-Secret: <admin-secret>" \\
    | python -m json.tool

Open demo/index.html in a browser and paste the read key from the owner-only file.
"""


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    api = args.api_url.rstrip("/")

    with httpx.Client() as client:
        _wait_for_api(client, api)

        print(f"\nProvisioning API keys for namespace '{args.namespace}' ...")
        write_key = _provision_key(
            client,
            api=api,
            admin_secret=args.admin_secret,
            namespace=args.namespace,
            label="seed-write",
            scopes=["read", "write"],
        )
        print("  write key: [redacted]")

        if args.read_key_output is not None:
            read_key = _provision_key(
                client,
                api=api,
                admin_secret=args.admin_secret,
                namespace=args.namespace,
                label="demo-readonly",
                scopes=["read"],
            )
            _write_read_key(args.read_key_output, read_key)
            print(f"  read key written to {args.read_key_output} (contents not logged)")
        else:
            print("  read key: not provisioned (use --read-key-output PATH)")

        print("\n── NVDA FY2026 Revenue Guidance (5 revisions) ──────────────────")
        _ingest(client, write_key, NVDA_GUIDANCE, api=api)

        print("\n── TSLA Quarterly Deliveries (4 quarters) ──────────────────────")
        _ingest(client, write_key, TSLA_DELIVERIES, api=api)

        print("\n── Fed Funds Rate Decisions (6 FOMC meetings) ───────────────────")
        _ingest(client, write_key, FED_RATES, api=api)

    print("\n" + "=" * 70)
    print("Demo data loaded.")
    print(
        _demo_instructions(
            api=api,
            namespace=args.namespace,
            read_key_path=args.read_key_output,
        )
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
