"""
optimizer.py — picks the mathematically optimal 15-man squad (or best transfer)
under real FPL constraints, using integer linear programming (PuLP / CBC solver).

Constraints enforced:
  - Exactly 15 players: 2 GKP, 5 DEF, 5 MID, 3 FWD
  - Budget (default £100.0m)
  - Max 3 players from any one Premier League club
  - Starting XI formation legality (1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD, 11 total)
  - Captain = highest-xP player in starting XI (2x points)
"""
import pulp

POSITIONS = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
SQUAD_REQ = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}


def build_player_pool(boot, xp_table, exclude_unavailable=True):
    pool = []
    for p in boot["elements"]:
        if exclude_unavailable and p.get("status") == "u":  # left the league / unavailable
            continue
        info = xp_table.get(p["id"], {})
        # Optimize on the time-decayed multi-gameweek total, not the raw sum — this is
        # what stops the solver overvaluing a player with one great week buried among
        # four bad ones. Falls back to xp_total for older cached data without the field.
        xp = info.get("xp_total_weighted", info.get("xp_total", 0.0))
        pool.append({
            "id": p["id"],
            "name": p["web_name"],
            "team": p["team"],
            "team_name": next(t["short_name"] for t in boot["teams"] if t["id"] == p["team"]),
            "pos": {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}[p["element_type"]],
            "cost": p["now_cost"] / 10.0,
            "xp": xp,
            "selected_by": float(p.get("selected_by_percent", 0) or 0),
            "status": p.get("status", "a"),
            "news": p.get("news", ""),
        })
    return pool


def optimize_squad(pool, budget=100.0, locked_ids=None, banned_ids=None,
                    current_squad_ids=None, max_transfers=None):
    """Full squad optimization: pick 15 players maximizing total starting-XI+bench xP
    (bench weighted low since they rarely play) under budget/club/position constraints.

    If `current_squad_ids` and `max_transfers` are given, the solver is CONSTRAINED to
    keep at least (15 - max_transfers) of your existing players. This is what makes it
    give real transfer advice for YOUR team rather than proposing a from-scratch rebuild
    every week."""
    locked_ids = set(locked_ids or [])
    banned_ids = set(banned_ids or [])
    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)

    x = {p["id"]: pulp.LpVariable(f"squad_{p['id']}", cat="Binary") for p in pool}
    starts = {p["id"]: pulp.LpVariable(f"xi_{p['id']}", cat="Binary") for p in pool}
    cap = {p["id"]: pulp.LpVariable(f"cap_{p['id']}", cat="Binary") for p in pool}

    by_id = {p["id"]: p for p in pool}

    # Objective: sum of xP for the 11 starters (captain doubled) + tiny weight on bench xP
    prob += pulp.lpSum(
        starts[pid] * by_id[pid]["xp"] + cap[pid] * by_id[pid]["xp"]
        for pid in x
    ) + 0.08 * pulp.lpSum((x[pid] - starts[pid]) * by_id[pid]["xp"] for pid in x)

    # Budget
    prob += pulp.lpSum(x[pid] * by_id[pid]["cost"] for pid in x) <= budget

    # Squad size & position quotas
    prob += pulp.lpSum(x.values()) == 15
    for pos, n in SQUAD_REQ.items():
        prob += pulp.lpSum(x[pid] for pid in x if by_id[pid]["pos"] == pos) == n

    # Max 3 per club
    clubs = set(p["team"] for p in pool)
    for c in clubs:
        prob += pulp.lpSum(x[pid] for pid in x if by_id[pid]["team"] == c) <= 3

    # Starting XI must be a subset of the squad, exactly 11, formation-legal
    for pid in x:
        prob += starts[pid] <= x[pid]
    prob += pulp.lpSum(starts.values()) == 11
    for pos in POSITIONS:
        prob += pulp.lpSum(starts[pid] for pid in x if by_id[pid]["pos"] == pos) >= XI_MIN[pos]
        prob += pulp.lpSum(starts[pid] for pid in x if by_id[pid]["pos"] == pos) <= XI_MAX[pos]

    # Captain must be a starter, exactly one captain
    for pid in x:
        prob += cap[pid] <= starts[pid]
    prob += pulp.lpSum(cap.values()) == 1

    # Locked / banned players
    for pid in locked_ids:
        if pid in x:
            prob += x[pid] == 1
    for pid in banned_ids:
        if pid in x:
            prob += x[pid] == 0

    # Transfer limit: keep at least (15 - max_transfers) of the current squad.
    # Without this the "optimizer" simply rebuilds an ideal team from scratch and then
    # reports however many changes that happens to imply — which is not transfer advice.
    if current_squad_ids is not None and max_transfers is not None:
        held = [x[pid] for pid in current_squad_ids if pid in x]
        prob += pulp.lpSum(held) >= max(0, 15 - max_transfers)

    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    squad, starters, captain_id = [], [], None
    for pid in x:
        if pulp.value(x[pid]) > 0.5:
            entry = dict(by_id[pid])
            entry["starting"] = pulp.value(starts[pid]) > 0.5
            entry["captain"] = pulp.value(cap[pid]) > 0.5
            if entry["captain"]:
                captain_id = pid
            squad.append(entry)
    total_cost = sum(p["cost"] for p in squad)
    starting_xp = sum(p["xp"] for p in squad if p["starting"])
    captain_bonus = by_id[captain_id]["xp"] if captain_id else 0
    return {
        "squad": sorted(squad, key=lambda p: (POSITIONS[p["pos"]], -p["xp"])),
        "total_cost": round(total_cost, 1),
        "budget_left": round(budget - total_cost, 1),
        "predicted_gw_points": round(starting_xp + captain_bonus, 1),
        "captain_id": captain_id,
        "status": pulp.LpStatus[prob.status],
    }


def best_transfers(pool, current_squad_ids, budget_bank, free_transfers=1, max_transfers=2):
    """Given an existing squad + bank + free transfers, find the best transfer plan.

    Evaluates 0, 1, ... up to max_transfers changes as genuinely separate constrained
    problems, and scores each by the GAIN OVER DOING NOTHING minus the points hit.
    A plan is only preferred if it actually beats holding your current squad.
    """
    by_id = {p["id"]: p for p in pool}
    current_ids = [pid for pid in current_squad_ids if pid in by_id]
    current_cost = sum(by_id[pid]["cost"] for pid in current_ids)
    squad_budget = current_cost + budget_bank

    # Baseline: your squad exactly as-is (0 transfers), best XI/captain chosen from it.
    baseline = optimize_squad(pool, budget=squad_budget,
                               current_squad_ids=current_ids, max_transfers=0)
    baseline_pts = baseline["predicted_gw_points"]

    best = {**baseline, "transfers_made": 0, "hit_cost": 0, "net_gain": 0.0,
            "transfers_in": [], "transfers_out": [], "baseline_points": baseline_pts}

    for n in range(1, max_transfers + 1):
        result = optimize_squad(pool, budget=squad_budget,
                                 current_squad_ids=current_ids, max_transfers=n)
        if result["status"] != "Optimal":
            continue
        new_ids = {p["id"] for p in result["squad"]}
        actual_transfers = len(set(current_ids) - new_ids)
        hit = max(0, actual_transfers - free_transfers) * 4
        # Gain measured against holding, which is the decision you're actually making
        net_gain = (result["predicted_gw_points"] - baseline_pts) - hit
        if net_gain > best["net_gain"]:
            out_ids = set(current_ids) - new_ids
            in_ids = new_ids - set(current_ids)
            best = {
                **result,
                "transfers_made": actual_transfers,
                "hit_cost": hit,
                "net_gain": round(net_gain, 2),
                "transfers_out": [by_id[i] for i in out_ids],
                "transfers_in": [by_id[i] for i in in_ids],
                "baseline_points": baseline_pts,
            }
    return best
