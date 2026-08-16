"""
tracking.py — the feedback loop. Every time run_weekly.py generates a report, it logs
the prediction. Every time it runs again after that gameweek has finished, it fetches
the real results and reconciles: predicted vs actual, per player and for the squad
total. This is what turns "a model I built once" into "a model I can actually trust
and improve", because right now there is no evidence either way about how good the
xP numbers are.

Log lives at data/predictions_log.json — a list of one entry per gameweek. Safe to
commit to git so the history survives across machines / GitHub Actions runs.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

LOG_PATH = Path(__file__).parent.parent / "data" / "predictions_log.json"


def _load_log():
    if not LOG_PATH.exists():
        return []
    return json.loads(LOG_PATH.read_text())


def _save_log(log):
    LOG_PATH.parent.mkdir(exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2))


def log_prediction(report):
    """Call this after building a weekly report, to record what the model predicted."""
    log = _load_log()
    gw = report["gameweek"]
    # Don't double-log the same gameweek — overwrite if we already had a prediction for it
    # (e.g. you reran the script closer to the deadline with fresher data).
    log = [e for e in log if e["gameweek"] != gw]
    entry = {
        "gameweek": gw,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "predicted_points": report["predicted_points"],
        "captain_id": report["captain"]["id"] if report["captain"] else None,
        "captain_name": report["captain"]["name"] if report["captain"] else None,
        "starters": [{"id": p["id"], "name": p["name"], "pos": p["pos"], "predicted_xp": p["xp"]}
                     for p in report["starters"]],
        "actual_points": None,     # filled in later by reconcile()
        "actual_by_player": None,
    }
    log.append(entry)
    _save_log(log)
    return entry


def reconcile_pending(boot, fetch_event_live_fn):
    """Finds any logged gameweeks that have finished but haven't been reconciled yet,
    fetches actual results, and fills in accuracy. Returns a list of newly-reconciled
    entries (for printing a summary)."""
    log = _load_log()
    finished_gws = {e["id"] for e in boot["events"] if e.get("finished")}
    newly_done = []

    for entry in log:
        if entry["actual_points"] is not None:
            continue
        if entry["gameweek"] not in finished_gws:
            continue
        live = fetch_event_live_fn(entry["gameweek"])
        actual_by_id = {el["id"]: el["stats"]["total_points"] for el in live["elements"]}

        actual_total = 0
        by_player = []
        for p in entry["starters"]:
            actual = actual_by_id.get(p["id"], 0)
            mult = 2 if p["id"] == entry["captain_id"] else 1
            actual_total += actual * mult
            by_player.append({**p, "actual": actual, "error": round(actual - p["predicted_xp"], 2)})

        entry["actual_points"] = actual_total
        entry["actual_by_player"] = by_player
        newly_done.append(entry)

    if newly_done:
        _save_log(log)
    return newly_done


def calibration_summary(n_recent=6):
    """Mean absolute error and bias (over/under-prediction) over the last N logged,
    reconciled gameweeks — the number to watch to know if the model is trustworthy."""
    log = [e for e in _load_log() if e["actual_points"] is not None]
    log = sorted(log, key=lambda e: e["gameweek"])[-n_recent:]
    if not log:
        return None

    total_errors = []
    pos_errors = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for entry in log:
        for p in entry["actual_by_player"]:
            total_errors.append(p["error"])
            pos_errors.setdefault(p["pos"], []).append(p["error"])

    def mae(errs):
        return round(sum(abs(e) for e in errs) / len(errs), 2) if errs else None

    def bias(errs):
        return round(sum(errs) / len(errs), 2) if errs else None

    return {
        "gameweeks_covered": [e["gameweek"] for e in log],
        "squad_predicted_vs_actual": [
            {"gw": e["gameweek"], "predicted": e["predicted_points"], "actual": e["actual_points"]}
            for e in log
        ],
        "overall_mae": mae(total_errors),
        "overall_bias": bias(total_errors),  # positive = model under-predicts, negative = over-predicts
        "by_position_mae": {pos: mae(errs) for pos, errs in pos_errors.items()},
        "by_position_bias": {pos: bias(errs) for pos, errs in pos_errors.items()},
    }
