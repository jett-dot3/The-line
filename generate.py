"""
THE LINE — Daily Sports Intelligence Generator
generate.py

Run this script manually or via GitHub Actions to regenerate
your picks page with fully live data from all sources.

Required env vars:
  ANTHROPIC_API_KEY   — from console.anthropic.com
  ODDS_API_KEY        — from the-odds-api.com
  WEATHER_API_KEY     — from openweathermap.org (free tier)

Optional (picks still generate without these):
  ROTOWIRE_API_KEY    — from rotowire.com (starting lineups)

Usage:
  python generate.py
  python generate.py --notes "Curry out, PHI/COL postponed"
  python generate.py --date 2026-04-10
"""

import os, sys, json, requests, datetime, argparse, time
from pathlib import Path

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ODDS_API_KEY      = os.environ.get("ODDS_API_KEY", "")
WEATHER_API_KEY   = os.environ.get("WEATHER_API_KEY", "")

NBA_PARKS = {}   # NBA doesn't need weather
MLB_PARKS = {
    "Arizona Diamondbacks":    ("33.4453", "-112.0667", "Phoenix AZ",       1082),
    "Atlanta Braves":          ("33.8908", "-84.4678",  "Cumberland GA",    1050),
    "Baltimore Orioles":       ("39.2838", "-76.6218",  "Baltimore MD",       30),
    "Boston Red Sox":          ("42.3467", "-71.0972",  "Boston MA",          20),
    "Chicago Cubs":            ("41.9484", "-87.6554",  "Chicago IL",         595),
    "Chicago White Sox":       ("41.8299", "-87.6338",  "Chicago IL",         595),
    "Cincinnati Reds":         ("39.0979", "-84.5082",  "Cincinnati OH",      490),
    "Cleveland Guardians":     ("41.4962", "-81.6852",  "Cleveland OH",       660),
    "Colorado Rockies":        ("39.7559", "-104.9942", "Denver CO",         5200),
    "Detroit Tigers":          ("42.3390", "-83.0485",  "Detroit MI",         600),
    "Houston Astros":          ("29.7572", "-95.3554",  "Houston TX",          43),
    "Kansas City Royals":      ("39.0517", "-94.4803",  "Kansas City MO",     750),
    "Los Angeles Angels":      ("33.8003", "-117.8827", "Anaheim CA",         160),
    "Los Angeles Dodgers":     ("34.0739", "-118.2400", "Los Angeles CA",     515),
    "Miami Marlins":           ("25.7781", "-80.2197",  "Miami FL",            10),
    "Milwaukee Brewers":       ("43.0283", "-87.9712",  "Milwaukee WI",       635),
    "Minnesota Twins":         ("44.9817", "-93.2778",  "Minneapolis MN",     815),
    "New York Mets":           ("40.7571", "-73.8458",  "Queens NY",           20),
    "New York Yankees":        ("40.8296", "-73.9262",  "Bronx NY",            55),
    "Oakland Athletics":       ("37.7516", "-122.2005", "Oakland CA",          20),
    "Philadelphia Phillies":   ("39.9061", "-75.1665",  "Philadelphia PA",     20),
    "Pittsburgh Pirates":      ("40.4469", "-80.0057",  "Pittsburgh PA",      745),
    "San Diego Padres":        ("32.7073", "-117.1566", "San Diego CA",        20),
    "San Francisco Giants":    ("37.7786", "-122.3893", "San Francisco CA",    10),
    "Seattle Mariners":        ("47.5913", "-122.3325", "Seattle WA",          20),
    "St. Louis Cardinals":     ("38.6226", "-90.1928",  "St. Louis MO",       535),
    "Tampa Bay Rays":          ("27.7682", "-82.6534",  "St. Petersburg FL",   15),
    "Texas Rangers":           ("32.7474", "-97.0836",  "Arlington TX",       551),
    "Toronto Blue Jays":       ("43.6414", "-79.3894",  "Toronto ON",         300),
    "Washington Nationals":    ("38.8730", "-77.0074",  "Washington DC",       25),
}

STATMUSE_NBA_URLS = {
    "ppg":   "https://www.statmuse.com/nba/ask/nba-players-points-per-game-this-season",
    "rpg":   "https://www.statmuse.com/nba/ask/nba-players-rebounds-per-game-this-season",
    "apg":   "https://www.statmuse.com/nba/ask/nba-players-assists-per-game-this-season",
    "drtg":  "https://www.statmuse.com/nba/ask/nba-team-defensive-ratings",
}
STATMUSE_MLB_URLS = {
    "ops":   "https://www.statmuse.com/mlb/ask/ops-leaders-this-season",
    "hr":    "https://www.statmuse.com/mlb/ask/home-run-leaders-2026-season",
    "wrcplus": "https://www.statmuse.com/mlb/ask/wrc-plus-leaders-2026-season",
    "era":   "https://www.statmuse.com/mlb/ask/era-leaders-2026-season",
    "avg":   "https://www.statmuse.com/mlb/ask/all-players-batting-averages-2026-season",
}

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def safe_get(url, headers=None, params=None, timeout=20, label="", retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
            print(f"  [WARN] {label or url[:60]}: {e}")
            return None

def safe_json(url, headers=None, params=None, label=""):
    r = safe_get(url, headers=headers, params=params, label=label)
    if r:
        try:
            return r.json()
        except:
            return None
    return None

def truncate(text, chars=3000):
    return text[:chars] + "..." if len(text) > chars else text

# ─────────────────────────────────────────
# 1. THE ODDS API — DraftKings lines
# ─────────────────────────────────────────
def fetch_odds():
    if not ODDS_API_KEY:
        print("  [SKIP] No ODDS_API_KEY — using estimated lines")
        return {"nba": [], "mlb": []}

    base = "https://api.the-odds-api.com/v4/sports"
    out = {}
    for sport, key in [("basketball_nba", "nba"), ("baseball_mlb", "mlb")]:
        url = f"{base}/{sport}/odds/"
        data = safe_json(url, params={
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "bookmakers": "draftkings",
            "oddsFormat": "american"
        }, label=f"Odds API {key.upper()}")
        out[key] = data or []
        time.sleep(0.5)

    # Also fetch player props for NBA
    props_url = f"{base}/basketball_nba/events"
    events = safe_json(props_url, params={"apiKey": ODDS_API_KEY}, label="NBA events list")
    nba_props = []
    if events:
        for ev in events[:6]:  # limit to 6 games to save API calls
            eid = ev.get("id")
            prop_data = safe_json(
                f"{base}/basketball_nba/events/{eid}/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "us",
                    "markets": "player_points,player_rebounds,player_assists",
                    "bookmakers": "draftkings",
                    "oddsFormat": "american"
                }, label=f"Props {ev.get('home_team','')[:20]}")
            if prop_data:
                nba_props.append(prop_data)
            time.sleep(0.3)
    out["nba_props"] = nba_props
    return out

# ─────────────────────────────────────────
# 2. STATMUSE — season stats
# ─────────────────────────────────────────
def fetch_statmuse():
    results = {"nba": {}, "mlb": {}}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    for key, url in STATMUSE_NBA_URLS.items():
        r = safe_get(url, headers=headers, label=f"StatMuse NBA {key}")
        if r:
            # Extract the table markdown — grab first 3000 chars after the table header
            txt = r.text
            start = txt.find("| 1 |")
            results["nba"][key] = txt[start:start+4000] if start > -1 else ""
        time.sleep(0.8)

    for key, url in STATMUSE_MLB_URLS.items():
        r = safe_get(url, headers=headers, label=f"StatMuse MLB {key}")
        if r:
            txt = r.text
            start = txt.find("| 1 |")
            results["mlb"][key] = txt[start:start+4000] if start > -1 else ""
        time.sleep(0.8)

    return results

# ─────────────────────────────────────────
# 3. ESPN INJURIES
# ─────────────────────────────────────────
def fetch_espn_injuries():
    headers = {"User-Agent": "Mozilla/5.0"}
    out = {}
    for sport, url in [
        ("nba", "https://www.espn.com/nba/injuries"),
        ("mlb", "https://www.espn.com/mlb/injuries"),
    ]:
        r = safe_get(url, headers=headers, label=f"ESPN {sport.upper()} injuries")
        if r:
            # Pull table rows — grab key OUT/QUESTIONABLE entries
            txt = r.text
            start = txt.find("| NAME |")
            out[sport] = txt[start:start+8000] if start > -1 else ""
        time.sleep(0.5)
    return out

# ─────────────────────────────────────────
# 4. BASEBALL SAVANT — Statcast CSV endpoints
# ─────────────────────────────────────────
def fetch_statcast():
    """
    Baseball Savant has direct CSV export endpoints that bypass JS rendering.
    We pull xBA, xSLG, xERA, barrel rate, exit velocity for 2026 season.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    year = datetime.date.today().year
    results = {}

    # Expected statistics (xBA, xSLG, xwOBA) — batters
    batter_url = (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=batter&year={year}&position=&team=&min=10&csv=true"
    )
    r = safe_get(batter_url, headers=headers, label="Savant xStats batters")
    if r and "," in r.text[:100]:  # verify it's CSV
        results["xstats_batters"] = r.text[:5000]

    # Expected statistics — pitchers
    pitcher_url = (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=pitcher&year={year}&position=&team=&min=5&csv=true"
    )
    r = safe_get(pitcher_url, headers=headers, label="Savant xStats pitchers")
    if r and "," in r.text[:100]:
        results["xstats_pitchers"] = r.text[:5000]

    # Barrel rate leaderboard — batters (power indicator)
    barrel_url = (
        f"https://baseballsavant.mlb.com/leaderboard/statcast"
        f"?type=batter&year={year}&position=&team=&min=10&csv=true"
    )
    r = safe_get(barrel_url, headers=headers, label="Savant barrel rates")
    if r and "," in r.text[:100]:
        results["barrels"] = r.text[:5000]

    # Sprint speed (running game, basestealing)
    sprint_url = (
        f"https://baseballsavant.mlb.com/leaderboard/sprint_speed"
        f"?year={year}&position=&team=&min=10&csv=true"
    )
    r = safe_get(sprint_url, headers=headers, label="Savant sprint speed")
    if r and "," in r.text[:100]:
        results["sprint_speed"] = r.text[:3000]

    # Park factors — important for environmental adjustments
    park_url = (
        f"https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
        f"?type=Batter&batSide=&stat=index_wOBA&condition=Is&rolling=no&year={year}&csv=true"
    )
    r = safe_get(park_url, headers=headers, label="Savant park factors")
    if r and "," in r.text[:100]:
        results["park_factors"] = r.text[:3000]

    time.sleep(0.5)
    return results

# ─────────────────────────────────────────
# 5. FANGRAPHS — CSV export endpoints
# (bypasses JS by hitting the API layer directly)
# ─────────────────────────────────────────
def fetch_fangraphs():
    """
    FanGraphs leaderboard pages are JS-rendered but their underlying
    data API returns JSON/CSV directly. We target those endpoints.
    """
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.fangraphs.com/",
        "Accept": "application/json, text/plain, */*",
    }
    year = datetime.date.today().year
    results = {}

    # Splits leaderboard API endpoint (bypasses JS)
    splits_url = "https://www.fangraphs.com/api/leaders/splits/splits-leaders"
    params = {
        "strPlayerId":   "",
        "strStat":       "1",   # 1 = batting
        "strGroup":      "career",
        "strSplit":      "",
        "strSplitArr":   "",
        "strSplitArrPitch": "",
        "strType":       "1",   # standard stats
        "strStartDate":  f"{year}-03-01",
        "strEndDate":    f"{year}-11-01",
        "strSplitTeams": "false",
        "strAutoPt":     "true",
        "strPosition":   "B",
        "strTeam":       "",
        "strPlayerid":   "",
    }
    r = safe_get(splits_url, headers=headers, params=params, label="FanGraphs splits API")
    if r:
        try:
            data = r.json()
            results["splits"] = json.dumps(data)[:5000]
        except:
            results["splits"] = truncate(r.text)

    # FanGraphs leaderboard API — batting
    leader_url = "https://www.fangraphs.com/api/leaders/major-league/data"
    leader_params = {
        "age":      "",
        "pos":      "all",
        "stats":    "bat",
        "lg":       "all",
        "qual":     "0",
        "season":   str(year),
        "season1":  str(year),
        "startdate": f"{year}-03-01",
        "enddate":   f"{year}-11-01",
        "month":    "0",
        "hand":     "",
        "team":     "0",
        "pageitems": "50",
        "pagenum":  "1",
        "ind":      "0",
        "rost":     "0",
        "players":  "",
        "type":     "8",
        "postseason": "",
        "sortdir":  "default",
        "sortstat": "WAR",
    }
    r = safe_get(leader_url, headers=headers, params=leader_params, label="FanGraphs batting leaders API")
    if r:
        try:
            data = r.json()
            results["batting_leaders"] = json.dumps(data)[:5000]
        except:
            results["batting_leaders"] = truncate(r.text)

    # FanGraphs leaderboard API — pitching
    leader_params["stats"] = "pit"
    leader_params["sortstat"] = "ERA"
    r = safe_get(leader_url, headers=headers, params=leader_params, label="FanGraphs pitching leaders API")
    if r:
        try:
            data = r.json()
            results["pitching_leaders"] = json.dumps(data)[:5000]
        except:
            results["pitching_leaders"] = truncate(r.text)

    time.sleep(0.5)
    return results

# ─────────────────────────────────────────
# 6. NBA.COM STATS API
# ─────────────────────────────────────────
def fetch_nba_stats():
    """
    NBA.com has an unofficial but stable JSON API.
    Requires specific headers to avoid 403s.
    Returns: player tracking, defensive matchups, team advanced stats.
    """
    headers = {
        "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer":      "https://www.nba.com/",
        "Origin":       "https://www.nba.com",
        "Accept":       "application/json, text/plain, */*",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token":  "true",
        "Connection":   "keep-alive",
    }
    season = "2025-26"
    results = {}

    # Team defensive ratings (confirmed working)
    def_url = "https://stats.nba.com/stats/leaguedashteamstats"
    def_params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "PerMode": "PerGame",
        "MeasureType": "Defense",
        "PaceAdjust": "N",
        "PlusMinus": "N",
        "Rank": "N",
        "Outcome": "",
        "Location": "",
        "Month": "0",
        "SeasonSegment": "",
        "DateFrom": "",
        "DateTo": "",
        "OpponentTeamID": "0",
        "VsConference": "",
        "VsDivision": "",
        "GameSegment": "",
        "Period": "0",
        "ShotClockRange": "",
        "LastNGames": "0",
        "Conference": "",
        "Division": "",
        "TwoWay": "0",
    }
    r = safe_get(def_url, headers=headers, params=def_params, timeout=30, label="NBA.com team defense")
    if r:
        try:
            data = r.json()
            headers_row = data["resultSets"][0]["headers"]
            rows = data["resultSets"][0]["rowSet"][:30]
            results["team_defense"] = {
                "headers": headers_row,
                "rows": rows
            }
        except Exception as e:
            print(f"  [WARN] NBA.com team defense parse error: {e}")
            results["team_defense"] = {}
    time.sleep(2)

    # Player general stats — per game
    player_url = "https://stats.nba.com/stats/leaguedashplayerstats"
    player_params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "PerMode": "PerGame",
        "MeasureType": "Base",
        "PaceAdjust": "N",
        "PlusMinus": "N",
        "Rank": "N",
        "Outcome": "", "Location": "", "Month": "0",
        "SeasonSegment": "", "DateFrom": "", "DateTo": "",
        "OpponentTeamID": "0", "VsConference": "", "VsDivision": "",
        "GameSegment": "", "Period": "0", "ShotClockRange": "",
        "LastNGames": "0", "Conference": "", "Division": "",
        "TwoWay": "0", "LeagueID": "00",
    }
    r = safe_get(player_url, headers=headers, params=player_params, timeout=30, label="NBA.com player stats")
    if r:
        try:
            data = r.json()
            hdrs = data["resultSets"][0]["headers"]
            rows = data["resultSets"][0]["rowSet"]
            # Sort by PTS index descending, take top 50
            pts_idx = hdrs.index("PTS")
            rows_sorted = sorted(rows, key=lambda x: x[pts_idx] or 0, reverse=True)[:50]
            results["player_stats"] = {"headers": hdrs, "rows": rows_sorted}
        except Exception as e:
            results["player_stats"] = {}
    time.sleep(1)

    # Player hustle/tracking stats (drives, pull-up, catch-shoot)
    track_url = "https://stats.nba.com/stats/leaguedashptstats"
    track_params = {
        "Season": season, "SeasonType": "Regular Season",
        "PerMode": "PerGame", "PlayerOrTeam": "Player",
        "PtMeasureType": "Drives",
        "Outcome": "", "Location": "", "Month": "0",
        "SeasonSegment": "", "DateFrom": "", "DateTo": "",
        "OpponentTeamID": "0", "VsConference": "", "VsDivision": "",
        "GameSegment": "", "Period": "0", "LastNGames": "0",
        "Conference": "", "Division": "", "LeagueID": "00",
    }
    r = safe_get(track_url, headers=headers, params=track_params, timeout=30, label="NBA.com tracking drives")
    if r:
        try:
            data = r.json()
            hdrs = data["resultSets"][0]["headers"]
            rows = data["resultSets"][0]["rowSet"][:40]
            results["tracking_drives"] = {"headers": hdrs, "rows": rows}
        except:
            results["tracking_drives"] = {}
    time.sleep(2)

    # Defensive matchup data
    matchup_url = "https://stats.nba.com/stats/leaguedashptdefend"
    matchup_params = {
        "Season": season, "SeasonType": "Regular Season",
        "PerMode": "PerGame", "DefenseCategory": "Overall",
        "LeagueID": "00", "Outcome": "", "Location": "",
        "Month": "0", "SeasonSegment": "", "DateFrom": "", "DateTo": "",
        "OpponentTeamID": "0", "VsConference": "", "VsDivision": "",
        "GameSegment": "", "Period": "0", "LastNGames": "0",
    }
    r = safe_get(matchup_url, headers=headers, params=matchup_params, timeout=30, label="NBA.com defensive matchups")
    if r:
        try:
            data = r.json()
            hdrs = data["resultSets"][0]["headers"]
            rows = data["resultSets"][0]["rowSet"][:50]
            results["defensive_matchups"] = {"headers": hdrs, "rows": rows}
        except:
            results["defensive_matchups"] = {}
    time.sleep(1)

    return results

# ─────────────────────────────────────────
# 7. COVERS — ATS records & consensus
# (Their consensus/trends pages that are accessible)
# ─────────────────────────────────────────
def fetch_covers():
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    results = {}

    # NBA team trends page (ATS records)
    nba_ats_url = "https://www.covers.com/sport/basketball/nba/teams/team-trends"
    r = safe_get(nba_ats_url, headers=headers, label="Covers NBA ATS trends")
    if r:
        txt = r.text
        start = txt.find("ATS")
        results["nba_ats"] = truncate(txt[max(0, start-200):start+3000]) if start > -1 else ""

    # MLB team trends page (ATS records)
    mlb_ats_url = "https://www.covers.com/sport/baseball/mlb/teams/team-trends"
    r = safe_get(mlb_ats_url, headers=headers, label="Covers MLB ATS trends")
    if r:
        txt = r.text
        start = txt.find("ATS")
        results["mlb_ats"] = truncate(txt[max(0, start-200):start+3000]) if start > -1 else ""

    # Covers public consensus — NBA (this URL confirmed accessible)
    consensus_url = "https://contests.covers.com/consensus/topconsensus/nba/overall"
    r = safe_get(consensus_url, headers=headers, label="Covers NBA consensus")
    if r:
        results["nba_consensus"] = truncate(r.text)

    # Covers public consensus — MLB
    mlb_consensus_url = "https://contests.covers.com/consensus/topconsensus/mlb/overall"
    r = safe_get(mlb_consensus_url, headers=headers, label="Covers MLB consensus")
    if r:
        results["mlb_consensus"] = truncate(r.text)

    time.sleep(0.5)
    return results

# ─────────────────────────────────────────
# 8. SCORESANDODDS — public betting splits
# (replacement for Action Network which blocks scrapers)
# ─────────────────────────────────────────
def fetch_public_betting():
    """
    ScoresAndOdds shows live public betting splits and line movement.
    Action Network blocks all programmatic access, so we use
    ScoresAndOdds + The Odds API line movement data instead.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    results = {}

    # NBA live odds + public splits
    nba_url = "https://www.scoresandodds.com/nba"
    r = safe_get(nba_url, headers=headers, label="ScoresAndOdds NBA")
    if r:
        txt = r.text
        # Find the odds tables
        start = txt.find("Moneyline")
        results["nba_lines"] = truncate(txt[max(0,start-100):start+5000]) if start > -1 else ""

    # MLB live odds
    mlb_url = "https://www.scoresandodds.com/mlb"
    r = safe_get(mlb_url, headers=headers, label="ScoresAndOdds MLB")
    if r:
        txt = r.text
        start = txt.find("Moneyline")
        results["mlb_lines"] = truncate(txt[max(0,start-100):start+5000]) if start > -1 else ""

    time.sleep(0.5)
    return results

# ─────────────────────────────────────────
# 9. WEATHER — per ballpark
# ─────────────────────────────────────────
def fetch_weather(mlb_game_teams):
    if not WEATHER_API_KEY:
        print("  [SKIP] No WEATHER_API_KEY — skipping weather")
        return {}

    results = {}
    fetched = set()

    for team in mlb_game_teams:
        if team not in MLB_PARKS or team in fetched:
            continue
        lat, lon, city, altitude = MLB_PARKS[team]
        url = "https://api.openweathermap.org/data/2.5/forecast"
        data = safe_json(url, params={
            "lat": lat, "lon": lon,
            "appid": WEATHER_API_KEY,
            "units": "imperial",
            "cnt": 8
        }, label=f"Weather {city}")
        if data:
            results[team] = {
                "city": city,
                "altitude_ft": altitude,
                "forecast": [
                    {
                        "time": f["dt_txt"],
                        "temp_f": f["main"]["temp"],
                        "wind_mph": round(f["wind"]["speed"] * 2.237, 1),
                        "wind_dir": f["wind"].get("deg", 0),
                        "humidity": f["main"]["humidity"],
                        "precip_chance": round(f.get("pop", 0) * 100),
                        "description": f["weather"][0]["description"],
                    }
                    for f in data.get("list", [])[:4]
                ]
            }
        fetched.add(team)
        time.sleep(0.3)

    return results

# ─────────────────────────────────────────
# 10. TODAY'S SCHEDULE
# ─────────────────────────────────────────
def fetch_schedule():
    today = datetime.date.today().isoformat()
    results = {}

    # NBA schedule via ESPN
    nba_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={today.replace('-','')}"
    data = safe_json(nba_url, label="ESPN NBA schedule")
    if data:
        games = []
        for ev in data.get("events", []):
            comps = ev.get("competitions", [{}])[0]
            competitors = comps.get("competitors", [])
            if len(competitors) >= 2:
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
                games.append({
                    "id": ev.get("id"),
                    "time": ev.get("date"),
                    "home": home.get("team", {}).get("displayName", ""),
                    "away": away.get("team", {}).get("displayName", ""),
                    "venue": comps.get("venue", {}).get("fullName", ""),
                    "status": ev.get("status", {}).get("type", {}).get("name", ""),
                })
        results["nba"] = games

    # MLB schedule via ESPN
    mlb_url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={today.replace('-','')}"
    data = safe_json(mlb_url, label="ESPN MLB schedule")
    if data:
        games = []
        for ev in data.get("events", []):
            comps = ev.get("competitions", [{}])[0]
            competitors = comps.get("competitors", [])
            if len(competitors) >= 2:
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
                sp = comps.get("situation", {})
                games.append({
                    "id": ev.get("id"),
                    "time": ev.get("date"),
                    "home": home.get("team", {}).get("displayName", ""),
                    "away": away.get("team", {}).get("displayName", ""),
                    "venue": comps.get("venue", {}).get("fullName", ""),
                    "status": ev.get("status", {}).get("type", {}).get("name", ""),
                    "home_pitcher": home.get("probables", [{}])[0].get("athlete", {}).get("displayName", "") if home.get("probables") else "",
                    "away_pitcher": away.get("probables", [{}])[0].get("athlete", {}).get("displayName", "") if away.get("probables") else "",
                })
        results["mlb"] = games

    return results

# ─────────────────────────────────────────
# 11. CLAUDE API — Generate picks
# ─────────────────────────────────────────
def generate_picks(data_bundle, notes="", target_date=""):
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is required")

    today_str = target_date or datetime.date.today().strftime("%B %d, %Y")

    # Build the data context — summarize each source
    context_parts = [f"DATE: {today_str}"]

    if data_bundle.get("schedule", {}).get("nba"):
        context_parts.append(f"\nNBA SCHEDULE TODAY ({len(data_bundle['schedule']['nba'])} games):\n" +
            json.dumps(data_bundle["schedule"]["nba"], indent=2)[:3000])

    if data_bundle.get("schedule", {}).get("mlb"):
        context_parts.append(f"\nMLB SCHEDULE TODAY ({len(data_bundle['schedule']['mlb'])} games):\n" +
            json.dumps(data_bundle["schedule"]["mlb"], indent=2)[:3000])

    if data_bundle.get("odds", {}).get("nba"):
        context_parts.append(f"\nDRAFTKINGS NBA ODDS (live):\n" +
            json.dumps(data_bundle["odds"]["nba"][:8], indent=2)[:4000])

    if data_bundle.get("odds", {}).get("mlb"):
        context_parts.append(f"\nDRAFTKINGS MLB ODDS (live):\n" +
            json.dumps(data_bundle["odds"]["mlb"][:8], indent=2)[:4000])

    if data_bundle.get("odds", {}).get("nba_props"):
        context_parts.append(f"\nDRAFTKINGS NBA PLAYER PROPS (live):\n" +
            json.dumps(data_bundle["odds"]["nba_props"][:4], indent=2)[:4000])

    if data_bundle.get("nba_stats", {}).get("team_defense", {}).get("rows"):
        td = data_bundle["nba_stats"]["team_defense"]
        context_parts.append(f"\nNBA TEAM DEFENSIVE RATINGS (NBA.com official):\n" +
            f"Headers: {td['headers']}\n" +
            "\n".join(str(r) for r in td["rows"][:30]))

    if data_bundle.get("nba_stats", {}).get("player_stats", {}).get("rows"):
        ps = data_bundle["nba_stats"]["player_stats"]
        context_parts.append(f"\nNBA PLAYER STATS — TOP 50 BY PPG (NBA.com official):\n" +
            f"Headers: {ps['headers']}\n" +
            "\n".join(str(r) for r in ps["rows"][:50]))

    if data_bundle.get("nba_stats", {}).get("tracking_drives", {}).get("rows"):
        td = data_bundle["nba_stats"]["tracking_drives"]
        context_parts.append(f"\nNBA TRACKING — DRIVES (NBA.com):\n" +
            f"Headers: {td['headers']}\n" +
            "\n".join(str(r) for r in td["rows"][:30]))

    for key, label in [("ppg","PPG"), ("rpg","RPG"), ("apg","APG"), ("drtg","DRTG")]:
        val = data_bundle.get("statmuse_nba", {}).get(key, "")
        if val:
            context_parts.append(f"\nSTATMUSE NBA {label}:\n{val[:2000]}")

    for key, label in [("ops","OPS"), ("hr","HR"), ("wrcplus","wRC+"), ("era","ERA"), ("avg","AVG")]:
        val = data_bundle.get("statmuse_mlb", {}).get(key, "")
        if val:
            context_parts.append(f"\nSTATMUSE MLB {label}:\n{val[:2000]}")

    if data_bundle.get("statcast", {}).get("xstats_batters"):
        context_parts.append(f"\nBASEBALL SAVANT — xSTATS BATTERS (xBA, xSLG, xwOBA, barrel%):\n" +
            data_bundle["statcast"]["xstats_batters"][:3000])

    if data_bundle.get("statcast", {}).get("xstats_pitchers"):
        context_parts.append(f"\nBASEBALL SAVANT — xSTATS PITCHERS (xERA, xBA against):\n" +
            data_bundle["statcast"]["xstats_pitchers"][:3000])

    if data_bundle.get("statcast", {}).get("barrels"):
        context_parts.append(f"\nBASEBALL SAVANT — BARREL RATES & EXIT VELOCITY:\n" +
            data_bundle["statcast"]["barrels"][:2000])

    if data_bundle.get("statcast", {}).get("park_factors"):
        context_parts.append(f"\nBASEBALL SAVANT — PARK FACTORS (2026):\n" +
            data_bundle["statcast"]["park_factors"][:2000])

    if data_bundle.get("fangraphs", {}).get("batting_leaders"):
        context_parts.append(f"\nFANGRAPHS BATTING LEADERS (WAR, wRC+, wOBA, OPS):\n" +
            data_bundle["fangraphs"]["batting_leaders"][:3000])

    if data_bundle.get("fangraphs", {}).get("pitching_leaders"):
        context_parts.append(f"\nFANGRAPHS PITCHING LEADERS (ERA, FIP, xFIP, K%):\n" +
            data_bundle["fangraphs"]["pitching_leaders"][:3000])

    if data_bundle.get("fangraphs", {}).get("splits"):
        context_parts.append(f"\nFANGRAPHS SPLITS (LHP vs RHP, Home/Away):\n" +
            data_bundle["fangraphs"]["splits"][:3000])

    injuries = data_bundle.get("injuries", {})
    if injuries.get("nba"):
        context_parts.append(f"\nESPN NBA INJURY REPORT:\n{injuries['nba'][:3000]}")
    if injuries.get("mlb"):
        context_parts.append(f"\nESPN MLB INJURY REPORT:\n{injuries['mlb'][:3000]}")

    if data_bundle.get("weather"):
        weather_summary = []
        for team, w in data_bundle["weather"].items():
            fc = w["forecast"][0] if w["forecast"] else {}
            weather_summary.append(
                f"{team} ({w['city']}, {w['altitude_ft']}ft): "
                f"{fc.get('temp_f','?')}°F, wind {fc.get('wind_mph','?')}mph "
                f"@ {fc.get('wind_dir','?')}°, {fc.get('precip_chance','?')}% precip, "
                f"{fc.get('description','')}"
            )
        context_parts.append(f"\nWEATHER BY BALLPARK:\n" + "\n".join(weather_summary))

    if data_bundle.get("public_betting", {}).get("nba_lines"):
        context_parts.append(f"\nLIVE NBA LINES + PUBLIC SPLITS (ScoresAndOdds):\n" +
            data_bundle["public_betting"]["nba_lines"][:2000])

    if data_bundle.get("public_betting", {}).get("mlb_lines"):
        context_parts.append(f"\nLIVE MLB LINES (ScoresAndOdds):\n" +
            data_bundle["public_betting"]["mlb_lines"][:2000])

    if notes:
        context_parts.append(f"\n\n🚨 BREAKING ANALYST NOTES — FACTOR THESE INTO ALL PICKS:\n{notes}\n"
                              f"(If a game is postponed, remove it entirely. "
                              f"If an injury status changed, adjust all related picks and parlays.)")

    full_context = "\n".join(context_parts)

    prompt = f"""You are THE LINE, an elite sports betting analyst with 15 years of experience.
You have access to the most comprehensive dataset available:
- Live DraftKings moneylines, spreads, totals, and player prop lines
- NBA.com official tracking stats, defensive matchups, drive data
- Baseball Savant Statcast metrics (xBA, xSLG, xERA, barrel rate, exit velocity, park factors)
- FanGraphs advanced metrics (FIP, xFIP, wRC+, wOBA, platoon splits)
- StatMuse season leaders for all key categories
- ESPN confirmed injury reports
- Live ballpark weather with wind direction, temperature, humidity
- Real-time public betting lines

TODAY'S DATA:
{full_context}

TASK: Generate today's complete sports betting intelligence report in the following exact JSON format.
Every pick must cite specific data points from the provided datasets. Be sharp, specific, and analytical.

Return ONLY valid JSON, no markdown, no preamble:

{{
  "date": "{today_str}",
  "nba_games": [
    {{
      "matchup": "AWAY @ HOME",
      "time": "7:00 PM ET",
      "venue": "Arena Name",
      "pick": "TEAM -ML or TEAM -X.X spread",
      "pick_type": "ML or SPREAD or TOTAL",
      "dk_line": "-XXX or +XXX",
      "dk_spread": "TEAM -X.X (-110)",
      "dk_total": "O/U XXX.X",
      "win_probability": "XX%",
      "confidence": 4,
      "article": "3-4 sentence sharp analysis citing specific stats from the data...",
      "key_stats": ["Stat 1 with number", "Stat 2", "Stat 3", "Stat 4"],
      "injuries": ["Player OUT (injury)", "Player QUESTIONABLE"],
      "verdict": "BET: [exact bet] — [one sentence rationale]"
    }}
  ],
  "mlb_games": [
    {{
      "matchup": "AWAY @ HOME",
      "time": "6:05 PM ET",
      "venue": "Stadium Name",
      "home_pitcher": "Name (ERA, WHIP)",
      "away_pitcher": "Name (ERA, WHIP)",
      "pick": "TEAM -ML or OVER/UNDER",
      "pick_type": "ML or TOTAL",
      "dk_line": "-XXX or +XXX",
      "dk_total": "O/U X.X",
      "confidence": 3,
      "environmental": {{
        "temp_f": 72,
        "wind_mph": 8,
        "wind_direction": "blowing out to CF",
        "altitude_ft": 5200,
        "park_factor": "hitter-friendly",
        "precip_chance": 10
      }},
      "statcast_edge": "Specific xBA/xSLG/barrel rate insight from the data",
      "fangraphs_edge": "Specific FIP/wRC+/platoon split insight",
      "article": "3-4 sentence sharp analysis citing Statcast, FanGraphs, weather...",
      "key_stats": ["wRC+ or xBA stat", "ERA vs FIP gap", "Park factor", "Weather factor"],
      "injuries": ["Player OUT"],
      "verdict": "BET: [exact bet] — [one sentence rationale]"
    }}
  ],
  "props": [
    {{
      "sport": "NBA or MLB",
      "player": "Full Name",
      "team": "TEAM",
      "prop_type": "Points / Rebounds / Assists / Total Bases / Strikeouts / HR",
      "direction": "OVER or UNDER",
      "line": "27.5",
      "dk_odds": "-115",
      "season_avg": "26.8",
      "matchup_edge": "Specific opponent defensive weakness from NBA.com matchup data",
      "statcast_edge": "Barrel rate or exit velo advantage (MLB only)",
      "reasoning": "2-3 sentence analysis with specific data points",
      "confidence": 4
    }}
  ],
  "parlays": [
    {{
      "name": "The Safe House",
      "risk_level": "LOW",
      "legs": [
        {{
          "pick": "BOS -ML",
          "dk_odds": "-650",
          "decimal": 1.154,
          "reasoning": "One sentence"
        }}
      ],
      "total_decimal": 1.384,
      "american_odds": "+284",
      "payout_100": "$384",
      "payout_50": "$192",
      "confidence": "HIGH"
    }}
  ]
}}

Generate 5 parlays ranging from 2-leg LOW RISK to 7-leg ULTRA RISK.
Calculate all parlay math precisely: multiply decimals, convert to American odds.
Use actual DK lines from the data for all calculations.
Flag any postponed games and exclude them completely.
"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 8000,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=120
    )

    print(f"  [API] Anthropic status: {response.status_code}")

    if response.status_code == 401:
        print("  [ERROR] 401 Unauthorized — billing not set up or key is invalid.")
        print("  [FIX]   Go to console.anthropic.com -> Billing -> add card + credits.")
        print("  [FIX]   Then API Keys -> delete old key -> create new -> update GitHub secret.")
        sys.exit(1)

    if response.status_code == 429:
        print("  [ERROR] 429 Rate limited — wait 60 seconds and try again.")
        sys.exit(1)

    response.raise_for_status()
    raw = response.json()["content"][0]["text"]

    # Robust JSON cleaning — handle all common Claude response formats
    raw = raw.strip()

    # Strip markdown code fences if present
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    # Find JSON boundaries as fallback
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start > -1 and end > start:
            raw = raw[start:end]

    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Log the raw response for debugging and return a safe fallback
        print(f"  [ERROR] JSON parse failed: {e}")
        print(f"  [DEBUG] Raw response first 500 chars: {raw[:500]}")
        # Return minimal valid structure so the script doesn't crash
        return {
            "date": today_str,
            "nba_games": [],
            "mlb_games": [],
            "props": [],
            "parlays": [],
            "error": f"JSON parse failed: {str(e)}. Raw: {raw[:200]}"
        }

# ─────────────────────────────────────────
# 12. HTML INJECTION
# ─────────────────────────────────────────
def build_html(picks, today_str, notes=""):
    """
    Injects picks JSON into index.html template.
    Works with or without INJECT:DATA tag — always finds </body>.
    """
    template_path = Path("template.html")
    if not template_path.exists():
        template_path = Path("index.html")

    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
        print(f"  [HTML] Template loaded: {len(template)} chars")
        print(f"  [HTML] Has INJECT tag: {chr(60)}!-- INJECT:DATA --{chr(62) in template}")
        print(f"  [HTML] Has </body>: {chr(60)}/body{chr(62) in template}")
    else:
        print("  [WARN] No index.html found — using minimal fallback")
        template = "<html><body></body></html>"

    picks_json = json.dumps(picks, indent=2, ensure_ascii=False)
    print(f"  [HTML] NBA games: {len(picks.get(chr(110)+chr(98)+chr(97)+'_games', []))}")
    print(f"  [HTML] MLB games: {len(picks.get(chr(109)+chr(108)+chr(98)+'_games', []))}")
    print(f"  [HTML] Props: {len(picks.get('props', []))}")
    print(f"  [HTML] Parlays: {len(picks.get('parlays', []))}")

    injection = "<script>\nwindow.THE_LINE_DATA = " + picks_json + ";\nwindow.THE_LINE_DATE = \"" + today_str + "\";\nconsole.log(\'THE LINE loaded, NBA games:\', (window.THE_LINE_DATA.nba_games||[]).length);\n</script>"

    if "<!-- INJECT:DATA -->" in template:
        print("  [HTML] Injecting via INJECT:DATA tag")
        output = template.replace("<!-- INJECT:DATA -->", injection)
    elif "</body>" in template:
        print("  [HTML] Injecting before </body>")
        output = template.replace("</body>", injection + "\n</body>")
    else:
        print("  [HTML] Appending to end")
        output = template + "\n" + injection

    print(f"  [HTML] Final output: {len(output)} chars")
    return output

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate THE LINE daily picks")
    parser.add_argument("--notes", default="", help="Breaking notes (injuries, postponements)")
    parser.add_argument("--date",  default="", help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--skip-statcast", action="store_true", help="Skip Baseball Savant")
    parser.add_argument("--skip-nba-api",  action="store_true", help="Skip NBA.com API")
    parser.add_argument("--output", default="index.html", help="Output HTML file")
    args = parser.parse_args()

    today_str = datetime.date.today().strftime("%B %d, %Y")
    if args.date:
        try:
            d = datetime.datetime.strptime(args.date, "%Y-%m-%d")
            today_str = d.strftime("%B %d, %Y")
        except:
            pass

    print(f"\n{'='*60}")
    print(f"  THE LINE — Generating picks for {today_str}")
    if args.notes:
        print(f"  Notes: {args.notes}")
    print(f"{'='*60}\n")

    # ── Fetch all data sources ──
    data_bundle = {}

    print("📅 Fetching schedule...")
    data_bundle["schedule"] = fetch_schedule()
    mlb_home_teams = [g["home"] for g in data_bundle["schedule"].get("mlb", [])]

    print("💰 Fetching DraftKings lines (The Odds API)...")
    odds = fetch_odds()
    data_bundle["odds"] = odds

    print("📊 Fetching StatMuse NBA stats...")
    statmuse = fetch_statmuse()
    data_bundle["statmuse_nba"] = statmuse["nba"]
    data_bundle["statmuse_mlb"] = statmuse["mlb"]

    print("🏥 Fetching ESPN injury reports...")
    data_bundle["injuries"] = fetch_espn_injuries()

    if not args.skip_statcast:
        print("⚾ Fetching Baseball Savant Statcast data...")
        data_bundle["statcast"] = fetch_statcast()
    else:
        data_bundle["statcast"] = {}

    print("📈 Fetching FanGraphs advanced metrics...")
    data_bundle["fangraphs"] = fetch_fangraphs()

    if not args.skip_nba_api:
        print("🏀 Fetching NBA.com official stats...")
        data_bundle["nba_stats"] = fetch_nba_stats()
    else:
        data_bundle["nba_stats"] = {}

    print("📋 Fetching Covers ATS records...")
    data_bundle["covers"] = fetch_covers()

    print("📡 Fetching live lines (ScoresAndOdds)...")
    data_bundle["public_betting"] = fetch_public_betting()

    print("🌤  Fetching weather data...")
    data_bundle["weather"] = fetch_weather(mlb_home_teams)

    # ── Generate picks with Claude ──
    print(f"\n🤖 Sending to Claude API ({sum(1 for v in data_bundle.values() if v)} data sources loaded)...")
    picks = generate_picks(data_bundle, notes=args.notes, target_date=today_str)

    # Save raw picks as JSON for debugging/logging
    picks_path = Path("picks_latest.json")
    picks_path.write_text(json.dumps(picks, indent=2), encoding="utf-8")
    print(f"✅ Picks JSON saved → {picks_path}")

    # ── Inject into HTML ──
    print(f"🔨 Building HTML → {args.output}...")
    html = build_html(picks, today_str, args.notes)
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"✅ Site updated → {args.output}")

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"  ✅ Generated {len(picks.get('nba_games',[]))} NBA picks")
    print(f"  ✅ Generated {len(picks.get('mlb_games',[]))} MLB picks")
    print(f"  ✅ Generated {len(picks.get('props',[]))} prop picks")
    print(f"  ✅ Generated {len(picks.get('parlays',[]))} parlays")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
