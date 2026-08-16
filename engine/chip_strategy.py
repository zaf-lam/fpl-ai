"""
chip_strategy.py — recommends whether to use Wildcard / Free Hit / Bench Boost /
Triple Captain this gameweek, based on double/blank gameweek detection from the
fixture list. Requires no extra data sources — everything comes from the FPL
fixtures endpoint you already fetch.
"""


def detect_double_and_blank_teams(fixtures, target_gw):
    """Returns (double_gw_team_ids, blank_gw_team_ids) for a specific gameweek."""
    from collections import Counter
    counts = Counter()
    for fx in fixtures:
        if fx.get("event") == target_gw:
            counts[fx["team_h"]] += 1
            counts[fx["team_a"]] += 1
    doubles = {t for t, c in counts.items() if c >= 2}
    # "Blank" here means: has fixtures scheduled elsewhere in the season but none
    # this specific gameweek. We approximate by checking which teams appear anywhere
    # in `fixtures` at all, then subtracting those with a fixture this GW.
    all_teams = set()
    for fx in fixtures:
        all_teams.add(fx["team_h"])
        all_teams.add(fx["team_a"])
    teams_this_gw = set(counts.keys())
    blanks = all_teams - teams_this_gw
    return doubles, blanks


def recommend_chip(squad, fixtures, next_gw, chips_available=None, first_chip_set_expires_gw=19):
    """squad: list of player dicts (with 'team', 'pos', 'xp'). Returns a recommendation dict."""
    chips_available = chips_available or {"wildcard", "free_hit", "bench_boost", "triple_captain"}
    doubles, blanks = detect_double_and_blank_teams(fixtures, next_gw)

    squad_teams = {p["team"] for p in squad}
    squad_doublers = squad_teams & doubles
    squad_blankers = squad_teams & blanks

    # Bench Boost: strong when several of YOUR players double this week
    if "bench_boost" in chips_available and len(squad_doublers) >= 4:
        return {
            "chip": "bench_boost",
            "rationale": f"{len(squad_doublers)} of your clubs have a double gameweek — "
                         f"your bench should score well too.",
            "target_gw": next_gw,
        }

    # Free Hit: strong when several of YOUR players blank this week
    if "free_hit" in chips_available and len(squad_blankers) >= 4:
        return {
            "chip": "free_hit",
            "rationale": f"{len(squad_blankers)} of your clubs have no fixture this "
                         f"gameweek — Free Hit lets you field a full XI just for this week.",
            "target_gw": next_gw,
        }

    # Wildcard: many underperforming/blanking players and it's not close to season end
    if "wildcard" in chips_available and len(squad_blankers) >= 3:
        return {
            "chip": "wildcard",
            "rationale": f"{len(squad_blankers)} squad players are without a fixture and "
                         f"it isn't a one-off blank — consider a Wildcard to restructure "
                         f"around the sides with better runs.",
            "target_gw": next_gw,
        }

    # Triple Captain: a strong attacking player with a double gameweek
    tc_candidates = [p for p in squad if p["team"] in squad_doublers and p["pos"] in ("MID", "FWD")]
    if "triple_captain" in chips_available and tc_candidates:
        best = max(tc_candidates, key=lambda p: p["xp"])
        return {
            "chip": "triple_captain",
            "rationale": f"{best['name']} has a double gameweek — strong Triple Captain window.",
            "target_gw": next_gw,
        }

    return {"chip": None, "rationale": "No standout chip opportunity this week — hold.",
            "target_gw": next_gw}
