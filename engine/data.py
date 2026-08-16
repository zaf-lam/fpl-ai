"""
data.py — pulls live data from the official Fantasy Premier League API.
No API key needed. Docs are unofficial/reverse-engineered but stable for years.
"""
import requests
import json
from pathlib import Path

BASE = "https://fantasy.premierleague.com/api"
CACHE_DIR = Path(__file__).parent.parent / "data"
CACHE_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (FPL-AI-Assistant/1.0)"}


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_bootstrap():
    """All players, teams, gameweeks, positions, prices, ownership, xG/xA, form."""
    data = _get(f"{BASE}/bootstrap-static/")
    (CACHE_DIR / "bootstrap.json").write_text(json.dumps(data))
    return data


def fetch_fixtures():
    """Full season fixture list with FDR (fixture difficulty rating)."""
    data = _get(f"{BASE}/fixtures/")
    (CACHE_DIR / "fixtures.json").write_text(json.dumps(data))
    return data


def fetch_my_team(entry_id, event=None):
    """Your current squad. entry_id = the number in the URL when you view your FPL team.
    Before the season's first gameweek finishes, there is no 'current' event yet — only
    a 'next' one — so we fall back to that (your saved picks for the upcoming gameweek)."""
    if event:
        url = f"{BASE}/entry/{entry_id}/event/{event}/picks/"
    else:
        boot = load_cached_bootstrap()
        events = boot["events"]
        target = next((e for e in events if e.get("is_current")), None)
        if target is None:
            target = next((e for e in events if e.get("is_next")), None)
        if target is None:
            target = next(e for e in events if not e["finished"])
        url = f"{BASE}/entry/{entry_id}/event/{target['id']}/picks/"
    return _get(url)


def fetch_event_live(event_id):
    """Actual points scored by every player in a specific (usually just-finished) gameweek."""
    data = _get(f"{BASE}/event/{event_id}/live/")
    return data


def load_cached_bootstrap():
    p = CACHE_DIR / "bootstrap.json"
    if not p.exists():
        return fetch_bootstrap()
    return json.loads(p.read_text())


def load_cached_fixtures():
    p = CACHE_DIR / "fixtures.json"
    if not p.exists():
        return fetch_fixtures()
    return json.loads(p.read_text())


def current_and_next_event(boot):
    events = boot["events"]
    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)
    if nxt is None:
        # preseason: nothing finished yet, "next" is first unfinished event
        nxt = next(e for e in events if not e["finished"])
    return current, nxt


if __name__ == "__main__":
    boot = fetch_bootstrap()
    fixtures = fetch_fixtures()
    print(f"Players: {len(boot['elements'])}  Teams: {len(boot['teams'])}  Fixtures: {len(fixtures)}")
