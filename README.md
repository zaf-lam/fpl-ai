# FPL AI — Squad Optimizer & Weekly Transfer Assistant

A data-driven system for Fantasy Premier League: pulls live FPL data, models
expected points per player (xG/xA + fixture difficulty + form + minutes reliability),
picks your optimal 15-man squad under real constraints (budget, club limits, formation),
and tells you who to transfer every week. Ships with a live dashboard and automatic
weekly email — both running for free on GitHub.

## How it works

| Piece | What it does |
|---|---|
| `engine/data.py` | Pulls live data from the official FPL API (no key needed) |
| `engine/xp_model.py` | Estimates expected points per player per upcoming gameweek |
| `engine/optimizer.py` | Integer linear programming (PuLP) — picks the true optimal squad/transfer under budget, position, and 3-per-club constraints |
| `engine/transfer_advisor.py` | Turns the optimizer's output into a plain-English weekly report |
| `engine/email_notify.py` | Sends that report to your inbox |
| `dashboard/index.html` | Live web dashboard (pitch view + recommendations) |
| `run_weekly.py` | The one script that runs everything |
| `.github/workflows/weekly.yml` | Runs `run_weekly.py` automatically every week, for free |

**Why GitHub Actions and not "live in the chat"?** FPL's API blocks browser-side
requests (CORS), and nothing in a chat conversation can keep running in the
background to email you every week. GitHub Actions is a free scheduled-job runner —
this is the standard, reliable way to get real weekly automation without paying for
server hosting.

## 1. Get this running locally first (5 minutes)

```bash
cd fpl-ai
pip install -r requirements.txt

# Build your initial optimal 15-man squad for the season:
python run_weekly.py

# See it on the dashboard:
cd dashboard && python -m http.server 8000
# open http://localhost:8000
```

That's your AI-picked squad for Gameweek 1, sorted into a pitch view with the
optimal starting XI, bench, and captain — built by solving the actual optimization
problem (not a heuristic guess) over every eligible Premier League player.

## 2. Once you've entered your squad on fantasy.premierleague.com

Find your team ID: go to "Points" on the FPL site, the number in the URL
(`.../entry/1234567/event/1`) is your `entry-id`.

```bash
python run_weekly.py --entry-id 1234567 --bank 0.3 --free-transfers 1
```

This pulls your actual current squad and tells you whether any transfer is worth
making this week (it only recommends a transfer if the expected points gain beats
the -4 hit cost — no chasing noise).

## 3. Set up the free weekly dashboard (GitHub Pages)

1. Push this folder to a new GitHub repo.
2. Repo Settings → Pages → Deploy from branch → `main` / `/dashboard`.
3. Your dashboard is now live at `https://<you>.github.io/<repo>/` and updates
   every week automatically.

## 4. Set up the free weekly email

1. Repo Settings → Secrets and variables → Actions → **New repository secret**, add:
   - `SMTP_HOST` — e.g. `smtp.gmail.com`
   - `SMTP_PORT` — `587`
   - `SMTP_USER` — the Gmail address you're sending from
   - `SMTP_PASS` — a Gmail **App Password** (Google Account → Security → App
     Passwords — needs 2FA enabled first; takes 2 minutes)
   - `EMAIL_TO` — where you want the report sent
2. Repo Settings → Secrets and variables → Actions → **Variables** tab, add:
   - `FPL_ENTRY_ID` — your team ID (skip this until you have a squad)
   - `FPL_BANK`, `FPL_FREE_TRANSFERS` — optional, defaults to 0 / 1
3. That's it. `.github/workflows/weekly.yml` runs every Thursday, refreshes the
   dashboard, and emails you. Change the `cron` line to match your league's deadline
   day, or trigger it manually anytime from the repo's Actions tab.

## The model, honestly

This is a genuinely useful decision-support tool, not a crystal ball — nothing
predicts football perfectly (injuries, rotation, red cards, VAR are irreducibly
uncertain). What it does well:
- Uses **underlying stats (xG/xA)** instead of raw goals/assists, which is far more
  predictive of *future* returns than past results
- Properly adjusts for **fixture difficulty** on both sides of the ball
- Enforces the same budget/club/formation rules FPL actually uses, and finds the
  provably optimal squad under those constraints (not a rule-of-thumb pick)
- Only recommends transfers when the expected gain clears the -4 point hit
- Flags blank/double gameweeks automatically

What it can't do: guarantee top-10-in-the-world. That also depends on chip timing
against the specific players everyone else has, template vs differential risk
tolerance, and plain variance over 38 gameweeks. Treat its output as a strong,
data-backed starting point each week — sanity-check injury news right before your
deadline, since team news often lands in the hour before kickoff.

## v1.1 additions

- **Time-decayed multi-horizon optimization** — the squad optimizer now maximizes a
  decay-weighted xP total (`xp_total_weighted` in `xp_model.py`) instead of a flat sum,
  so a player with one great fixture buried among four bad ones no longer gets
  overvalued.
- **`engine/captain.py`** — composite captain scoring (xP + ceiling estimate + double
  gameweek boost), not just "pick the highest-xP starter."
- **`engine/chip_strategy.py`** — detects double/blank gameweeks from the fixtures you
  already fetch and recommends Wildcard / Free Hit / Bench Boost / Triple Captain
  timing. Zero new data sources required.

### What I deliberately did NOT add, and why

If you've seen "blueprints" suggesting Understat/FBref scraping and a trained
RandomForest/XGBoost ensemble — skip them for this project:
- FPL's own API already returns Opta-sourced `expected_goals_per_90` /
  `expected_assists_per_90` per player, which is what those scrapers would get you
  anyway, with extra fragility (both sites actively rate-limit/block bots).
- A trained ML ensemble needs multi-season historical data and an ongoing retraining
  pipeline to avoid overfitting — a real project on its own, and with under a season
  of clean data it's likely to underperform a well-calibrated formula like this one.
  The formula-based model here is transparent and debuggable, which matters more than
  a black box at this scale.
- The optimizer already solves a true mixed-integer linear program (PuLP/CBC) — that
  was never the bottleneck.

## Tuning

- `--horizon 5` — how many gameweeks ahead to optimize for (raise for wildcard
  planning, lower for a single-week punt)
- Edit `form_weight` in `xp_model.py` to weight recent form vs season-long
  underlying stats
- Edit `DEFAULT_SCORING` in `xp_model.py` if FPL changes its scoring rules
