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

from engine import data, transfer_advisor, tracking

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-id", type=int, default=None, help="Your FPL team ID (from the URL)")
    ap.add_argument("--squad", type=str, default=None, help="Comma-separated player IDs, manual override")
    ap.add_argument("--bank", type=float, default=0.0, help="Money in the bank (£m)")
    ap.add_argument("--free-transfers", type=int, default=1)
    ap.add_argument("--budget", type=float, default=100.0, help="Total budget for initial squad build")
    ap.add_argument("--horizon", type=int, default=5, help="Gameweeks to look ahead")
    ap.add_argument("--risk-mode", type=str, default="balanced",
                     choices=["balanced", "chase", "protect"],
                     help="balanced=pure EV, chase=differential captains for climbing rank, "
                          "protect=template captains for defending rank")
    ap.add_argument("--email", action="store_true", help="Send the report by email")
    ap.add_argument("--no-refresh", action="store_true", help="Use cached data instead of re-fetching")
    args = ap.parse_args()

    if not args.no_refresh:
        print("Fetching live FPL data...")
        data.fetch_bootstrap()
        data.fetch_fixtures()

    boot = data.load_cached_bootstrap()

    # Reconcile any past predictions whose gameweek has now finished — this is the
    # feedback loop that tells you whether the model is actually any good.
    newly_reconciled = tracking.reconcile_pending(boot, data.fetch_event_live)
    for entry in newly_reconciled:
        diff = entry["actual_points"] - entry["predicted_points"]
        print(f"\n[Calibration] GW{entry['gameweek']}: predicted {entry['predicted_points']:.1f}, "
              f"actual {entry['actual_points']}, diff {diff:+.1f}")
    calib = tracking.calibration_summary()
    if calib:
        print(f"[Calibration] Last {len(calib['gameweeks_covered'])} GWs — "
              f"mean absolute error/player: {calib['overall_mae']}, "
              f"bias: {calib['overall_bias']:+.2f} "
              f"({'model under-predicts' if calib['overall_bias'] > 0 else 'model over-predicts'})")

    current_squad_ids = None
    if args.squad:
        current_squad_ids = [int(x) for x in args.squad.split(",")]
    elif args.entry_id:
        try:
            picks = data.fetch_my_team(args.entry_id)
            current_squad_ids = [p["element"] for p in picks["picks"]]
        except Exception as e:
            print(f"\n[Warning] Could not fetch entry {args.entry_id}'s picks ({e}). "
                  f"This is expected before the season's first gameweek starts, or if "
                  f"the FPL API is briefly down. Falling back to a fresh optimal-squad "
                  f"build instead of transfer advice for this run.")
            current_squad_ids = None

    report = transfer_advisor.build_weekly_report(
        n_gameweeks=args.horizon,
        free_transfers=args.free_transfers,
        budget_bank=args.bank,
        current_squad_ids=current_squad_ids,
        total_budget=args.budget,
        risk_mode=args.risk_mode,
    )

    text = transfer_advisor.format_email_text(report)
    print("\n" + text)

    # Log this week's prediction so it can be checked against reality next run
    tracking.log_prediction(report)

    # Write dashboard data (consumed by dashboard/index.html)
    out_dir = ROOT / "dashboard"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(report, default=str, indent=2))
    print(f"\nDashboard data written to {out_dir / 'data.json'}")

    if args.email:
        try:
            from engine import email_notify
            email_notify.send_report_email(
                subject=f"FPL AI Report — Gameweek {report['gameweek']}",
                text_body=text,
            )
        except Exception as e:
            print(f"\n[Warning] Email failed to send ({e}). Check that SMTP_HOST, "
                  f"SMTP_PORT, SMTP_USER, SMTP_PASS, and EMAIL_TO are all set correctly "
                  f"under repo Settings -> Secrets and variables -> Actions -> Secrets. "
                  f"The squad/dashboard data above was still generated successfully.")


if __name__ == "__main__":
    main()
