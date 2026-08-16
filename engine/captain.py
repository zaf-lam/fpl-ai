"""
captain.py — picks the best captain from your starting XI using a composite score,
not just "highest xP". Raw xP alone under-weights ceiling (ownership-beating ceiling
matters if you're chasing rank) and ignores double-gameweek upside.
"""


def ceiling_estimate(player_xp_this_gw, pos):
    """Rough 90th-percentile outcome estimate. Attacking returns are lumpy (a goal is
    worth 4-10 pts in one go), so ceiling scales super-linearly with xP for MID/FWD;
    defenders' ceiling is more capped by clean-sheet binary outcomes."""
    if pos in ("MID", "FWD"):
        return player_xp_this_gw * 2.1
    return player_xp_this_gw * 1.6


def score_captain_candidate(player, this_gw_xp, num_fixtures_this_gw, pos, risk_mode="balanced",
                             ownership=0.0):
    """risk_mode:
      - 'balanced' (default): pure expected-value ranking
      - 'chase': for climbing rank fast — rewards ceiling and low ownership (a
        differential captain that hauls gains you places on the whole field, not just
        the people who also own them)
      - 'protect': for defending a good rank — rewards matching the template captain,
        since going differential and it failing loses you relative ground even if your
        pick's expected value is similar
    """
    ceiling = ceiling_estimate(this_gw_xp, pos)
    dgw_boost = 1.35 if num_fixtures_this_gw >= 2 else 1.0

    if risk_mode == "chase":
        differential = (100 - ownership) / 100.0
        score = (0.45 * this_gw_xp + 0.35 * ceiling * 0.3 + 0.25 * differential * this_gw_xp) * dgw_boost
    elif risk_mode == "protect":
        template = ownership / 100.0
        score = (0.65 * this_gw_xp + 0.15 * ceiling * 0.3 + 0.20 * template * this_gw_xp) * dgw_boost
    else:
        score = (0.60 * this_gw_xp + 0.30 * ceiling * 0.3) * dgw_boost
    return score


def recommend_captaincy(starters, xp_table, next_gw, risk_mode="balanced"):
    """starters: list of squad dicts with 'starting'=True (from optimizer output).
    Returns (captain, vice_captain, ranked_list) using the composite score."""
    candidates = []
    for p in starters:
        info = xp_table.get(p["id"], {})
        this_gw_xp = info.get("xp_by_gw", {}).get(next_gw, p.get("xp", 0.0))
        n_fx = info.get("next_gw_fixture_count", 1)
        ownership = p.get("selected_by", 0.0)
        score = score_captain_candidate(p, this_gw_xp, n_fx, p["pos"], risk_mode, ownership)
        candidates.append({**p, "captain_score": round(score, 2), "gw_xp": round(this_gw_xp, 2)})

    ranked = sorted(candidates, key=lambda c: -c["captain_score"])
    captain = ranked[0] if ranked else None
    vice = ranked[1] if len(ranked) > 1 else None
    return captain, vice, ranked[:5]
