"""
utils.py — Football predictions bot
Odds: 1xBet UZ scraper + The-Odds-API fallback
AI:   Mistral AI (H2H + form analysis) + Poisson model cross-check
"""

import asyncio
import json
import logging
import re
from difflib import SequenceMatcher

import aiohttp
from scipy.stats import poisson

from config import (
    ODDS_API_KEY, API_FOOTBALL_KEY, MISTRAL_API_KEY,
    ODDS_API_BASE, API_FOOTBALL_BASE, MISTRAL_API_BASE, MISTRAL_MODEL,
    USD_TO_UZS,
)

logger = logging.getLogger(__name__)

# ── The-Odds-API fallback sports list ─────────────────────
FOOTBALL_SPORTS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_turkey_super_league",
]

# ── 1xBet UZ browser headers ──────────────────────────────
_1XBET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://1xbet.uz/",
    "Origin": "https://1xbet.uz",
}


# ══════════════════════════════════════════════════════════
#  1xBET UZ SCRAPER
# ══════════════════════════════════════════════════════════

async def fetch_1xbet_live_odds() -> list:
    """
    Fetch live football 1x2 odds directly from 1xBet UZ.
    Returns list of {home, away, p1, draw, p2, game_id, url}.
    """
    endpoints = [
        # Primary: live feed with 1x2 markets
        "https://1xbet.uz/LineFeed/Get1x2_MW"
        "?sports=1&count=100&lng=ru&mode=4"
        "&isFiltered=1&country=0&champ=0&onlyLive=1",
        # Fallback: all live events
        "https://1xbet.uz/LineFeed/GetSportMenu"
        "?sports=1&champs=0&all=1&zerocount=0&open=1&lang=ru&mode=4",
    ]
    for url in endpoints:
        try:
            async with aiohttp.ClientSession(headers=_1XBET_HEADERS) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
                    if r.status != 200:
                        continue
                    raw = await r.json(content_type=None)
            result = _parse_1xbet_response(raw)
            if result:
                logger.info(f"1xBet scraper: got {len(result)} live games")
                return result
        except Exception as e:
            logger.warning(f"1xBet scraper endpoint failed: {e}")
    return []


def _parse_1xbet_response(raw: dict | list) -> list:
    """Parse 1xBet live feed JSON into flat odds list."""
    games = []

    # Format A: {"Value": [...events...]}
    events = raw if isinstance(raw, list) else raw.get("Value", [])
    if not events:
        # Format B: nested leagues -> events
        for league_block in (raw.get("SportMenuItems") or []):
            for champ in (league_block.get("Champs") or []):
                for ev in (champ.get("Events") or []):
                    events.append(ev)

    for ev in events:
        try:
            home = ev.get("Team1") or ev.get("HomeTeam") or ev.get("O1") or ""
            away = ev.get("Team2") or ev.get("AwayTeam") or ev.get("O2") or ""
            if not home or not away:
                continue

            game_id = ev.get("Id") or ev.get("GameId") or ""
            p1 = p_draw = p2 = 0.0

            # Try to extract odds from "Events" or "MainOdds" block
            for odds_block in (ev.get("Events") or ev.get("MainOdds") or []):
                gtype = odds_block.get("T") or odds_block.get("GroupType") or 0
                if gtype not in (1, "1x2"):
                    continue
                for coef in (odds_block.get("Coefs") or odds_block.get("Coefficients") or []):
                    pos = coef.get("P") or coef.get("Position") or ""
                    val = float(coef.get("C") or coef.get("Value") or 0)
                    if str(pos) == "1":
                        p1 = val
                    elif str(pos) == "X" or str(pos) == "2":
                        if str(pos) == "X":
                            p_draw = val
                        else:
                            p2 = val

            # Older flat format: W1, WX, W2
            if p1 == 0:
                p1 = float(ev.get("W1") or ev.get("Koef1") or 0)
                p_draw = float(ev.get("WX") or ev.get("KoefX") or 0)
                p2 = float(ev.get("W2") or ev.get("Koef2") or 0)

            if p1 < 1.01:
                continue

            games.append({
                "home": home,
                "away": away,
                "p1": round(p1, 2),
                "draw": round(p_draw, 2),
                "p2": round(p2, 2),
                "game_id": str(game_id),
                "url": f"https://1xbet.uz/en/live/Football/{game_id}" if game_id else "https://1xbet.uz/en/live/Football",
            })
        except Exception:
            continue
    return games


async def fetch_1xbet_game_odds(game_id: str) -> dict:
    """Fetch detailed odds for a specific 1xBet game ID."""
    if not game_id:
        return {}
    url = (
        f"https://1xbet.uz/LineFeed/GetGameZip?id={game_id}"
        "&lng=ru&isSubGames=true&GroupEvents=true"
        "&allEventsGroupSubGames=true&countevents=50"
        "&partner=51&getEmpty=true"
    )
    try:
        async with aiohttp.ClientSession(headers=_1XBET_HEADERS) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return {}
                raw = await r.json(content_type=None)
        return raw.get("Value", {})
    except Exception as e:
        logger.warning(f"fetch_1xbet_game_odds({game_id}): {e}")
        return {}


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_1xbet_odds(match: dict, xbet_games: list) -> dict | None:
    """Find the best-matching game in 1xBet live feed and return odds."""
    home = match["home_team"].lower()
    away = match["away_team"].lower()
    best = None
    best_score = 0.0
    for g in xbet_games:
        s = (_name_similarity(home, g["home"]) + _name_similarity(away, g["away"])) / 2
        if s > best_score:
            best_score = s
            best = g
    if best and best_score >= 0.45:  # at least 45% name similarity
        return best
    return None


# ══════════════════════════════════════════════════════════
#  LIVE MATCHES FETCHING
# ══════════════════════════════════════════════════════════

async def fetch_live_matches() -> list:
    # Fetch matches + 1xBet odds concurrently
    matches_task = _fetch_matches_raw()
    xbet_task = fetch_1xbet_live_odds()
    matches, xbet_games = await asyncio.gather(matches_task, xbet_task, return_exceptions=True)
    if isinstance(matches, Exception):
        matches = []
    if isinstance(xbet_games, Exception):
        xbet_games = []

    # Enrich each match with 1xBet odds if found
    for m in matches:
        if (m.get("p1_odds") or 0) < 1.01 and xbet_games:
            found = match_1xbet_odds(m, xbet_games)
            if found:
                m["p1_odds"] = found["p1"]
                m["x_odds"] = found["draw"]
                m["p2_odds"] = found["p2"]
                m["xbet_game_id"] = found["game_id"]
                m["xbet_url"] = found["url"]

    return matches if matches else _demo_matches()


async def _fetch_matches_raw() -> list:
    if API_FOOTBALL_KEY:
        result = await _from_api_football()
        if result:
            return result
    if ODDS_API_KEY:
        result = await _from_odds_api()
        if result:
            return result
    return _demo_matches()


async def _from_api_football() -> list:
    url = f"{API_FOOTBALL_BASE}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    live_statuses = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url, params={"live": "all"},
                timeout=aiohttp.ClientTimeout(total=12)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"API-Football status {resp.status}")
                    return []
                data = await resp.json()

        results = []
        for fix in (data.get("response") or [])[:25]:
            status_short = fix.get("fixture", {}).get("status", {}).get("short", "")
            if status_short not in live_statuses:
                continue
            teams = fix.get("teams", {})
            goals = fix.get("goals", {})
            league = fix.get("league", {})
            elapsed = fix.get("fixture", {}).get("status", {}).get("elapsed") or 0
            home_g = goals.get("home") or 0
            away_g = goals.get("away") or 0
            results.append({
                "home_team": teams.get("home", {}).get("name", "?"),
                "away_team": teams.get("away", {}).get("name", "?"),
                "home_team_id": teams.get("home", {}).get("id"),
                "away_team_id": teams.get("away", {}).get("id"),
                "score": f"{home_g}:{away_g}",
                "minute": f"{elapsed}'",
                "p1_odds": 0.0, "x_odds": 0.0, "p2_odds": 0.0,
                "league": league.get("name", "Football"),
                "country": league.get("country", ""),
                "fixture_id": fix.get("fixture", {}).get("id"),
                "xbet_url": "https://1xbet.uz/en/live/Football",
                "source": "api_football",
            })

        # Enrich with The-Odds-API as extra source
        if results and ODDS_API_KEY:
            for sport in FOOTBALL_SPORTS[:3]:
                try:
                    odds = await _get_odds_api_sport(sport)
                    for m in results:
                        if (m.get("p1_odds") or 0) > 1:
                            continue
                        found = _find_odds_api_match(m, odds)
                        if found:
                            m["p1_odds"], m["x_odds"], m["p2_odds"] = found
                except Exception:
                    pass
        return results
    except Exception as e:
        logger.error(f"_from_api_football: {e}")
        return []


async def _get_odds_api_sport(sport: str) -> list:
    url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
    params = {
        "apiKey": ODDS_API_KEY, "regions": "eu",
        "markets": "h2h", "oddsFormat": "decimal",
        "bookmakers": "onexbet",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as r:
                return await r.json() if r.status == 200 else []
    except Exception:
        return []


def _find_odds_api_match(match: dict, odds_list: list) -> tuple | None:
    home = match["home_team"].lower()
    away = match["away_team"].lower()
    for event in odds_list:
        eh = event.get("home_team", "").lower()
        ea = event.get("away_team", "").lower()
        if (home in eh or eh in home) and (away in ea or ea in away):
            bms = event.get("bookmakers", [])
            if not bms:
                continue
            mkt = next((m for m in bms[0].get("markets", []) if m["key"] == "h2h"), None)
            if not mkt:
                continue
            oc = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
            return (
                oc.get(event["home_team"], 0),
                oc.get("Draw", 0),
                oc.get(event["away_team"], 0),
            )
    return None


async def _from_odds_api() -> list:
    results = []
    try:
        for sport in FOOTBALL_SPORTS[:4]:
            events = await _get_odds_api_sport(sport)
            for ev in events[:5]:
                bms = ev.get("bookmakers", [])
                if not bms:
                    continue
                mkt = next((m for m in bms[0].get("markets", []) if m["key"] == "h2h"), None)
                if not mkt:
                    continue
                oc = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                results.append({
                    "home_team": ev["home_team"], "away_team": ev["away_team"],
                    "home_team_id": None, "away_team_id": None,
                    "score": "—", "minute": "LIVE",
                    "p1_odds": oc.get(ev["home_team"], 0),
                    "x_odds": oc.get("Draw", 0),
                    "p2_odds": oc.get(ev["away_team"], 0),
                    "league": sport.replace("soccer_", "").replace("_", " ").title(),
                    "country": "", "fixture_id": None,
                    "xbet_url": "https://1xbet.uz/en/live/Football",
                    "source": "odds_api",
                })
        return results[:15]
    except Exception as e:
        logger.error(f"_from_odds_api: {e}")
        return []


def _demo_matches() -> list:
    return [
        {
            "home_team": "Real Madrid", "away_team": "Barcelona",
            "home_team_id": 541, "away_team_id": 529,
            "score": "1:0", "minute": "67'",
            "p1_odds": 2.10, "x_odds": 3.40, "p2_odds": 3.20,
            "league": "La Liga", "country": "Spain",
            "fixture_id": None, "xbet_url": "https://1xbet.uz/en/live/Football",
            "source": "demo",
        },
        {
            "home_team": "Man City", "away_team": "Liverpool",
            "home_team_id": 50, "away_team_id": 40,
            "score": "0:1", "minute": "45'",
            "p1_odds": 1.85, "x_odds": 3.70, "p2_odds": 4.10,
            "league": "Premier League", "country": "England",
            "fixture_id": None, "xbet_url": "https://1xbet.uz/en/live/Football",
            "source": "demo",
        },
        {
            "home_team": "Bayern Munich", "away_team": "Dortmund",
            "home_team_id": 157, "away_team_id": 165,
            "score": "2:2", "minute": "78'",
            "p1_odds": 1.60, "x_odds": 4.00, "p2_odds": 5.50,
            "league": "Bundesliga", "country": "Germany",
            "fixture_id": None, "xbet_url": "https://1xbet.uz/en/live/Football",
            "source": "demo",
        },
    ]


# ══════════════════════════════════════════════════════════
#  HISTORICAL DATA (API-Football H2H + form)
# ══════════════════════════════════════════════════════════

async def fetch_team_form(team_id: int, count: int = 5) -> list:
    if not API_FOOTBALL_KEY or not team_id:
        return []
    try:
        async with aiohttp.ClientSession(headers={"x-apisports-key": API_FOOTBALL_KEY}) as s:
            async with s.get(
                f"{API_FOOTBALL_BASE}/fixtures",
                params={"team": team_id, "last": count, "status": "FT"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        return data.get("response", [])
    except Exception as e:
        logger.warning(f"fetch_team_form({team_id}): {e}")
        return []


async def fetch_h2h(t1: int, t2: int, count: int = 5) -> list:
    if not API_FOOTBALL_KEY or not t1 or not t2:
        return []
    try:
        async with aiohttp.ClientSession(headers={"x-apisports-key": API_FOOTBALL_KEY}) as s:
            async with s.get(
                f"{API_FOOTBALL_BASE}/fixtures/headtohead",
                params={"h2h": f"{t1}-{t2}", "last": count},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        return data.get("response", [])
    except Exception as e:
        logger.warning(f"fetch_h2h: {e}")
        return []


def _summarize_fixtures(fixtures: list, team_id: int = None) -> str:
    if not fixtures:
        return "нет данных"
    lines = []
    for fix in fixtures[:5]:
        home = fix.get("teams", {}).get("home", {}).get("name", "?")
        away = fix.get("teams", {}).get("away", {}).get("name", "?")
        hg = fix.get("goals", {}).get("home") or 0
        ag = fix.get("goals", {}).get("away") or 0
        date = fix.get("fixture", {}).get("date", "")[:10]
        winner_true = fix.get("teams", {}).get("home", {}).get("winner")
        if team_id:
            home_id = fix.get("teams", {}).get("home", {}).get("id")
            is_home = home_id == team_id
            if winner_true is True:
                res = "П1" if is_home else "П2"
            elif winner_true is False:
                res = "П2" if is_home else "П1"
            else:
                res = "X"
            lines.append(f"{date}: {home} {hg}:{ag} {away} → {res}")
        else:
            lines.append(f"{date}: {home} {hg}:{ag} {away}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  MISTRAL AI PREDICTION
# ══════════════════════════════════════════════════════════

async def mistral_predict(match: dict) -> dict:
    if not MISTRAL_API_KEY:
        logger.warning("MISTRAL_API_KEY not set — skipping Mistral")
        return {}

    home = match["home_team"]
    away = match["away_team"]
    score = match.get("score", "0:0")
    minute = match.get("minute", "LIVE")
    league = match.get("league", "Football")
    country = match.get("country", "")
    home_id = match.get("home_team_id")
    away_id = match.get("away_team_id")

    # Fetch historical data concurrently
    h2h_data, home_form_data, away_form_data = await asyncio.gather(
        fetch_h2h(home_id, away_id, 5),
        fetch_team_form(home_id, 5),
        fetch_team_form(away_id, 5),
        return_exceptions=True,
    )

    h2h_text = _summarize_fixtures(h2h_data if isinstance(h2h_data, list) else [])
    home_form = _summarize_fixtures(
        home_form_data if isinstance(home_form_data, list) else [], home_id
    )
    away_form = _summarize_fixtures(
        away_form_data if isinstance(away_form_data, list) else [], away_id
    )

    try:
        hg, ag = map(int, score.split(":"))
    except Exception:
        hg, ag = 0, 0
    try:
        elapsed = int(str(minute).replace("'", "").strip())
    except Exception:
        elapsed = 0
    remaining = max(0, 90 - elapsed)

    p1_o = match.get("p1_odds") or 0
    x_o = match.get("x_odds") or 0
    p2_o = match.get("p2_odds") or 0
    odds_ctx = (
        f"Коэффициенты 1xBet: П1={p1_o:.2f} X={x_o:.2f} П2={p2_o:.2f}"
        if p1_o > 1 else
        "Коэффициенты 1xBet: не найдены — рассчитай справедливые сам"
    )

    prompt = f"""Ты профессиональный футбольный аналитик. Дай прогноз на LIVE матч.

МАТЧ: {home} vs {away}
Лига: {league} ({country})
Счёт: {hg}:{ag} | Минута: {elapsed}' (осталось ~{remaining} мин)
{odds_ctx}

H2H (последние 5 встреч):
{h2h_text}

Форма {home} (последние 5):
{home_form}

Форма {away} (последние 5):
{away_form}

Используй ВСЁ: историю встреч, форму команд, текущий счёт и оставшееся время.
Если коэффициентов нет — придумай справедливые сам на основе анализа.

Ответь ТОЛЬКО валидным JSON (без текста вокруг):
{{
  "prob_p1": <0-100>,
  "prob_x": <0-100>,
  "prob_p2": <0-100>,
  "prob_btts": <0-100>,
  "prob_over25": <0-100>,
  "ai_p1_odds": <число напр. 2.10>,
  "ai_x_odds": <число напр. 3.40>,
  "ai_p2_odds": <число напр. 4.20>,
  "best_bet": "П1 (победа дома)" | "X (ничья)" | "П2 (победа гостей)" | "Обе забьют (ДА)" | "ТБ 2.5 голов" | "ТМ 2.5 голов",
  "best_bet_odds": <коэффициент>,
  "best_bet_prob": <0-100>,
  "confidence": "высокая" | "средняя" | "низкая",
  "is_value": true | false,
  "analysis": "2-3 предложения анализа на русском"
}}"""

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{MISTRAL_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MISTRAL_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 700,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=35),
            ) as r:
                body = await r.text()
                if r.status != 200:
                    logger.error(f"Mistral HTTP {r.status}: {body[:300]}")
                    return {}
                data = json.loads(body)

        content = data["choices"][0]["message"]["content"]
        # Strip accidental markdown code fences
        content = re.sub(r"```(?:json)?", "", content).strip()
        result = json.loads(content)

        return {
            "prob_p1":       float(result.get("prob_p1", 33)),
            "prob_x":        float(result.get("prob_x", 33)),
            "prob_p2":       float(result.get("prob_p2", 33)),
            "prob_btts":     float(result.get("prob_btts", 50)),
            "prob_over25":   float(result.get("prob_over25", 50)),
            "ai_p1_odds":    float(result.get("ai_p1_odds", 0)),
            "ai_x_odds":     float(result.get("ai_x_odds", 0)),
            "ai_p2_odds":    float(result.get("ai_p2_odds", 0)),
            "best_bet":      result.get("best_bet", "—"),
            "best_bet_odds": float(result.get("best_bet_odds", 0)),
            "best_bet_prob": float(result.get("best_bet_prob", 0)),
            "confidence":    result.get("confidence", "средняя"),
            "is_value":      bool(result.get("is_value", False)),
            "analysis":      result.get("analysis", ""),
            "from_mistral":  True,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Mistral JSON parse error: {e} | content: {content[:200]}")
        return {}
    except Exception as e:
        logger.error(f"mistral_predict exception: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#  POISSON MODEL (cross-check when bookmaker odds exist)
# ══════════════════════════════════════════════════════════

def poisson_prediction(match: dict) -> dict:
    p1_o = match.get("p1_odds") or 0
    x_o  = match.get("x_odds") or 0
    p2_o = match.get("p2_odds") or 0
    if p1_o < 1.01 or x_o < 1.01 or p2_o < 1.01:
        return {}

    imp1, impx, imp2 = 1 / p1_o, 1 / x_o, 1 / p2_o
    tot = imp1 + impx + imp2
    pp1, px, pp2 = imp1 / tot, impx / tot, imp2 / tot

    score = match.get("score", "0:0")
    minute = str(match.get("minute", "45'")).replace("'", "").strip()
    try:
        hg, ag = map(int, score.split(":"))
    except Exception:
        hg, ag = 0, 0
    try:
        elapsed = int(minute) if minute.isdigit() else 45
    except Exception:
        elapsed = 45

    rem = max(0, 90 - elapsed) / 90.0
    avg = 2.5
    lh = avg * 0.55 * pp1 / max(pp1 + pp2, 0.01) * rem
    la = avg * 0.45 * pp2 / max(pp1 + pp2, 0.01) * rem

    diff = hg - ag
    if diff < 0:
        lh *= 1.35; la *= 0.80
    elif diff > 0:
        lh *= 0.80; la *= 1.25

    fp1 = fx = fp2 = btts = over25 = 0.0
    for h in range(7):
        for a in range(7):
            p = poisson.pmf(h, lh) * poisson.pmf(a, la)
            th, ta = hg + h, ag + a
            if th > ta:   fp1 += p
            elif th == ta: fx += p
            else:          fp2 += p
            if th >= 1 and ta >= 1: btts   += p
            if th + ta >= 3:        over25 += p

    t = fp1 + fx + fp2
    if t > 0:
        fp1 /= t; fx /= t; fp2 /= t

    btts   = min(btts, 0.99)
    over25 = min(over25, 0.99)
    b_o = round(1 / btts * 0.90, 2)   if btts   > 0.05 else 0
    o_o = round(1 / over25 * 0.90, 2) if over25 > 0.05 else 0

    cands = [
        {"name": "П1 (победа дома)",   "prob": fp1,   "odds": p1_o},
        {"name": "X (ничья)",           "prob": fx,    "odds": x_o},
        {"name": "П2 (победа гостей)", "prob": fp2,   "odds": p2_o},
        {"name": "Обе забьют (ДА)",    "prob": btts,  "odds": b_o},
        {"name": "ТБ 2.5 голов",       "prob": over25,"odds": o_o},
    ]
    for c in cands:
        c["value"] = (c["prob"] - 1 / c["odds"]) * 100 if c["odds"] > 1 else -99

    vb   = [c for c in cands if c["value"] > 0 and c["odds"] >= 1.50]
    best = max(vb, key=lambda c: c["value"]) if vb else max(cands, key=lambda c: c["prob"])

    return {
        "prob_p1":       round(fp1   * 100, 1),
        "prob_x":        round(fx    * 100, 1),
        "prob_p2":       round(fp2   * 100, 1),
        "prob_btts":     round(btts  * 100, 1),
        "prob_over25":   round(over25 * 100, 1),
        "best_bet":      best["name"],
        "best_bet_odds": best["odds"],
        "best_bet_prob": round(best["prob"] * 100, 1),
        "value_pct":     round(best["value"], 1),
        "is_value":      best["value"] > 0,
        "from_poisson":  True,
    }


# ══════════════════════════════════════════════════════════
#  COMBINED PREDICTION  (main entry point)
# ══════════════════════════════════════════════════════════

async def calculate_prediction(match: dict) -> dict:
    """
    1. Always runs Mistral AI (uses H2H + form + league knowledge).
    2. Runs Poisson model in parallel if 1xBet odds are available.
    3. Blends both for the final result.
    """
    has_odds = (match.get("p1_odds") or 0) > 1.01

    # Run Mistral async; Poisson is CPU-sync so run directly
    mistral_task = mistral_predict(match)

    if has_odds:
        mistral, poisson_res = await asyncio.gather(
            mistral_task, _async_wrap(poisson_prediction, match),
            return_exceptions=True,
        )
    else:
        mistral = await mistral_task
        poisson_res = {}

    if isinstance(mistral, Exception):
        logger.error(f"mistral_predict raised: {mistral}")
        mistral = {}
    if isinstance(poisson_res, Exception):
        poisson_res = {}

    # ── Mistral success → primary result ─────────────────
    if mistral:
        ai_p1 = float(mistral.get("ai_p1_odds") or match.get("p1_odds") or 0)
        ai_x  = float(mistral.get("ai_x_odds")  or match.get("x_odds")  or 0)
        ai_p2 = float(mistral.get("ai_p2_odds") or match.get("p2_odds") or 0)

        pp1 = mistral["prob_p1"]
        px  = mistral["prob_x"]
        pp2 = mistral["prob_p2"]
        if poisson_res:
            pp1 = round(pp1 * 0.6 + poisson_res["prob_p1"] * 0.4, 1)
            px  = round(px  * 0.6 + poisson_res["prob_x"]  * 0.4, 1)
            pp2 = round(pp2 * 0.6 + poisson_res["prob_p2"] * 0.4, 1)

        conf = mistral.get("confidence", "средняя")
        conf_emoji = {"высокая": "🟢", "средняя": "🟡", "низкая": "🔴"}.get(conf, "🟡")

        return {
            "prob_p1":    pp1,
            "prob_x":     px,
            "prob_p2":    pp2,
            "btts":       mistral.get("prob_btts", 0),
            "over25":     mistral.get("prob_over25", 0),
            "best_bet":   mistral.get("best_bet", "—"),
            "best_odds":  mistral.get("best_bet_odds", ai_p1),
            "best_prob":  mistral.get("best_bet_prob", 0),
            "value_pct":  poisson_res.get("value_pct", 0) if poisson_res else 0,
            "is_value":   mistral.get("is_value", False),
            "ai_p1_odds": ai_p1,
            "ai_x_odds":  ai_x,
            "ai_p2_odds": ai_p2,
            "confidence": conf,
            "conf_emoji": conf_emoji,
            "analysis":   mistral.get("analysis", ""),
            "source":     "mistral+poisson" if poisson_res else "mistral",
        }

    # ── Poisson only fallback ────────────────────────────
    if poisson_res:
        return {
            "prob_p1":    poisson_res["prob_p1"],
            "prob_x":     poisson_res["prob_x"],
            "prob_p2":    poisson_res["prob_p2"],
            "btts":       poisson_res.get("prob_btts", 0),
            "over25":     poisson_res.get("prob_over25", 0),
            "best_bet":   poisson_res["best_bet"],
            "best_odds":  poisson_res["best_bet_odds"],
            "best_prob":  poisson_res["best_bet_prob"],
            "value_pct":  poisson_res["value_pct"],
            "is_value":   poisson_res["is_value"],
            "ai_p1_odds": match.get("p1_odds", 0),
            "ai_x_odds":  match.get("x_odds", 0),
            "ai_p2_odds": match.get("p2_odds", 0),
            "confidence": "средняя", "conf_emoji": "🟡",
            "analysis":   "",
            "source":     "poisson",
        }

    # ── No data ──────────────────────────────────────────
    return {
        "prob_p1": 0, "prob_x": 0, "prob_p2": 0,
        "btts": 0, "over25": 0, "best_bet": "—",
        "best_odds": 0, "best_prob": 0, "value_pct": 0,
        "is_value": False,
        "ai_p1_odds": 0, "ai_x_odds": 0, "ai_p2_odds": 0,
        "confidence": "низкая", "conf_emoji": "🔴",
        "analysis": "Нет данных для анализа (API недоступны).",
        "source": "none",
    }


async def _async_wrap(fn, *args):
    """Run a sync function as if it were async (no thread — it's fast)."""
    return fn(*args)


# ══════════════════════════════════════════════════════════
#  FORMAT MATCH CARD
# ══════════════════════════════════════════════════════════

def format_match_card(match: dict, pred: dict) -> str:
    home        = match["home_team"]
    away        = match["away_team"]
    score       = match.get("score", "—")
    minute      = match.get("minute", "LIVE")
    league      = match.get("league", "Football")
    source_tag  = match.get("source", "")
    xbet_url    = match.get("xbet_url", "https://1xbet.uz/en/live/Football")

    real_p1 = match.get("p1_odds") or 0
    real_x  = match.get("x_odds")  or 0
    real_p2 = match.get("p2_odds") or 0
    ai_p1   = pred.get("ai_p1_odds") or 0
    ai_x    = pred.get("ai_x_odds")  or 0
    ai_p2   = pred.get("ai_p2_odds") or 0

    if real_p1 > 1:
        odds_line = f"П1: <b>{real_p1:.2f}</b>  X: <b>{real_x:.2f}</b>  П2: <b>{real_p2:.2f}</b>"
        odds_note = "  <i>(1xBet)</i>"
    elif ai_p1 > 1:
        odds_line = f"П1: <b>{ai_p1:.2f}</b>  X: <b>{ai_x:.2f}</b>  П2: <b>{ai_p2:.2f}</b>"
        odds_note = "  <i>(AI-расчёт)</i>"
    else:
        odds_line = "<i>не найдены</i>"
        odds_note = ""

    conf_emoji  = pred.get("conf_emoji", "🟡")
    confidence  = pred.get("confidence", "средняя")
    value_pct   = pred.get("value_pct", 0) or 0
    is_value    = pred.get("is_value", False)
    best_odds   = pred.get("best_odds") or 0

    value_line = ""
    if is_value and value_pct > 0:
        value_line = f"\n🟢 <b>VALUE BET +{value_pct:.1f}%</b>"
    elif value_pct < 0 and best_odds > 1:
        value_line = f"\n🟡 Value: {value_pct:.1f}%"

    stake_block = ""
    if best_odds > 1:
        stake  = 127_000
        win    = int(stake * best_odds)
        profit = win - stake
        stake_block = (
            "\n\n💵 <b>Расчёт ставки (10 USD ≈ 127 000 UZS):</b>\n"
            f"  Ставка: <code>127 000 UZS</code>\n"
            f"  Выигрыш: <code>{win:,} UZS</code>\n"
            f"  Прибыль: <code>+{profit:,} UZS</code>"
        ).replace(",", " ")

    analysis    = pred.get("analysis", "")
    analysis_block = f"\n\n🧠 <b>Анализ Mistral AI:</b>\n<i>{analysis}</i>" if analysis else ""

    pred_source = pred.get("source", "none")
    if source_tag == "demo":
        src_note = "\n⚠️ <i>Демо-данные. Добавьте API-ключи для реальных матчей.</i>"
    elif pred_source == "mistral+poisson":
        src_note = "\n✅ <i>Mistral AI + Poisson-модель</i>"
    elif pred_source == "mistral":
        src_note = "\n✅ <i>Mistral AI (история + форма команд)</i>"
    elif pred_source == "poisson":
        src_note = "\n📐 <i>Poisson-модель (нет ключа Mistral)</i>"
    else:
        src_note = ""

    return (
        f"⚽ <b>{home} vs {away}</b>\n"
        f"🏆 {league}  |  ⏱ <b>{score}</b>  Мин: {minute}\n\n"
        f"📊 <b>Коэффициенты 1xBet:</b>{odds_note}\n{odds_line}\n\n"
        f"🤖 <b>AI-вероятности:</b>\n"
        f"  П1: <b>{pred['prob_p1']}%</b>  X: <b>{pred['prob_x']}%</b>  П2: <b>{pred['prob_p2']}%</b>\n"
        f"  Обе забьют: {pred['btts']}%  |  ТБ 2.5: {pred['over25']}%\n\n"
        f"🎯 <b>Лучшая ставка:</b> {pred['best_bet']} @ <b>{best_odds:.2f}</b>\n"
        f"📈 Вероятность: <b>{pred['best_prob']}%</b>  "
        f"{conf_emoji} Уверенность: <b>{confidence}</b>"
        f"{value_line}"
        f"{analysis_block}"
        f"{stake_block}\n\n"
        f"🔗 <a href='{xbet_url}'>Ставить на 1xBet UZ</a>"
        f"{src_note}\n\n"
        f"<i>⚠️ Прогнозы информационные. Ставки — ваш риск.</i>"
    )


def build_1xbet_link(home: str, away: str) -> str:
    q = f"{home} {away}".replace(" ", "+")
    return f"https://1xbet.uz/en/line/football/?query={q}"
