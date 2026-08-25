"""
transfer_advisor.py — produces the actual weekly output: who to captain, who to
transfer in/out (if worth it after the -4 hit), differentials to watch, and
chip suggestions (wildcard / bench boost / triple captain / free hit).
"""
from . import data, xp_model, optimizer, captain as captain_mod, chip_strategy


def build_weekly_report(entry_id=None, n_gameweeks=5, free_transfers=1, budget_bank=0.0,
                          current_squad_ids=None, total_budget=100.0, chips_available=None,
                          risk_mode="balanced", banned_ids=None):
    boot = data.load_cached_bootstrap()
    fixtures = data.load_cached_fixtures()
    _, next_gw = xp_model.current_and_next_event_(boot)

    xp_table = xp_model.compute_xp_table(boot, fixtures, n_gameweeks=n_gameweeks)
    pool = optimizer.build_player_pool(boot, xp_table)

    if current_squad_ids:
        rec = optimizer.best_transfers(pool, current_squad_ids, budget_bank, free_transfers)
        mode = "transfer_advice"
    else:
        rec = optimizer.optimize_squad(pool, budget=total_budget, banned_ids=banned_ids)
        mode = "initial_squad"

    squad = rec["squad"]
    starters = [p for p in squad if p["starting"]]
    bench = [p for p in squad if not p["starting"]]

    # Composite captain scoring (xP + ceiling + DGW boost) instead of raw highest-xP
    captain, vice, captain_ranked = captain_mod.recommend_captaincy(starters, xp_table, next_gw, risk_mode)
    for p in squad:
        p["captain"] = (captain is not None and p["id"] == captain["id"])

    chip_rec = chip_strategy.recommend_chip(squad, fixtures, next_gw, chips_available)

    # Differentials: low ownership, high xP — useful for climbing ranks fast
    differentials = sorted(
        [p for p in pool if p["selected_by"] < 10 and p["xp"] > 3.0],
        key=lambda p: -p["xp"]
    )[:8]

    # Double/blank gameweek flags — checked against the SPECIFIC next gameweek, not the
    # whole lookahead horizon (a bug in the earlier version flagged nearly every player
    # as having a "double" simply because they had multiple fixtures somewhere across
    # the 5-week window).
    dgw_bgw_notes = []
    for p in squad:
        info = xp_table.get(p["id"], {})
        if info.get("next_gw_fixture_count", 1) == 0:
            dgw_bgw_notes.append(f"{p['name']}: BLANK gameweek {next_gw} — no fixture, "
                                  f"consider benching/replacing")
        elif info.get("next_gw_fixture_count", 1) >= 2:
            dgw_bgw_notes.append(f"{p['name']}: DOUBLE gameweek {next_gw} — plays twice, "
                                  f"strong captain/hold candidate")
        # Also surface a heads-up for blanks/doubles later in the horizon (not this week)
        future_doubles = [gw for gw in info.get("double_gws", []) if gw != next_gw]
        future_blanks = [gw for gw in info.get("blank_gws", []) if gw != next_gw]
        if future_doubles:
            dgw_bgw_notes.append(f"{p['name']}: double gameweek later (GW{future_doubles[0]}) — plan ahead")
        if future_blanks:
            dgw_bgw_notes.append(f"{p['name']}: blank later (GW{future_blanks[0]}) — plan ahead")

    # Attach fixture list + confidence info to each squad player so the dashboard can
    # show club/fixtures/reasoning without a second lookup.
    for p in squad:
        info = xp_table.get(p["id"], {})
        p["fixtures"] = info.get("fixtures", [])
        p["career_minutes"] = info.get("career_minutes", 0)
        p["data_confidence"] = info.get("data_confidence", 1.0)

    # The optimizer works on horizon totals (5 GWs), so its "predicted_gw_points" is a
    # multi-week figure. For display we also compute what this XI is expected to score
    # in the NEXT gameweek specifically, which is the number that's actually intuitive.
    next_gw_points = 0.0
    for p in starters:
        gw_xp = xp_table.get(p["id"], {}).get("xp_by_gw", {}).get(next_gw, 0.0)
        next_gw_points += gw_xp * (2 if p.get("captain") else 1)

    report = {
        "gameweek": next_gw,
        "mode": mode,
        "next_gw_points": round(next_gw_points, 1),
        "horizon_gameweeks": n_gameweeks,
        "squad": squad,
        "starters": starters,
        "bench": bench,
        "captain": captain,
        "vice_captain": vice,
        "predicted_points": rec["predicted_gw_points"],
        "total_cost": rec["total_cost"],
        "budget_left": rec["budget_left"],
        "differentials": differentials,
        "flags": dgw_bgw_notes,
        "captain_alternatives": captain_ranked,
        "chip_recommendation": chip_rec,
    }
    if mode == "transfer_advice":
        report["transfers_made"] = rec["transfers_made"]
        report["hit_cost"] = rec["hit_cost"]
        report["net_gain"] = rec["net_gain"]
        report["transfers_in"] = rec.get("transfers_in", [])
        report["transfers_out"] = rec.get("transfers_out", [])
        report["baseline_points"] = rec.get("baseline_points")
        report["worth_it"] = rec["net_gain"] > 0.5  # small margin so it's not chasing noise
    return report


def format_email_text(report):
    lines = [f"FPL AI Weekly Report — Gameweek {report['gameweek']}", "=" * 40, ""]
    if report["mode"] == "transfer_advice":
        if report["worth_it"] and report["transfers_made"] > 0:
            hit_txt = f", taking a -{report['hit_cost']} pt hit" if report["hit_cost"] else " (free)"
            lines.append(f"RECOMMENDATION: Make {report['transfers_made']} transfer(s)"
                         f"{hit_txt}.")
            for out_p, in_p in zip(report.get("transfers_out", []), report.get("transfers_in", [])):
                lines.append(f"   OUT: {out_p['name']} ({out_p['team_name']}, £{out_p['cost']}m)"
                             f"  ->  IN: {in_p['name']} ({in_p['team_name']}, £{in_p['cost']}m)")
            lines.append(f"   Net expected gain vs holding: +{report['net_gain']:.1f} pts "
                         f"over the next {report.get('horizon_gameweeks', 5)} GWs.")
        else:
            lines.append("RECOMMENDATION: Hold — no transfer beats keeping your current squad "
                         "once the points hit is accounted for.")
        lines.append("")
    lines.append(f"Captain: {report['captain']['name']} ({report['captain']['xp']:.1f} xP)")
    if report["vice_captain"]:
        lines.append(f"Vice-captain: {report['vice_captain']['name']}")
    chip = report.get("chip_recommendation")
    if chip and chip.get("chip"):
        lines.append(f"Chip suggestion: {chip['chip'].replace('_',' ').title()} — {chip['rationale']}")
    lines.append(f"Predicted points for GW{report['gameweek']} (XI + captain): "
                 f"{report.get('next_gw_points', 0):.1f}")
    lines.append(f"  (over the full {report.get('horizon_gameweeks', 5)}-GW horizon: "
                 f"{report['predicted_points']:.1f})")
    lines.append("")
    lines.append("Starting XI:")
    for p in sorted(report["starters"], key=lambda x: x["pos"]):
        tag = " (C)" if p.get("captain") else ""
        lines.append(f"  {p['pos']:4s} {p['name']:18s} £{p['cost']:.1f}m  xP {p['xp']:.1f}{tag}")
    lines.append("")
    lines.append("Bench:")
    for p in sorted(report["bench"], key=lambda x: -x["xp"]):
        lines.append(f"  {p['pos']:4s} {p['name']:18s} £{p['cost']:.1f}m  xP {p['xp']:.1f}")
    if report["flags"]:
        lines.append("")
        lines.append("Fixture flags:")
        for f in report["flags"]:
            lines.append(f"  - {f}")
    if report["differentials"]:
        lines.append("")
        lines.append("Differentials worth watching (<10% owned):")
        for p in report["differentials"][:5]:
            lines.append(f"  - {p['name']} ({p['team_name']}, {p['pos']}) "
                         f"{p['selected_by']:.1f}% owned, xP {p['xp']:.1f}")
    return "\n".join(lines)
