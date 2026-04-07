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

import os, sys, json, re, requests, datetime, argparse, time
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
    "drtg":  "https://www.statmuse.com/nba/ask/nba-team-defensive-ratings",
}
STATMUSE_MLB_URLS = {
    "ops":   "https://www.statmuse.com/mlb/ask/ops-leaders-this-season",
    "era":   "https://www.statmuse.com/mlb/ask/era-leaders-2026-season",
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
    """
    Fetch injuries using ESPN's scoreboard API which embeds injury data per game.
    Also fetches each team's roster to give Claude current player names.
    """
    out = {}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    today = datetime.date.today().strftime("%Y%m%d")

    # Get NBA injuries from today's scoreboard (embedded in each game)
    nba_sb = safe_json(
        f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={today}",
        headers=headers, label="ESPN NBA scoreboard for injuries"
    )
    nba_injuries = []
    nba_teams_seen = set()
    if nba_sb:
        for event in nba_sb.get("events", []):
            for comp in event.get("competitions", []):
                for team in comp.get("competitors", []):
                    team_name = team.get("team", {}).get("shortDisplayName", "")
                    team_id = team.get("team", {}).get("id", "")
                    if team_id and team_id not in nba_teams_seen:
                        nba_teams_seen.add(team_id)
                        # Fetch team injuries
                        inj_data = safe_json(
                            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}?enable=injuries,roster",
                            headers=headers, label=f"ESPN {team_name} injuries"
                        )
                        if inj_data:
                            team_info = inj_data.get("team", {})
                            for inj in team_info.get("injuries", []):
                                athlete = inj.get("athlete", {})
                                name = athlete.get("displayName", "")
                                status = inj.get("status", "")
                                detail = inj.get("details", {}) or {}
                                inj_type = detail.get("type", "")
                                if name and status:
                                    nba_injuries.append(f"{team_name}: {name} {status} ({inj_type})")
                        time.sleep(0.3)
    out["nba"] = "\n".join(nba_injuries[:80]) if nba_injuries else ""
    print(f"  [ESPN] NBA injuries fetched: {len(nba_injuries)} players")

    time.sleep(0.5)

    # Get MLB injuries from today's scoreboard
    mlb_sb = safe_json(
        f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={today}",
        headers=headers, label="ESPN MLB scoreboard for injuries"
    )
    mlb_injuries = []
    mlb_teams_seen = set()
    if mlb_sb:
        for event in mlb_sb.get("events", []):
            for comp in event.get("competitions", []):
                for team in comp.get("competitors", []):
                    team_name = team.get("team", {}).get("shortDisplayName", "")
                    team_id = team.get("team", {}).get("id", "")
                    if team_id and team_id not in mlb_teams_seen:
                        mlb_teams_seen.add(team_id)
                        inj_data = safe_json(
                            f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{team_id}?enable=injuries,roster",
                            headers=headers, label=f"ESPN {team_name} MLB injuries"
                        )
                        if inj_data:
                            team_info = inj_data.get("team", {})
                            for inj in team_info.get("injuries", []):
                                athlete = inj.get("athlete", {})
                                name = athlete.get("displayName", "")
                                status = inj.get("status", "")
                                detail = inj.get("details", {}) or {}
                                inj_type = detail.get("type", "")
                                if name and status:
                                    mlb_injuries.append(f"{team_name}: {name} {status} ({inj_type})")
                        time.sleep(0.3)
    out["mlb"] = "\n".join(mlb_injuries[:80]) if mlb_injuries else ""
    print(f"  [ESPN] MLB injuries fetched: {len(mlb_injuries)} players")

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
        results["xstats_batters"] = r.text[:1500]

    # Expected statistics — pitchers
    pitcher_url = (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=pitcher&year={year}&position=&team=&min=5&csv=true"
    )
    r = safe_get(pitcher_url, headers=headers, label="Savant xStats pitchers")
    if r and "," in r.text[:100]:
        results["xstats_pitchers"] = r.text[:1500]

    # Barrel rate leaderboard — batters (power indicator)
    barrel_url = (
        f"https://baseballsavant.mlb.com/leaderboard/statcast"
        f"?type=batter&year={year}&position=&team=&min=10&csv=true"
    )
    r = safe_get(barrel_url, headers=headers, label="Savant barrel rates")
    if r and "," in r.text[:100]:
        results["barrels"] = r.text[:1500]

    # Sprint speed (running game, basestealing)
    sprint_url = (
        f"https://baseballsavant.mlb.com/leaderboard/sprint_speed"
        f"?year={year}&position=&team=&min=10&csv=true"
    )
    r = safe_get(sprint_url, headers=headers, label="Savant sprint speed")
    if r and "," in r.text[:100]:
        results["sprint_speed"] = r.text[:1500]

    # Park factors — important for environmental adjustments
    park_url = (
        f"https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
        f"?type=Batter&batSide=&stat=index_wOBA&condition=Is&rolling=no&year={year}&csv=true"
    )
    r = safe_get(park_url, headers=headers, label="Savant park factors")
    if r and "," in r.text[:100]:
        results["park_factors"] = r.text[:1500]

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
            results["splits"] = json.dumps(data)[:1500]
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
            results["batting_leaders"] = json.dumps(data)[:1500]
        except:
            results["batting_leaders"] = truncate(r.text)

    # FanGraphs leaderboard API — pitching
    leader_params["stats"] = "pit"
    leader_params["sortstat"] = "ERA"
    r = safe_get(leader_url, headers=headers, params=leader_params, label="FanGraphs pitching leaders API")
    if r:
        try:
            data = r.json()
            results["pitching_leaders"] = json.dumps(data)[:1500]
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
def generate_picks(data_bundle, notes="", target_date="", query=""):
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is required")

    today_str = target_date or datetime.date.today().strftime("%B %d, %Y")

    # Build the data context — summarize each source
    context_parts = [f"""DATE: {today_str}

ANALYST QUERY: """ + (query if query else "Generate full daily picks report") + """

CRITICAL RULES — READ BEFORE GENERATING PICKS:
1. ONLY use data provided below. NEVER use training knowledge for rosters, trades, or lines.
2. Players may have been traded — only reference players listed in the injury report data provided.
3. Lines must come from the DraftKings odds data provided — never estimate from memory.
4. If a field has no data write N/A — do not substitute training knowledge.
5. You MUST generate picks for every game regardless of data gaps."""]

    if data_bundle.get("schedule", {}).get("nba"):
        context_parts.append(f"\nNBA SCHEDULE TODAY ({len(data_bundle['schedule']['nba'])} games):\n" +
            json.dumps(data_bundle["schedule"]["nba"], indent=2)[:1500])

    if data_bundle.get("schedule", {}).get("mlb"):
        context_parts.append(f"\nMLB SCHEDULE TODAY ({len(data_bundle['schedule']['mlb'])} games):\n" +
            json.dumps(data_bundle["schedule"]["mlb"], indent=2)[:1500])

    if data_bundle.get("odds", {}).get("nba"):
        context_parts.append(f"\nDRAFTKINGS NBA ODDS (live):\n" +
            json.dumps(data_bundle["odds"]["nba"][:8], indent=2)[:1500])

    if data_bundle.get("odds", {}).get("mlb"):
        context_parts.append(f"\nDRAFTKINGS MLB ODDS (live):\n" +
            json.dumps(data_bundle["odds"]["mlb"][:8], indent=2)[:1500])

    if data_bundle.get("odds", {}).get("nba_props"):
        context_parts.append(f"\nDRAFTKINGS NBA PLAYER PROPS (live):\n" +
            json.dumps(data_bundle["odds"]["nba_props"][:4], indent=2)[:1500])

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
            context_parts.append(f"\nSTATMUSE NBA {label}:\n{val[:1500]}")

    for key, label in [("ops","OPS"), ("hr","HR"), ("wrcplus","wRC+"), ("era","ERA"), ("avg","AVG")]:
        val = data_bundle.get("statmuse_mlb", {}).get(key, "")
        if val:
            context_parts.append(f"\nSTATMUSE MLB {label}:\n{val[:1500]}")

    if data_bundle.get("statcast", {}).get("xstats_batters"):
        context_parts.append(f"\nBASEBALL SAVANT — xSTATS BATTERS (xBA, xSLG, xwOBA, barrel%):\n" +
            data_bundle["statcast"]["xstats_batters"][:1500])

    if data_bundle.get("statcast", {}).get("xstats_pitchers"):
        context_parts.append(f"\nBASEBALL SAVANT — xSTATS PITCHERS (xERA, xBA against):\n" +
            data_bundle["statcast"]["xstats_pitchers"][:1500])

    if data_bundle.get("statcast", {}).get("barrels"):
        context_parts.append(f"\nBASEBALL SAVANT — BARREL RATES & EXIT VELOCITY:\n" +
            data_bundle["statcast"]["barrels"][:1500])

    if data_bundle.get("statcast", {}).get("park_factors"):
        context_parts.append(f"\nBASEBALL SAVANT — PARK FACTORS (2026):\n" +
            data_bundle["statcast"]["park_factors"][:1500])

    if data_bundle.get("fangraphs", {}).get("batting_leaders"):
        context_parts.append(f"\nFANGRAPHS BATTING LEADERS (WAR, wRC+, wOBA, OPS):\n" +
            data_bundle["fangraphs"]["batting_leaders"][:1500])

    if data_bundle.get("fangraphs", {}).get("pitching_leaders"):
        context_parts.append(f"\nFANGRAPHS PITCHING LEADERS (ERA, FIP, xFIP, K%):\n" +
            data_bundle["fangraphs"]["pitching_leaders"][:1500])

    if data_bundle.get("fangraphs", {}).get("splits"):
        context_parts.append(f"\nFANGRAPHS SPLITS (LHP vs RHP, Home/Away):\n" +
            data_bundle["fangraphs"]["splits"][:1500])

    injuries = data_bundle.get("injuries", {})
    if injuries.get("nba"):
        context_parts.append(f"\nESPN NBA INJURY REPORT:\n{injuries['nba'][:1500]}")
    if injuries.get("mlb"):
        context_parts.append(f"\nESPN MLB INJURY REPORT:\n{injuries['mlb'][:1500]}")

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
            data_bundle["public_betting"]["nba_lines"][:1500])

    if data_bundle.get("public_betting", {}).get("mlb_lines"):
        context_parts.append(f"\nLIVE MLB LINES (ScoresAndOdds):\n" +
            data_bundle["public_betting"]["mlb_lines"][:1500])

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
      "article": "2 sentence analysis.",
      "key_stats": ["Stat 1", "Stat 2"],
      "injuries": ["Player OUT"],
      "verdict": "BET: [exact bet] — [reason]"
    }}
  ],
  "mlb_games": [
    {{
      "matchup": "AWAY @ HOME",
      "time": "6:05 PM ET",
      "venue": "Stadium Name",
      "home_pitcher": "Name (ERA)",
      "away_pitcher": "Name (ERA)",
      "pick": "TEAM -ML or OVER/UNDER",
      "pick_type": "ML or TOTAL",
      "dk_line": "-XXX",
      "dk_total": "O/U X.X",
      "confidence": 3,
      "environmental": {{"temp_f": 72, "wind_mph": 8, "wind_direction": "out", "altitude_ft": 0, "park_factor": "neutral", "precip_chance": 10}},
      "statcast_edge": "one sentence",
      "fangraphs_edge": "one sentence",
      "article": "2 sentence analysis.",
      "key_stats": ["Stat 1", "Stat 2"],
      "injuries": ["Player OUT"],
      "verdict": "BET: [exact bet] — [reason]"
    }}
  ],
  "props": [
    {{
      "sport": "NBA or MLB",
      "player": "Full Name",
      "team": "TEAM",
      "prop_type": "Points",
      "direction": "OVER",
      "line": "27.5",
      "dk_odds": "-115",
      "season_avg": "26.8",
      "matchup_edge": "one sentence",
      "statcast_edge": "one sentence",
      "reasoning": "2 sentence analysis.",
      "confidence": 4
    }}
  ],
  "parlays": [
    {{
      "name": "The Safe House",
      "risk_level": "LOW",
      "legs": [
        {{
          "pick": "TEAM -ML",
          "dk_odds": "-300",
          "decimal": 1.33,
          "reasoning": "One sentence"
        }}
      ],
      "total_decimal": 1.33,
      "american_odds": "+33",
      "payout_100": "$133",
      "payout_50": "$67",
      "confidence": "HIGH"
    }}
  ]
}}

CRITICAL INSTRUCTIONS:
- You MUST generate picks for every game on today's schedule regardless of data gaps
- If DK lines are missing, estimate based on team records and recent form
- If injury data is missing, note "injury status unknown" but still pick
- If Statcast data is missing, use season stats available from StatMuse
- NEVER return "DATA_INSUFFICIENT" or refuse to generate — always produce picks
- Generate exactly 5 parlays from 2-leg LOW to 7-leg ULTRA risk
- Calculate parlay math: multiply decimals, convert to American odds
- If you lack specific data for a field, use "N/A" or a reasonable estimate
- The output MUST be valid JSON with nba_games, mlb_games, props, and parlays arrays
- Each array must have AT LEAST the games/props listed in the schedule above
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
            "max_tokens": 16000,
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
def get_js():
    """Returns the page JavaScript as a plain string."""
    return r"""<script>
function sw(n){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('on');});
  document.querySelectorAll('.nt').forEach(function(t){t.classList.remove('on');});
  document.getElementById(n).classList.add('on');
  document.querySelector('[data-t='+n+']').classList.add('on');
}
function setQ(q){document.getElementById('si').value=q;document.getElementById('si').focus();}
async function doSearch(){
  var q=document.getElementById('si').value.trim();if(!q)return;
  var el=document.getElementById('sr');
  el.innerHTML='<div style="text-align:center;padding:40px"><div style="font-family:monospace;color:#e8d5a3;margin-bottom:12px">Analyzing with live data...</div><div style="display:inline-block;width:28px;height:28px;border:3px solid rgba(232,213,163,.2);border-top-color:#e8d5a3;border-radius:50%;animation:spin 1s linear infinite"></div></div>';
  var picks=window.THE_LINE_DATA||{};
  var nb=(picks.nba_games||[]).map(function(g){return g.matchup+' '+g.time+' Pick:'+g.pick+' ML:'+g.dk_line+' Spread:'+g.dk_spread;}).join('\n');
  var mb=(picks.mlb_games||[]).map(function(g){return g.matchup+' '+g.time+' Pick:'+g.pick+' ML:'+g.dk_line+' Total:'+g.dk_total;}).join('\n');
  var pb=(picks.props||[]).map(function(p){return p.player+'('+p.team+') '+p.prop_type+' '+p.direction+' '+p.line+' @ '+p.dk_odds;}).join('\n');
  var ctx='Date:'+(picks.date||'')+'\nNBA:\n'+nb+'\nMLB:\n'+mb+'\nPROPS:\n'+pb;
  var prompt='You are THE LINE sports betting analyst.\n\n'+ctx+'\n\nQuery: '+JSON.stringify(q)+'\n\nReturn ONLY valid JSON: {"results":[{"player_or_team":"","sport":"NBA or MLB","pick":"","line":"","odds":"","reasoning":"2-3 sentences","confidence":4}]} with 5-8 picks answering the query.';
  var key=window.AK||localStorage.getItem('tlk')||'';
  if(!key){el.innerHTML=kp();return;}
  try{
    var resp=await fetch('https://shiny-sound-9779.matthewpjett.workers.dev',{
      method:'POST',
      headers:{'content-type':'application/json','anthropic-version':'2023-06-01'},
      body:JSON.stringify({model:'claude-haiku-4-5-20251001',max_tokens:2000,messages:[{role:'user',content:prompt}]})
    });
    if(resp.status===401){el.innerHTML=kp();return;}
    var d=await resp.json();
    var raw=d.content[0].text.trim().replace(/```json|```/g,'').trim();
    var parsed;
    try{parsed=JSON.parse(raw);}
    catch(e){var s=raw.indexOf('{'),en=raw.lastIndexOf('}')+1;parsed=JSON.parse(raw.slice(s,en));}
    var res=parsed.results||parsed.query_results||[];
    if(!res.length){el.innerHTML='<div style="text-align:center;padding:40px;color:#5a5a72;font-family:monospace">No results. Try a different query.</div>';return;}
    var html='<div style="font-size:11px;color:#5a5a72;font-family:monospace;margin-bottom:16px">RESULTS FOR: '+q.toUpperCase()+'</div>';
    res.forEach(function(r){
      var c=parseInt(r.confidence)||3;
      var sc=r.sport==='NBA'?'#4f8ef7':'#e8734a';
      var sbg=r.sport==='NBA'?'rgba(79,142,247,.15)':'rgba(232,115,74,.15)';
      var sbd=r.sport==='NBA'?'rgba(79,142,247,.3)':'rgba(232,115,74,.3)';
      var dots='';for(var i=1;i<=5;i++)dots+='<span style="width:8px;height:8px;border-radius:50%;background:'+(i<=c?'#e8d5a3':'#22222f')+';display:inline-block;margin-left:2px"></span>';
      html+='<div style="background:#111118;border:1px solid rgba(232,213,163,.22);border-radius:12px;padding:18px 20px;margin-bottom:12px">';
      html+='<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px">';
      html+='<div><div style="margin-bottom:6px"><span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:'+sbg+';color:'+sc+';border:1px solid '+sbd+';font-family:monospace">'+(r.sport||'NBA')+'</span></div>';
      html+='<div style="font-family:Georgia,serif;font-size:18px;font-weight:700">'+(r.player_or_team||'')+'</div>';
      html+='<div style="font-size:13px;font-weight:600;color:#4ade80;font-family:monospace;margin-top:4px">'+(r.pick||'')+'</div></div>';
      html+='<div style="text-align:right;flex-shrink:0"><div style="font-family:monospace;font-size:18px;font-weight:600;color:#fbbf24">'+(r.odds||'')+'</div>';
      html+='<div style="font-family:monospace;font-size:12px;color:#5a5a72">'+(r.line||'')+'</div><div style="margin-top:6px">'+dots+'</div></div></div>';
      html+='<div style="font-size:13px;line-height:1.7;color:#9090a8">'+(r.reasoning||'')+'</div></div>';
    });
    el.innerHTML=html;
  }catch(err){
    el.innerHTML='<div style="text-align:center;padding:40px;color:#f87171;font-family:monospace">Error: '+err.message+'</div>';
  }
}
function kp(){
  return '<div style="background:#111118;border:1px solid rgba(232,213,163,.22);border-radius:12px;padding:24px;text-align:center">'
    +'<div style="font-family:Georgia,serif;font-size:20px;font-weight:700;margin-bottom:8px">API Key Required</div>'
    +'<div style="font-size:13px;color:#9090a8;margin-bottom:16px">Enter your Anthropic API key. Stored only in your browser.</div>'
    +'<input id="ki" type="password" placeholder="sk-ant-api03-..." style="width:100%;max-width:500px;background:#0a0a0f;border:1px solid rgba(255,255,255,.2);border-radius:8px;padding:12px 16px;font-size:13px;color:#f0f0f5;font-family:monospace;outline:none;margin-bottom:12px;display:block;margin-left:auto;margin-right:auto"/>'
    +'<button onclick="sk()" style="background:#e8d5a3;color:#0a0a0f;border:none;border-radius:8px;padding:10px 24px;font-size:13px;font-weight:700;cursor:pointer;font-family:monospace">SAVE &amp; SEARCH</button>'
    +'<div style="font-size:11px;color:#5a5a72;font-family:monospace;margin-top:10px">Key stored in your browser only</div></div>';
}
function sk(){var k=document.getElementById('ki').value.trim();if(k){window.AK=k;localStorage.setItem('tlk',k);doSearch();}}
window.AK=localStorage.getItem('tlk')||'';
</script>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>
</body>
</html>"""


def build_html(picks, today_str, notes="", query=""):
    """Builds complete HTML page from picks data. No template needed."""
    nba = picks.get("nba_games", [])
    mlb = picks.get("mlb_games", [])
    props = picks.get("props", [])
    parlays = picks.get("parlays", [])
    query_results = picks.get("query_results", [])
    print(f"  [HTML] Rendering: {len(nba)} NBA, {len(mlb)} MLB, {len(props)} props, {len(parlays)} parlays")

    def dots(n):
        try: n = int(n)
        except: n = 3
        out = ""
        for i in range(1, 6):
            col = "#e8d5a3" if i <= n else "#22222f"
            out += f'<span style="width:8px;height:8px;border-radius:50%;background:{col};display:inline-block;margin-right:3px"></span>'
        return out

    def badge(txt, color, bg, border):
        return (f'<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;'
                f'background:{bg};color:{color};border:1px solid {border};font-family:monospace">{txt}</span>')

    def inj_chips(lst):
        out = ""
        for i in lst:
            out += (f'<span style="font-size:11px;padding:2px 8px;border-radius:4px;'
                    f'background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.3);'
                    f'color:#f87171;font-family:monospace;margin:0 4px 4px 0;display:inline-block">{i}</span>')
        return out

    def obox(label, val, col="#fbbf24"):
        if not val or str(val) in ["NOT_PROVIDED", "N/A", "", "None"]:
            return ""
        return (f'<div style="background:#1a1a24;border:1px solid rgba(232,213,163,.2);'
                f'border-radius:7px;padding:7px 11px;min-width:80px">'
                f'<div style="font-size:10px;color:#5a5a72;font-family:monospace;margin-bottom:2px">{label}</div>'
                f'<div style="font-size:15px;font-weight:600;color:{col};font-family:monospace">{val}</div></div>')

    def nba_card(g, first=False):
        bdr = "rgba(232,213,163,.3)" if first else "rgba(255,255,255,0.08)"
        inj = inj_chips(g.get("injuries", []))
        boxes = obox("ML", g.get("dk_line","")) + obox("SPREAD", g.get("dk_spread",""), "#f0f0f5") + obox("TOTAL", g.get("dk_total",""), "#60a5fa")
        wp = g.get("win_probability", "")
        h = f'<div style="background:#111118;border:1px solid {bdr};border-radius:12px;margin-bottom:12px;overflow:hidden">'
        h += f'<div style="padding:16px 20px 0">'
        h += f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">'
        h += badge("NBA","#4f8ef7","rgba(79,142,247,.15)","rgba(79,142,247,.3)")
        h += f'<span style="font-size:11px;color:#5a5a72;font-family:monospace">{g.get("time","")} · {g.get("venue","")}</span>'
        h += f'<span>{dots(g.get("confidence",3))}</span>'
        if wp: h += f'<span style="font-size:11px;color:#4ade80;font-family:monospace;font-weight:600">{wp}</span>'
        h += f'</div>'
        h += f'<div style="font-family:Georgia,serif;font-size:20px;font-weight:700;margin-bottom:4px">{g.get("matchup","TBD")}</div>'
        h += f'<div style="font-size:13px;font-weight:600;color:#4ade80;font-family:monospace;margin-bottom:6px">{g.get("pick","")}</div>'
        h += f'</div>'
        if boxes: h += f'<div style="display:flex;gap:8px;flex-wrap:wrap;padding:8px 20px">{boxes}</div>'
        h += f'<div style="padding:12px 20px 16px">'
        if inj: h += f'<div style="margin-bottom:10px">{inj}</div>'
        h += f'<div style="font-size:13px;line-height:1.75;color:#9090a8;margin-bottom:12px">{g.get("article","")}</div>'
        h += f'<div style="padding:10px 13px;border-radius:7px;font-size:13px;font-weight:600;background:rgba(74,222,128,.08);border:1px solid rgba(74,222,128,.28);color:#4ade80">{g.get("verdict","")}</div>'
        h += f'</div></div>'
        return h

    def mlb_card(g, first=False):
        bdr = "rgba(232,115,74,.3)" if first else "rgba(255,255,255,0.08)"
        inj = inj_chips(g.get("injuries", []))
        env = g.get("environmental", {}) or {}
        tf = str(env.get("temp_f","")) + "F" if env.get("temp_f") else ""
        boxes = obox("ML", g.get("dk_line","")) + obox("TOTAL", g.get("dk_total",""), "#60a5fa") + obox("TEMP", tf, "#f0f0f5")
        hp = g.get("home_pitcher",""); ap = g.get("away_pitcher","")
        h = f'<div style="background:#111118;border:1px solid {bdr};border-radius:12px;margin-bottom:12px;overflow:hidden">'
        h += f'<div style="padding:16px 20px 0">'
        h += f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">'
        h += badge("MLB","#e8734a","rgba(232,115,74,.15)","rgba(232,115,74,.3)")
        h += f'<span style="font-size:11px;color:#5a5a72;font-family:monospace">{g.get("time","")} · {g.get("venue","")}</span>'
        h += f'<span>{dots(g.get("confidence",3))}</span>'
        h += f'</div>'
        h += f'<div style="font-family:Georgia,serif;font-size:20px;font-weight:700;margin-bottom:4px">{g.get("matchup","TBD")}</div>'
        h += f'<div style="font-size:13px;font-weight:600;color:#4ade80;font-family:monospace;margin-bottom:6px">{g.get("pick","")}</div>'
        h += f'</div>'
        if boxes: h += f'<div style="display:flex;gap:8px;flex-wrap:wrap;padding:8px 20px">{boxes}</div>'
        h += f'<div style="padding:12px 20px 16px">'
        if hp or ap: h += f'<div style="font-size:12px;color:#9090a8;margin-bottom:8px"><strong style="color:#f0f0f5">Pitchers:</strong> {ap} vs {hp}</div>'
        if inj: h += f'<div style="margin-bottom:10px">{inj}</div>'
        h += f'<div style="font-size:13px;line-height:1.75;color:#9090a8;margin-bottom:12px">{g.get("article","")}</div>'
        h += f'<div style="padding:10px 13px;border-radius:7px;font-size:13px;font-weight:600;background:rgba(74,222,128,.08);border:1px solid rgba(74,222,128,.28);color:#4ade80">{g.get("verdict","")}</div>'
        h += f'</div></div>'
        return h

    def prop_card(p):
        d = (p.get("direction") or "OVER").upper()
        c = "#4ade80" if d == "OVER" else "#f87171"
        bg = "rgba(74,222,128,.08)" if d == "OVER" else "rgba(248,113,113,.08)"
        bd = "rgba(74,222,128,.28)" if d == "OVER" else "rgba(248,113,113,.28)"
        sp = p.get("sport", "NBA")
        sc = "#4f8ef7" if sp == "NBA" else "#e8734a"
        sbg = "rgba(79,142,247,.15)" if sp == "NBA" else "rgba(232,115,74,.15)"
        sbd = "rgba(79,142,247,.3)" if sp == "NBA" else "rgba(232,115,74,.3)"
        h = f'<div style="background:#111118;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:14px 16px">'
        h += f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px">'
        h += f'<div><div style="margin-bottom:5px">{badge(sp,sc,sbg,sbd)}</div>'
        h += f'<div style="font-family:Georgia,serif;font-size:15px;font-weight:700">{p.get("player","")}'
        h += f' <span style="font-size:12px;color:#5a5a72;font-weight:400">({p.get("team","")})</span></div></div>'
        h += f'<span style="font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;background:{bg};color:{c};border:1px solid {bd};font-family:monospace;flex-shrink:0">{d}</span></div>'
        h += f'<div style="font-family:monospace;font-size:12px;color:#e8d5a3;margin-bottom:5px">{p.get("prop_type","")} — {d} {p.get("line","")}</div>'
        if p.get("dk_odds"): h += f'<div style="font-family:monospace;font-size:11px;color:#fbbf24;margin-bottom:6px">DK: {p.get("dk_odds","")} · Avg: {p.get("season_avg","")}</div>'
        h += f'<div style="font-size:12px;line-height:1.6;color:#9090a8">{p.get("reasoning","")}</div></div>'
        return h

    def parlay_card(p, idx):
        risk = (p.get("risk_level") or "MED").upper()
        if "LOW" in risk:    rc,rbg,rbd = "#4ade80","rgba(74,222,128,.08)","rgba(74,222,128,.28)"
        elif "ULTRA" in risk: rc,rbg,rbd = "#a78bfa","rgba(167,139,250,.1)","rgba(167,139,250,.3)"
        elif "HIGH" in risk:  rc,rbg,rbd = "#f87171","rgba(248,113,113,.08)","rgba(248,113,113,.28)"
        else:                 rc,rbg,rbd = "#fbbf24","rgba(251,191,36,.08)","rgba(251,191,36,.28)"
        legs_html = ""
        for i, leg in enumerate(p.get("legs", [])):
            legs_html += (f'<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.06)">'
                         f'<div style="font-family:monospace;font-size:10px;color:#5a5a72;background:#1a1a24;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px">{i+1}</div>'
                         f'<div><div style="font-size:13px;font-weight:600;margin-bottom:2px">{leg.get("pick","")}</div>'
                         f'<div style="font-family:monospace;font-size:11px;color:#fbbf24;margin-bottom:2px">DK: {leg.get("dk_odds","")} · Dec: {leg.get("decimal","")}</div>'
                         f'<div style="font-size:12px;color:#5a5a72">{leg.get("reasoning","")}</div></div></div>')
        n = len(p.get("legs", []))
        h = f'<div style="background:#111118;border:1px solid rgba(255,255,255,0.08);border-radius:12px;margin-bottom:12px;overflow:hidden">'
        h += f'<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid rgba(255,255,255,.06)">'
        h += f'<div style="display:flex;align-items:center;gap:10px">'
        h += f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:900;color:#5a5a72;line-height:1">0{idx+1}</div>'
        h += f'<div><div style="font-family:Georgia,serif;font-size:16px;font-weight:700">{p.get("name","Parlay "+str(idx+1))}</div>'
        h += f'<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:{rbg};color:{rc};border:1px solid {rbd};font-family:monospace;display:inline-block;margin-top:3px">{risk} · {n} LEGS</span></div></div>'
        h += f'<div style="text-align:right"><div style="font-family:monospace;font-size:20px;font-weight:500;color:#e8d5a3">{p.get("american_odds","")}</div>'
        h += f'<div style="font-size:10px;color:#5a5a72;font-family:monospace">PAYOUT ODDS</div></div></div>'
        h += f'<div style="padding:4px 18px">{legs_html}</div>'
        h += f'<div style="padding:10px 18px;background:#1a1a24;border-top:1px solid rgba(255,255,255,.06)">'
        h += f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'
        h += f'<div style="background:#111118;border:1px solid rgba(255,255,255,.08);border-radius:6px;padding:8px 11px"><div style="font-size:10px;color:#5a5a72;font-family:monospace;margin-bottom:2px">$50 BET</div><div style="font-size:14px;font-weight:600;color:#e8d5a3;font-family:monospace">{p.get("payout_50","—")}</div></div>'
        h += f'<div style="background:#111118;border:1px solid rgba(255,255,255,.08);border-radius:6px;padding:8px 11px"><div style="font-size:10px;color:#5a5a72;font-family:monospace;margin-bottom:2px">$100 BET</div><div style="font-size:14px;font-weight:600;color:#e8d5a3;font-family:monospace">{p.get("payout_100","—")}</div></div>'
        h += f'</div></div></div>'
        return h

    def qcard(r):
        c = int(r.get("confidence", 3))
        sp = r.get("sport", "NBA")
        sc = "#4f8ef7" if sp == "NBA" else "#e8734a"
        sbg = "rgba(79,142,247,.15)" if sp == "NBA" else "rgba(232,115,74,.15)"
        sbd = "rgba(79,142,247,.3)" if sp == "NBA" else "rgba(232,115,74,.3)"
        h = f'<div style="background:#111118;border:1px solid rgba(232,213,163,.22);border-radius:12px;padding:18px 20px;margin-bottom:12px">'
        h += f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px">'
        h += f'<div><div style="margin-bottom:6px">{badge(sp,sc,sbg,sbd)}</div>'
        h += f'<div style="font-family:Georgia,serif;font-size:18px;font-weight:700">{r.get("player_or_team","")}</div>'
        h += f'<div style="font-size:13px;font-weight:600;color:#4ade80;font-family:monospace;margin-top:4px">{r.get("pick","")}</div></div>'
        h += f'<div style="text-align:right;flex-shrink:0"><div style="font-family:monospace;font-size:18px;font-weight:600;color:#fbbf24">{r.get("odds","")}</div>'
        h += f'<div style="font-family:monospace;font-size:12px;color:#5a5a72">{r.get("line","")}</div>'
        h += f'<div style="margin-top:6px">{dots(c)}</div></div></div>'
        h += f'<div style="font-size:13px;line-height:1.7;color:#9090a8">{r.get("reasoning","")}</div></div>'
        return h

    empty = '<div style="text-align:center;padding:60px 20px;color:#5a5a72;font-family:monospace;font-size:13px">No data. Run the workflow to generate picks.</div>'
    nba_html   = "".join(nba_card(g, i==0) for i,g in enumerate(nba)) if nba else empty
    mlb_html   = "".join(mlb_card(g, i==0) for i,g in enumerate(mlb)) if mlb else empty
    props_html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:11px">' + ("".join(prop_card(p) for p in props) if props else empty) + "</div>"
    parl_html  = "".join(parlay_card(p,i) for i,p in enumerate(parlays)) if parlays else empty

    query_display = query or ""
    if query_results:
        qr_html = (f'<div style="font-size:11px;color:#5a5a72;font-family:monospace;margin-bottom:16px">RESULTS FOR: {query_display.upper()}</div>' +
                   "".join(qcard(r) for r in query_results))
    elif query:
        qr_html = '<div style="text-align:center;padding:40px;color:#5a5a72;font-family:monospace">No results. Try running again.</div>'
    else:
        qr_html = '<div style="text-align:center;padding:40px;color:#5a5a72;font-family:monospace">Type a question above and click SEARCH</div>'

    suggestions = ["Best HR picks today","Best unders tonight","Top strikeout props","Best value spreads","Same game parlay","Best player props"]
    chips = "".join(
        f'<button onclick="setQ(\'{s}\')" style="font-size:11px;padding:4px 10px;border-radius:16px;background:#1a1a24;border:1px solid rgba(255,255,255,.1);color:#e8d5a3;font-family:monospace;cursor:pointer">{s}</button>'
        for s in suggestions
    )

    css = """<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:"DM Sans",sans-serif;background:#0a0a0f;color:#f0f0f5;min-height:100vh}
header{position:sticky;top:0;z-index:100;background:rgba(10,10,15,.93);backdrop-filter:blur(20px);border-bottom:1px solid rgba(255,255,255,.08);padding:0 28px}
.hi{max-width:1200px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;height:58px}
.logo{font-family:"Playfair Display",serif;font-weight:900;font-size:21px}.logo span{color:#e8d5a3}
nav{background:#111118;border-bottom:1px solid rgba(255,255,255,.08);padding:0 28px;position:sticky;top:58px;z-index:99;overflow-x:auto}
.ni{max-width:1200px;margin:0 auto;display:flex}
.nt{padding:13px 22px;font-size:13px;font-weight:500;color:#5a5a72;cursor:pointer;border:none;background:none;white-space:nowrap;position:relative;transition:color .2s}
.nt::after{content:"";position:absolute;bottom:0;left:0;right:0;height:2px;background:transparent;transition:background .2s}
.nt.on{color:#f0f0f5}.nt.on::after{background:#e8d5a3}
.nt[data-t=nba].on{color:#4f8ef7}.nt[data-t=nba].on::after{background:#4f8ef7}
.nt[data-t=mlb].on{color:#e8734a}.nt[data-t=mlb].on::after{background:#e8734a}
.nt[data-t=props].on{color:#4ade80}.nt[data-t=props].on::after{background:#4ade80}
.nt[data-t=parlays].on{color:#a78bfa}.nt[data-t=parlays].on::after{background:#a78bfa}
.nt[data-t=search].on{color:#e8d5a3}.nt[data-t=search].on::after{background:#e8d5a3}
.wrap{max-width:1200px;margin:0 auto;padding:28px 28px 80px}
.tab{display:none}.tab.on{display:block}
.ht{font-family:"Playfair Display",serif;font-size:32px;font-weight:900;letter-spacing:-1px;margin-bottom:5px}
.ht em{font-style:italic;color:#e8d5a3}
.hs{font-size:11px;color:#5a5a72;font-family:monospace;margin-bottom:20px}
.disc{margin-top:32px;padding:13px 16px;border:1px solid rgba(255,255,255,.08);border-radius:8px;font-size:11px;color:#5a5a72;line-height:1.6;font-family:monospace}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:600px){header,nav{padding:0 14px}.wrap{padding:18px 14px 60px}.ht{font-size:24px}}
</style>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>THE LINE — {today_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,700&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
{css}
</head>
<body>
<header><div class="hi">
  <div class="logo">THE <span>LINE</span></div>
  <div style="display:flex;align-items:center;gap:14px">
    <div style="display:flex;align-items:center;gap:6px;font-family:monospace;font-size:11px;color:#4ade80"><span style="width:7px;height:7px;border-radius:50%;background:#4ade80;display:inline-block;animation:pulse 2s infinite"></span>LIVE DATA</div>
    <div style="font-family:monospace;font-size:11px;color:#5a5a72">{today_str.upper()}</div>
  </div>
</div></header>
<nav><div class="ni">
  <button class="nt on" data-t="nba" onclick="sw('nba')">NBA</button>
  <button class="nt" data-t="mlb" onclick="sw('mlb')">MLB</button>
  <button class="nt" data-t="props" onclick="sw('props')">PROP PICKS</button>
  <button class="nt" data-t="parlays" onclick="sw('parlays')">PARLAYS</button>
  <button class="nt" data-t="search" onclick="sw('search')">&#128269; SEARCH</button>
</div></nav>
<div class="wrap">
  <div id="nba" class="tab on"><div class="ht">NBA <em>Picks</em></div><div class="hs">{today_str.upper()} · {len(nba)} GAMES · LIVE DRAFTKINGS LINES</div>{nba_html}</div>
  <div id="mlb" class="tab"><div class="ht">MLB <em>Picks</em></div><div class="hs">{today_str.upper()} · {len(mlb)} GAMES · STATCAST + WEATHER</div>{mlb_html}</div>
  <div id="props" class="tab"><div class="ht">Prop <em>Picks</em></div><div class="hs">{today_str.upper()} · {len(props)} PROPS · LIVE DK LINES</div>{props_html}</div>
  <div id="parlays" class="tab"><div class="ht">Daily <em>Parlays</em></div><div class="hs">{today_str.upper()} · {len(parlays)} PARLAYS · LOW TO ULTRA RISK</div>{parl_html}<div class="disc">GAMBLING PROBLEM? CALL 1-800-GAMBLER. For entertainment only. Verify lines at DraftKings. 21+ only.</div></div>
  <div id="search" class="tab">
    <div class="ht">Search <em>Picks</em></div><div class="hs">ASK ANYTHING — AI-POWERED LIVE ANALYSIS</div>
    <div style="background:#111118;border:1px solid rgba(232,213,163,.22);border-radius:12px;padding:20px;margin-bottom:20px">
      <div style="display:flex;gap:10px;margin-bottom:14px">
        <input id="si" type="text" placeholder="e.g. Best home run picks today..." onkeydown="if(event.key==='Enter')doSearch()" style="flex:1;background:#0a0a0f;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:12px 16px;font-size:14px;color:#f0f0f5;font-family:monospace;outline:none"/>
        <button onclick="doSearch()" style="background:#e8d5a3;color:#0a0a0f;border:none;border-radius:8px;padding:12px 24px;font-size:13px;font-weight:700;cursor:pointer;font-family:monospace;white-space:nowrap">SEARCH</button>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px"><span style="font-size:11px;color:#5a5a72;font-family:monospace;align-self:center;margin-right:4px">Try:</span>{chips}</div>
    </div>
    <div id="sr">{qr_html}</div>
  </div>
</div>
"""
    return html + get_js()


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
    parser.add_argument("--query", default="", help="Specific pick query e.g. best HR picks today")
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

    # NBA.com always times out on GitHub Actions IPs - skip by default
    data_bundle["nba_stats"] = {}
    print("  [SKIP] NBA.com API skipped (times out on CI) — using StatMuse instead")

    # Covers ATS pages return 404 - skip
    data_bundle["covers"] = {}
    print("  [SKIP] Covers skipped (404s on team-trends pages)")

    print("📡 Fetching live lines (ScoresAndOdds)...")
    data_bundle["public_betting"] = fetch_public_betting()

    print("🌤  Fetching weather data...")
    data_bundle["weather"] = fetch_weather(mlb_home_teams)

    # ── Generate picks with Claude ──
    print(f"\n🤖 Sending to Claude API ({sum(1 for v in data_bundle.values() if v)} data sources loaded)...")
    picks = generate_picks(data_bundle, notes=args.notes, target_date=today_str, query=args.query)

    # Save raw picks as JSON for debugging/logging
    picks_path = Path("picks_latest.json")
    picks_path.write_text(json.dumps(picks, indent=2), encoding="utf-8")
    print(f"✅ Picks JSON saved → {picks_path}")

    # ── Inject into HTML ──
    print(f"🔨 Building HTML → {args.output}...")
    html = build_html(picks, today_str, args.notes, query=args.query)
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
