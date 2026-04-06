[README.md](https://github.com/user-attachments/files/26519110/README.md)
# THE LINE — Automated Sports Picks

Daily NBA + MLB betting intelligence powered by Claude AI.

## Setup (one time, ~20 minutes)

### 1. Get API Keys
| Service | URL | Cost | What it provides |
|---------|-----|------|-----------------|
| Anthropic | console.anthropic.com | ~$0.20/run | Claude generates picks + analysis |
| The Odds API | the-odds-api.com | Free/\$10mo | Live DraftKings lines + player props |
| OpenWeatherMap | openweathermap.org/api | Free | Ballpark weather per city |

### 2. Create GitHub Repo
1. github.com → New Repository → name: `the-line` → Public
2. Enable GitHub Pages: Settings → Pages → Source: **GitHub Actions**

### 3. Add Secrets
Settings → Secrets and variables → Actions → New repository secret:
- `ANTHROPIC_API_KEY`
- `ODDS_API_KEY`
- `WEATHER_API_KEY`

### 4. Add Files
Upload these files to your repo root:
- `generate.py` — the data engine
- `daily.yml` → goes in `.github/workflows/daily.yml`
- `index.html` — your picks template (the sports-intel.html file)
- `README.md` — this file

> **Important:** Rename `sports-intel.html` to `index.html` when uploading,
> and add `<!-- INJECT:DATA -->` just before `</body>` so the script knows
> where to inject the picks data.

### 5. Your URL
`https://YOUR-USERNAME.github.io/the-line`

---

## How to Generate Picks

### Via GitHub Website (easiest)
1. Go to your repo
2. Click **Actions** tab
3. Click **"THE LINE — Generate Daily Picks"**
4. Click **"Run workflow"** (green button, top right)
5. Fill in optional notes field
6. Click green **Run workflow**

Picks are live in ~90 seconds.

### Notes Field Examples
```
Curry OUT per Shams. PHI/COL postponed rain delay.
Luka upgraded to probable 45min before tip.
LAD/SD game pushed 2hrs — weather in SD.
Edwards playing through knee — monitor minutes.
```

### Re-run Anytime
You can trigger it multiple times per day. Each run overwrites `index.html`
with the freshest data. Great for late scratches or line movement.

---

## Data Sources (all wired in)

| Source | What it provides | Access |
|--------|-----------------|--------|
| The Odds API | Live DK lines, spreads, totals, player props | API key |
| StatMuse | PPG, RPG, APG, wRC+, ERA, OPS, HR leaders | Free scrape |
| ESPN Injuries | Confirmed OUT/QUESTIONABLE for all rosters | Free scrape |
| Baseball Savant | xBA, xSLG, xERA, barrel rate, park factors | Free CSV endpoint |
| FanGraphs | FIP, xFIP, wRC+, wOBA, platoon splits | Free API endpoint |
| NBA.com Stats | Official tracking, matchup data, DRTG | Free unofficial API |
| ScoresAndOdds | Live line movement, public splits | Free scrape |
| Covers | ATS records, consensus picks | Free scrape |
| OpenWeatherMap | Ballpark weather — temp, wind, humidity | Free API |
| ESPN Schedule | Today's games, starters, venues | Free API |

---

## Cost Estimate
- Anthropic API: ~$0.15–0.40 per run
- The Odds API: Free tier (500 req/mo) or \$10/mo for daily use
- Everything else: Free

**~\$10–15/month total** for daily picks with full data stack.
