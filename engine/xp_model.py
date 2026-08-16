"""
xp_model.py — estimates expected FPL points for every player over the next N gameweeks.

Inputs blended per player:
  - Underlying attacking output: expected_goals_per_90, expected_assists_per_90 (xG/xA,
    far more predictive than actual goals/assists because it strips out finishing luck)
  - Recent form (FPL's own 30-day form metric) blended with season-long per-90 rates,
    so a player heating up / cooling off is weighted appropriately
  - Playing-time reliability: starts_per_90 and the official "chance of playing" flags,
    so injury-prone / rotation-risk players are discounted
  - Clean sheet probability: modelled from each team's attack/defence strength vs the
    specific opponent they face that gameweek (pulled from fixtures + team strength ratings)
  - Fixture difficulty: every player's attacking/defensive output is scaled by the
    opponent's strength for that fixture, and double gameweeks (2 fixtures) /
    blank gameweeks (0 fixtures) are handled by summing across all fixtures in the event
  - Bonus points: approximated from BPS (bonus points system) rank trends
  - New defensive-contribution points (2025/26+ rule): probability of hitting the
    defensive-actions threshold (10 for DEF, 12 for MID/FWD) estimated from
    defensive_contribution_per_90
"""
from collections import defaultdict

POSITION_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

DEFAULT_SCORING = {
    "goals_scored": {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4},
    "assists": 3,
    "clean_sheets": {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0},
    "goals_conceded_per_2": {"GKP": -1, "DEF": -1, "MID": 0, "FWD": 0},  # per 2 conceded
    "saves_per_3": 1,
    "long_play": 2,   # 60+ mins
    "short_play": 1,  # 1-59 mins
    "yellow_cards": -1,
    "red_cards": -3,
    "bonus_avg": True,
    "dc_threshold": {"DEF": 10, "MID": 12, "FWD": 12, "GKP": 999},
    "dc_points": {"GKP": 0, "DEF": 2, "MID": 2, "FWD": 2},
}


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def build_team_strength(boot):
    """Rough attack/defence strength index per team, home & away, 0-1 scaled."""
    teams = {t["id"]: t for t in boot["teams"]}
    max_att = max(max(t["strength_attack_home"], t["strength_attack_away"]) for t in teams.values()) or 1
    max_def = max(max(t["strength_defence_home"], t["strength_defence_away"]) for t in teams.values()) or 1
    strength = {}
    for tid, t in teams.items():
        strength[tid] = {
            "att_home": t["strength_attack_home"] / max_att if max_att else 0.5,
            "att_away": t["strength_attack_away"] / max_att if max_att else 0.5,
            "def_home": t["strength_defence_home"] / max_def if max_def else 0.5,
            "def_away": t["strength_defence_away"] / max_def if max_def else 0.5,
        }
    return strength, teams


def fixtures_by_event(fixtures, from_event, n_events):
    """Group upcoming fixtures per team for the next n_events gameweeks."""
    target_events = set(range(from_event, from_event + n_events))
    by_team = defaultdict(list)
    for fx in fixtures:
        ev = fx.get("event")
        if ev not in target_events:
            continue
        by_team[fx["team_h"]].append({"event": ev, "opp": fx["team_a"], "home": True,
                                       "fdr": fx.get("team_h_difficulty", 3)})
        by_team[fx["team_a"]].append({"event": ev, "opp": fx["team_h"], "home": False,
                                       "fdr": fx.get("team_a_difficulty", 3)})
    return by_team


def playing_prob(player):
    """Probability the player features meaningfully this gameweek."""
    status = player.get("status", "a")
    if status in ("i", "s", "u", "n"):  # injured/suspended/unavailable/not in squad
        cop = player.get("chance_of_playing_next_round")
        return (cop or 0) / 100.0
    cop = player.get("chance_of_playing_next_round")
    if cop is not None:
        return cop / 100.0
    starts_p90 = _f(player.get("starts_per_90"))
    return min(1.0, max(0.15, starts_p90))


def dc_probability(per90_rate, threshold):
    """Rough logistic-style estimate of hitting the defensive-contribution threshold in a match."""
    if threshold >= 999 or per90_rate <= 0:
        return 0.0
    ratio = per90_rate / threshold
    # squashes to (0,1), centered so ratio=1.0 (average matches threshold) -> ~0.5
    import math
    return 1 / (1 + math.exp(-4 * (ratio - 1)))


def expected_points_for_fixture(player, pos, opp_strength, is_home, scoring=DEFAULT_SCORING,
                                 form_weight=0.55):
    """Expected points for ONE fixture, before summing across a gameweek's fixtures."""
    season_xg90 = _f(player.get("expected_goals_per_90"))
    season_xa90 = _f(player.get("expected_assists_per_90"))
    form = _f(player.get("form"))
    ppg = _f(player.get("points_per_game"))

    # Blend season-long underlying rate with recent form signal (form correlates with
    # minutes trend + hot/cold streaks that pure season xG/xA per90 won't capture)
    form_factor = 1.0
    if ppg > 0.3:
        form_factor = max(0.4, min(1.8, (form / max(ppg, 0.5))))
    blended_xg90 = season_xg90 * (1 + form_weight * (form_factor - 1))
    blended_xa90 = season_xa90 * (1 + form_weight * (form_factor - 1))

    # Fixture adjustment: attacking output scaled by opponent's defensive strength
    # (weaker opponent defence -> higher attacking strength multiplier)
    opp_def = opp_strength["def_home"] if not is_home else opp_strength["def_away"]
    att_mult = 0.7 + 0.6 * (1 - opp_def)  # weaker defence -> up to 1.3x; strong defence -> ~0.7x

    xg = blended_xg90 * att_mult
    xa = blended_xa90 * att_mult

    goal_pts = scoring["goals_scored"][pos]
    pts = xg * goal_pts + xa * scoring["assists"]

    # Clean sheet probability from opponent attack strength vs own defence quality
    exp_gc90 = _f(player.get("expected_goals_conceded_per_90"), default=1.3)
    opp_att = opp_strength["att_away"] if is_home else opp_strength["att_home"]
    adj_gc = exp_gc90 * (0.7 + 0.6 * opp_att)
    cs_prob = max(0.03, min(0.75, 0.55 - 0.35 * adj_gc))
    if pos in ("GKP", "DEF"):
        pts += cs_prob * scoring["clean_sheets"][pos]
        pts += -(adj_gc / 2.0) * abs(scoring["goals_conceded_per_2"][pos])
    elif pos == "MID":
        pts += cs_prob * scoring["clean_sheets"][pos]

    if pos == "GKP":
        saves90 = _f(player.get("saves_per_90"))
        pts += (saves90 / 3.0) * scoring["saves_per_3"]

    # Appearance points, weighted by probability of playing 60+ vs a cameo
    starts_p90 = _f(player.get("starts_per_90"))
    p60 = min(0.95, starts_p90)
    pts += p60 * scoring["long_play"] + (1 - p60) * 0.35 * scoring["short_play"]

    # Defensive contribution points (new-style DC bonus)
    dc90 = _f(player.get("defensive_contribution_per_90"))
    thr = scoring["dc_threshold"].get(pos, 999)
    pts += dc_probability(dc90, thr) * scoring["dc_points"].get(pos, 0)

    # Bonus points approximation from BPS scale (rough: top decile bps -> ~0.6 bonus/game)
    bps = _f(player.get("bps"))
    minutes = _f(player.get("minutes"), default=1)
    bps90 = (bps / minutes * 90) if minutes > 0 else 0
    pts += max(0, min(1.2, bps90 / 300))

    p_play = playing_prob(player)
    return max(0.0, pts) * p_play


def compute_xp_table(boot, fixtures, n_gameweeks=5):
    """Returns {player_id: {'xp_total':..., 'xp_by_gw': {gw: pts}, 'fixtures': [...]}}"""
    strength, teams = build_team_strength(boot)
    _, nxt = current_and_next_event_(boot)
    by_team_fixtures = fixtures_by_event(fixtures, nxt, n_gameweeks)

    results = {}
    for p in boot["elements"]:
        pos = POSITION_MAP[p["element_type"]]
        team_id = p["team"]
        fx_list = by_team_fixtures.get(team_id, [])
        xp_by_gw = defaultdict(float)
        fixture_desc = []
        for fx in fx_list:
            opp_id = fx["opp"]
            opp_strength = strength[opp_id]
            pts = expected_points_for_fixture(p, pos, opp_strength, fx["home"])
            xp_by_gw[fx["event"]] += pts
            fixture_desc.append({
                "gw": fx["event"],
                "opponent": teams[opp_id]["short_name"],
                "home": fx["home"],
                "fdr": fx["fdr"],
                "xp": round(pts, 2),
            })
        results[p["id"]] = {
            "xp_total": round(sum(xp_by_gw.values()), 2),
            "xp_by_gw": {k: round(v, 2) for k, v in xp_by_gw.items()},
            "fixtures": fixture_desc,
            "num_fixtures": len(fx_list),  # 0 = blank GW risk, 2+ = double GW upside
        }
    return results


def current_and_next_event_(boot):
    events = boot["events"]
    nxt = next((e for e in events if e.get("is_next")), None)
    if nxt is None:
        nxt = next((e for e in events if not e["finished"]), events[0])
    return None, nxt["id"]
