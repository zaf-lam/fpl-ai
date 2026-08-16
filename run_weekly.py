#!/usr/bin/env python3
"""
run_weekly.py — the single entrypoint. Run this locally, or let GitHub Actions run it
every week automatically (see .github/workflows/weekly.yml).

Usage:
  python run_weekly.py                          # builds your initial optimal 15
  python run_weekly.py --squad 1,2,3,...,15      # advises transfers for an existing squad
  python run_weekly.py --entry-id 1234567        # pulls YOUR real FPL squad automatically
"""
import argparse
import json
import os
from pathlib import Path

from engine import data, transfer_advisor

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-id", type=int, default=None, help="Your FPL team ID (from the URL)")
    ap.add_argument("--squad", type=str, default=None, help="Comma-separated player IDs, manual override")
    ap.add_argument("--bank", type=float, default=0.0, help="Money in the bank (£m)")
    ap.add_argument("--free-transfers", type=int, default=1)
    ap.add_argument("--budget", type=float, default=100.0, help="Total budget for initial squad build")
    ap.add_argument("--horizon", type=int, default=5, help="Gameweeks to look ahead")
    ap.add_argument("--email", action="store_true", help="Send the report by email")
    ap.add_argument("--no-refresh", action="store_true", help="Use cached data instead of re-fetching")
    args = ap.parse_args()

    if not args.no_refresh:
        print("Fetching live FPL data...")
        data.fetch_bootstrap()
        data.fetch_fixtures()

    current_squad_ids = None
    if args.squad:
        current_squad_ids = [int(x) for x in args.squad.split(",")]
    elif args.entry_id:
        picks = data.fetch_my_team(args.entry_id)
        current_squad_ids = [p["element"] for p in picks["picks"]]

    report = transfer_advisor.build_weekly_report(
        n_gameweeks=args.horizon,
        free_transfers=args.free_transfers,
        budget_bank=args.bank,
        current_squad_ids=current_squad_ids,
        total_budget=args.budget,
    )

    text = transfer_advisor.format_email_text(report)
    print(text)

    # Write dashboard data (consumed by dashboard/index.html)
    out_dir = ROOT / "dashboard"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(report, default=str, indent=2))
    print(f"\nDashboard data written to {out_dir / 'data.json'}")

    if args.email:
        from engine import email_notify
        email_notify.send_report_email(
            subject=f"FPL AI Report — Gameweek {report['gameweek']}",
            text_body=text,
        )


if __name__ == "__main__":
    main()
