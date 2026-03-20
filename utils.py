import aiohttp
import logging
import json
import re
from scipy.stats import poisson
from config import (
    ODDS_API_KEY, API_FOOTBALL_KEY, MISTRAL_API_KEY,
    ODDS_API_BASE, API_FOOTBALL_BASE, MISTRAL_API_BASE, MISTRAL_MODEL,
    USD_TO_UZS
)

logger = logging.getLogger(__name__)

FOOTBALL_SPORTS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_uefa_champs_league",
    "soccer_uefa_europa_league", "soccer_turkey_super_league",
]


# ══════════════════════════════════════════════════════════
#  LIVE MATCHES FETCHING
# ══════════════════════════════════════════════════════════

async def fetch_live_matches() -> list:
    matches = []
    if API_FOOTBALL_KEY:
        matches = await _from_api_football()
    if not matches and ODDS_API_KEY:
        matches = await _from_odds_api()
    if not matches:
        matches = _demo_matches()
    return matches


async def _from_api_football() -> list:
    url = f"{API_FOOTBALL_BASE}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    live_statuses = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params={"live": "all"}, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        results = []
        for fix in (data.get("response") or [])[:20]:
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
                "source": "api_football",
            })

        # Try to enrich with 1xBet odds
        if results and ODDS_API_KEY:
            for sport in FOOTBALL_SPORTS[:4]:
                try:
                    odds = await _get_odds_for_sport(sport)
                    for m in results:
                        if m["p1_odds"] > 0:
                            continue
                        found = _find_odds(m, odds)
                        if found:
                            m["p1_odds"] = found[0]
                            m["x_odds"] = found[1]
                            m["p2_odds"] = found[2]
                except Exception:
                    pass
        return results
    except Exception as e:
        logger.error(f"_from_api_football: {e}")
        return []


async def _get_odds_for_sport(sport: str) -> list:
    url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
    params = {
        "apiKey": ODDS_API_KEY, "regions": "eu",
        "markets": "h2h", "oddsFormat": "decimal", "bookmakers": "onexbet"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return []
                return await resp.json()
    except Exception:
        return []


def _find_odds(match: dict, odds_list: list) -> tuple | None:
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
            return oc.get(event["home_team"], 0), oc.get("Draw", 0), oc.get(event["away_team"], 0)
    return None


async def _from_odds_api() -> list:
    results = []
    try:
        for sport in FOOTBALL_SPORTS[:4]:
            events = await _get_odds_for_sport(sport)
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
                    "source": "odds_api",
                })
        return results[:15]
    except Exception as e:
        logger.error(f"_from_odds_api: {e}")
        return []


def _demo_matches() -> list:
    return [
        {"home_team": "Real Madrid", "away_team": "Barcelona", "score": "1:0", "minute": "67'",
         "home_team_id": 541, "away_team_id": 529,
         "p1_odds": 2.10, "x_odds": 3.40, "p2_odds": 3.20,
         "league": "La Liga", "country": "Spain", "fixture_id": None, "source": "demo"},
        {"home_team": "Man City", "away_team": "Liverpool", "score": "0:1", "minute": "45'",
         "home_team_id": 50, "away_team_id": 40,
         "p1_odds": 1.85, "x_odds": 3.70, "p2_odds": 4.10,
         "league": "Premier League", "country": "England", "fixture_id": None, "source": "demo"},
        {"home_team": "Bayern", "away_team": "Dortmund", "score": "2:2", "minute": "78'",
         "home_team_id": 157, "away_team_id": 165,
         "p1_odds": 1.60, "x_odds": 4.00, "p2_odds": 5.50,
         "league": "Bundesliga", "country": "Germany", "fixture_id": None, "source": "demo"},
    ]


# ══════════════════════════════════════════════════════════
#  HISTORICAL DATA (API-Football)
# ══════════════════════════════════════════════════════════

async def fetch_team_form(team_id: int, count: int = 5) -> list:
    """Last N finished matches for a team."""
    if not API_FOOTBALL_KEY or not team_id:
        return []
    url = f"{API_FOOTBALL_BASE}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url, params={"team": team_id, "last": count, "status": "FT"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        return data.get("response", [])
    except Exception as e:
        logger.warning(f"fetch_team_form({team_id}): {e}")
        return []


async def fetch_h2h(team1_id: int, team2_id: int, count: int = 5) -> list:
    """Head-to-head last N matches."""
    if not API_FOOTBALL_KEY or not team1_id or not team2_id:
        return []
    url = f"{API_FOOTBALL_BASE}/fixtures/headtohead"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url, params={"h2h": f"{team1_id}-{team2_id}", "last": count},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        return data.get("response", [])
    except Exception as e:
        logger.warning(f"fetch_h2h: {e}")
        return []


def _summarize_fixtures(fixtures: list, team_id: int = None) -> str:
    """Convert fixture list to compact text for AI prompt."""
    if not fixtures:
        return "нет данных"
    lines = []
    for fix in fixtures[:5]:
        home = fix.get("teams", {}).get("home", {}).get("name", "?")
        away = fix.get("teams", {}).get("away", {}).get("name", "?")
        hg = fix.get("goals", {}).get("home") or 0
        ag = fix.get("goals", {}).get("away") or 0
        date = fix.get("fixture", {}).get("date", "")[:10]
        winner_id = fix.get("teams", {}).get("home", {}).get("winner")
        if team_id:
            home_id = fix.get("teams", {}).get("home", {}).get("id")
            is_home = home_id == team_id
            if winner_id is True:
                result = "П1" if is_home else "П2"
            elif winner_id is False:
                result = "П2" if is_home else "П1"
            else:
                result = "X"
            lines.append(f"{date}: {home} {hg}:{ag} {away} → {result}")
        else:
            lines.append(f"{date}: {home} {hg}:{ag} {away}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  MISTRAL AI PREDICTION
# ══════════════════════════════════════════════════════════

async def mistral_predict(match: dict) -> dict:
    """
    Full AI prediction using Mistral.
    Fetches H2H + team form data, then asks Mistral for analysis.
    Returns enriched prediction dict.
    """
    if not MISTRAL_API_KEY:
        return {}

    home = match["home_team"]
    away = match["away_team"]
    score = match.get("score", "0:0")
    minute = match.get("minute", "LIVE")
    league = match.get("league", "Football")
    country = match.get("country", "")
    home_id = match.get("home_team_id")
    away_id = match.get("away_team_id")

    # Fetch historical data (parallel)
    import asyncio
    h2h_data, home_form_data, away_form_data = await asyncio.gather(
        fetch_h2h(home_id, away_id, 5),
        fetch_team_form(home_id, 5),
        fetch_team_form(away_id, 5),
        return_exceptions=True,
    )

    h2h_text = _summarize_fixtures(h2h_data if isinstance(h2h_data, list) else [])
    home_form_text = _summarize_fixtures(
        home_form_data if isinstance(home_form_data, list) else [], home_id
    )
    away_form_text = _summarize_fixtures(
        away_form_data if isinstance(away_form_data, list) else [], away_id
    )

    # Parse current score
    try:
        hg, ag = map(int, score.split(":"))
    except Exception:
        hg, ag = 0, 0
    try:
        elapsed = int(minute.replace("'", "").strip())
    except Exception:
        elapsed = 0

    remaining = max(0, 90 - elapsed)

    # Has 1xBet odds?
    p1_o = match.get("p1_odds", 0) or 0
    x_o = match.get("x_odds", 0) or 0
    p2_o = match.get("p2_odds", 0) or 0
    odds_text = (
        f"Коэффициенты 1xBet: П1={p1_o:.2f}, X={x_o:.2f}, П2={p2_o:.2f}"
        if p1_o > 1 else "Коэффициенты 1xBet: недоступны (оцени сам)"
    )

    prompt = f"""Ты профессиональный спортивный аналитик. Проанализируй LIVE-матч по футболу и дай точный прогноз.

МАТЧ: {home} vs {away}
Лига: {league} ({country})
Текущий счёт: {hg}:{ag}
Минута: {elapsed}' (осталось ~{remaining} мин)
{odds_text}

ИСТОРИЯ ОЧНЫХ ВСТРЕЧ (H2H, последние 5):
{h2h_text}

ФОРМА {home} (последние 5 матчей):
{home_form_text}

ФОРМА {away} (последние 5 матчей):
{away_form_text}

Задача: на основе ВСЕХ данных (история встреч, текущая форма, счёт, время матча):

1. Оцени вероятности исходов с учётом текущего состояния матча (учти счёт и оставшееся время)
2. Если нет коэффициентов от букмекера — рассчитай справедливые коэффициенты сам
3. Выбери ЛУЧШУЮ ставку прямо сейчас
4. Дай краткое объяснение (2-3 предложения)

ВАЖНО: отвечай ТОЛЬКО JSON, без лишнего текста:
{{
  "prob_p1": число от 0 до 100,
  "prob_x": число от 0 до 100,
  "prob_p2": число от 0 до 100,
  "prob_btts": число от 0 до 100,
  "prob_over25": число от 0 до 100,
  "ai_p1_odds": коэффициент П1 (например 2.10),
  "ai_x_odds": коэффициент X (например 3.40),
  "ai_p2_odds": коэффициент П2 (например 4.20),
  "best_bet": "П1 (победа дома)" | "X (ничья)" | "П2 (победа гостей)" | "Обе забьют (ДА)" | "ТБ 2.5 голов" | "ТМ 2.5 голов",
  "best_bet_odds": коэффициент рекомендуемой ставки,
  "best_bet_prob": вероятность рекомендуемой ставки,
  "confidence": "высокая" | "средняя" | "низкая",
  "is_value": true | false,
  "analysis": "краткий анализ на русском языке (2-3 предложения)"
}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MISTRAL_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MISTRAL_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Mistral API error {resp.status}: {body[:200]}")
                    return {}
                data = await resp.json()

        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)

        # Normalise
        return {
            "prob_p1": float(result.get("prob_p1", 0)),
            "prob_x": float(result.get("prob_x", 0)),
            "prob_p2": float(result.get("prob_p2", 0)),
            "prob_btts": float(result.get("prob_btts", 0)),
            "prob_over25": float(result.get("prob_over25", 0)),
            "ai_p1_odds": float(result.get("ai_p1_odds", 0)),
            "ai_x_odds": float(result.get("ai_x_odds", 0)),
            "ai_p2_odds": float(result.get("ai_p2_odds", 0)),
            "best_bet": result.get("best_bet", "—"),
            "best_bet_odds": float(result.get("best_bet_odds", 0)),
            "best_bet_prob": float(result.get("best_bet_prob", 0)),
            "confidence": result.get("confidence", "средняя"),
            "is_value": bool(result.get("is_value", False)),
            "analysis": result.get("analysis", ""),
            "from_mistral": True,
        }
    except json.JSONDecodeError as e:
        logger.error(f"Mistral JSON parse error: {e}")
        return {}
    except Exception as e:
        logger.error(f"mistral_predict error: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#  POISSON PREDICTION (when we have bookmaker odds)
# ══════════════════════════════════════════════════════════

def poisson_prediction(match: dict) -> dict:
    p1_o = match.get("p1_odds", 0) or 0
    x_o = match.get("x_odds", 0) or 0
    p2_o = match.get("p2_odds", 0) or 0

    if p1_o < 1.01 or x_o < 1.01 or p2_o < 1.01:
        return {}

    imp1, impx, imp2 = 1 / p1_o, 1 / x_o, 1 / p2_o
    total = imp1 + impx + imp2
    pp1, px, pp2 = imp1 / total, impx / total, imp2 / total

    score = match.get("score", "0:0")
    minute = match.get("minute", "0'").replace("'", "").strip()
    try:
        hg, ag = map(int, score.split(":"))
    except Exception:
        hg, ag = 0, 0
    try:
        elapsed = int(minute) if minute.isdigit() else 45
    except Exception:
        elapsed = 45

    remaining = max(0, 90 - elapsed) / 90.0
    avg = 2.5
    lh = avg * 0.55 * pp1 / max(pp1 + pp2, 0.01) * remaining
    la = avg * 0.45 * pp2 / max(pp1 + pp2, 0.01) * remaining

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
            if th > ta: fp1 += p
            elif th == ta: fx += p
            else: fp2 += p
            if th >= 1 and ta >= 1: btts += p
            if th + ta >= 3: over25 += p

    total_p = fp1 + fx + fp2
    if total_p > 0:
        fp1 /= total_p; fx /= total_p; fp2 /= total_p

    btts = min(btts, 0.99)
    over25 = min(over25, 0.99)
    btts_o = round(1 / btts * 0.90, 2) if btts > 0.05 else 0
    over_o = round(1 / over25 * 0.90, 2) if over25 > 0.05 else 0

    candidates = [
        {"name": "П1 (победа дома)", "prob": fp1, "odds": p1_o},
        {"name": "X (ничья)", "prob": fx, "odds": x_o},
        {"name": "П2 (победа гостей)", "prob": fp2, "odds": p2_o},
        {"name": "Обе забьют (ДА)", "prob": btts, "odds": btts_o},
        {"name": "ТБ 2.5 голов", "prob": over25, "odds": over_o},
    ]
    for c in candidates:
        c["value"] = (c["prob"] - 1 / c["odds"]) * 100 if c["odds"] > 1 else -99

    vb = [c for c in candidates if c["value"] > 0 and c["odds"] >= 1.50]
    best = max(vb, key=lambda c: c["value"]) if vb else max(candidates, key=lambda c: c["prob"])

    return {
        "prob_p1": round(fp1 * 100, 1),
        "prob_x": round(fx * 100, 1),
        "prob_p2": round(fp2 * 100, 1),
        "prob_btts": round(btts * 100, 1),
        "prob_over25": round(over25 * 100, 1),
        "best_bet": best["name"],
        "best_bet_odds": best["odds"],
        "best_bet_prob": round(best["prob"] * 100, 1),
        "value_pct": round(best["value"], 1),
        "is_value": best["value"] > 0,
        "from_poisson": True,
    }


# ══════════════════════════════════════════════════════════
#  COMBINED PREDICTION  (main entry point)
# ══════════════════════════════════════════════════════════

async def calculate_prediction(match: dict) -> dict:
    """
    Always runs Mistral for deep analysis.
    Poisson is used as a cross-check when odds exist.
    Returns merged result.
    """
    has_odds = (match.get("p1_odds") or 0) > 1.01

    # Run in parallel
    import asyncio
    tasks = [mistral_predict(match)]
    if has_odds:
        tasks.append(asyncio.coroutine(lambda: poisson_prediction(match))())
    results = await asyncio.gather(*tasks, return_exceptions=True)

    mistral = results[0] if isinstance(results[0], dict) else {}
    poisson_res = results[1] if len(results) > 1 and isinstance(results[1], dict) else {}

    # If Mistral succeeded — use it as primary
    if mistral:
        # Merge 1xBet odds from match (if exist) with AI estimated odds
        ai_p1_odds = mistral.get("ai_p1_odds") or match.get("p1_odds") or 0
        ai_x_odds = mistral.get("ai_x_odds") or match.get("x_odds") or 0
        ai_p2_odds = mistral.get("ai_p2_odds") or match.get("p2_odds") or 0

        # Cross-check: blend Poisson probs if available
        prob_p1 = mistral["prob_p1"]
        prob_x = mistral["prob_x"]
        prob_p2 = mistral["prob_p2"]
        if poisson_res:
            prob_p1 = round((prob_p1 * 0.6 + poisson_res["prob_p1"] * 0.4), 1)
            prob_x = round((prob_x * 0.6 + poisson_res["prob_x"] * 0.4), 1)
            prob_p2 = round((prob_p2 * 0.6 + poisson_res["prob_p2"] * 0.4), 1)

        confidence = mistral.get("confidence", "средняя")
        conf_emoji = {"высокая": "🟢", "средняя": "🟡", "низкая": "🔴"}.get(confidence, "🟡")

        return {
            "prob_p1": prob_p1,
            "prob_x": prob_x,
            "prob_p2": prob_p2,
            "btts": mistral.get("prob_btts", 0),
            "over25": mistral.get("prob_over25", 0),
            "best_bet": mistral.get("best_bet", "—"),
            "best_odds": mistral.get("best_bet_odds", ai_p1_odds),
            "best_prob": mistral.get("best_bet_prob", 0),
            "value_pct": poisson_res.get("value_pct", 0) if poisson_res else 0,
            "is_value": mistral.get("is_value", False),
            "ai_p1_odds": ai_p1_odds,
            "ai_x_odds": ai_x_odds,
            "ai_p2_odds": ai_p2_odds,
            "confidence": confidence,
            "conf_emoji": conf_emoji,
            "analysis": mistral.get("analysis", ""),
            "source": "mistral+poisson" if poisson_res else "mistral",
        }

    # Fallback to Poisson only
    if poisson_res:
        return {
            "prob_p1": poisson_res["prob_p1"],
            "prob_x": poisson_res["prob_x"],
            "prob_p2": poisson_res["prob_p2"],
            "btts": poisson_res.get("prob_btts", 0),
            "over25": poisson_res.get("prob_over25", 0),
            "best_bet": poisson_res["best_bet"],
            "best_odds": poisson_res["best_bet_odds"],
            "best_prob": poisson_res["best_bet_prob"],
            "value_pct": poisson_res["value_pct"],
            "is_value": poisson_res["is_value"],
            "ai_p1_odds": match.get("p1_odds", 0),
            "ai_x_odds": match.get("x_odds", 0),
            "ai_p2_odds": match.get("p2_odds", 0),
            "confidence": "средняя",
            "conf_emoji": "🟡",
            "analysis": "",
            "source": "poisson",
        }

    # No data at all
    return {
        "prob_p1": 0, "prob_x": 0, "prob_p2": 0,
        "btts": 0, "over25": 0, "best_bet": "—",
        "best_odds": 0, "best_prob": 0, "value_pct": 0,
        "is_value": False, "ai_p1_odds": 0, "ai_x_odds": 0, "ai_p2_odds": 0,
        "confidence": "низкая", "conf_emoji": "🔴",
        "analysis": "Данные временно недоступны.",
        "source": "none",
    }


# ══════════════════════════════════════════════════════════
#  FORMAT MATCH CARD
# ══════════════════════════════════════════════════════════

def format_match_card(match: dict, pred: dict) -> str:
    home, away = match["home_team"], match["away_team"]
    score = match.get("score", "—")
    minute = match.get("minute", "LIVE")
    league = match.get("league", "Football")
    source_tag = match.get("source", "")

    # Коэффициенты: реальные 1xBet или AI-оценка
    real_p1 = match.get("p1_odds", 0) or 0
    real_x = match.get("x_odds", 0) or 0
    real_p2 = match.get("p2_odds", 0) or 0
    ai_p1 = pred.get("ai_p1_odds", 0) or 0
    ai_x = pred.get("ai_x_odds", 0) or 0
    ai_p2 = pred.get("ai_p2_odds", 0) or 0

    if real_p1 > 1:
        odds_line = f"П1: <b>{real_p1:.2f}</b>  X: <b>{real_x:.2f}</b>  П2: <b>{real_p2:.2f}</b>"
        odds_note = "  <i>(1xBet)</i>"
    elif ai_p1 > 1:
        odds_line = f"П1: <b>{ai_p1:.2f}</b>  X: <b>{ai_x:.2f}</b>  П2: <b>{ai_p2:.2f}</b>"
        odds_note = "  <i>(AI-оценка)</i>"
    else:
        odds_line = "<i>нет данных</i>"
        odds_note = ""

    # Value / confidence
    conf_emoji = pred.get("conf_emoji", "🟡")
    confidence = pred.get("confidence", "средняя")
    value_pct = pred.get("value_pct", 0)
    is_value = pred.get("is_value", False)

    if is_value and value_pct > 0:
        value_line = f"🟢 <b>VALUE BET +{value_pct:.1f}%</b>"
    elif value_pct < 0 and pred.get("best_odds", 0) > 1:
        value_line = f"🟡 Value: {value_pct:.1f}%"
    else:
        value_line = ""

    # Ставка в UZS
    best_odds = pred.get("best_odds", 0) or 0
    stake_block = ""
    if best_odds > 1:
        stake_uzs = 127_000
        win_uzs = int(stake_uzs * best_odds)
        profit_uzs = win_uzs - stake_uzs
        stake_block = (
            "\n💵 <b>Расчёт ставки (10 USD ≈ 127 000 UZS):</b>\n"
            f"  Ставка: <code>127 000 UZS</code>\n"
            f"  Выигрыш: <code>{win_uzs:,} UZS</code>\n"
            f"  Прибыль: <code>+{profit_uzs:,} UZS</code>"
        ).replace(",", " ")

    # AI-analysis блок
    analysis = pred.get("analysis", "")
    analysis_block = f"\n\n🧠 <b>AI-анализ Mistral:</b>\n<i>{analysis}</i>" if analysis else ""

    # Источник данных
    pred_source = pred.get("source", "none")
    source_note = ""
    if source_tag == "demo":
        source_note = "\n⚠️ <i>Демо-данные.</i>"
    elif pred_source == "mistral+poisson":
        source_note = "\n✅ <i>Прогноз: Mistral AI + Poisson-модель</i>"
    elif pred_source == "mistral":
        source_note = "\n✅ <i>Прогноз: Mistral AI (история матчей)</i>"
    elif pred_source == "poisson":
        source_note = "\n📐 <i>Прогноз: Poisson-модель</i>"

    link = build_1xbet_link(home, away)

    return (
        f"⚽ <b>{home} vs {away}</b>\n"
        f"🏆 {league}  |  ⏱ <b>{score}</b>  |  Мин: {minute}\n\n"
        f"📊 <b>Коэффициенты 1xBet:</b>{odds_note}\n{odds_line}\n\n"
        f"🤖 <b>AI-вероятности:</b>\n"
        f"  П1: <b>{pred['prob_p1']}%</b>  X: <b>{pred['prob_x']}%</b>  П2: <b>{pred['prob_p2']}%</b>\n"
        f"  Обе забьют: {pred['btts']}%  |  ТБ 2.5: {pred['over25']}%\n\n"
        f"🎯 <b>Рекомендация:</b> {pred['best_bet']} @ <b>{best_odds:.2f}</b>\n"
        f"📈 Вероятность: <b>{pred['best_prob']}%</b>\n"
        f"{conf_emoji} Уверенность AI: <b>{confidence}</b>\n"
        f"{value_line}"
        f"{analysis_block}"
        f"{stake_block}\n\n"
        f"🔗 <a href='{link}'>Ставить на 1xBet UZ</a>"
        f"{source_note}\n\n"
        f"<i>⚠️ Прогнозы информационные. Ставки — ваш риск.</i>"
    )


def build_1xbet_link(home: str, away: str) -> str:
    q = f"{home} {away}".replace(" ", "+")
    return f"https://1xbet.uz/en/line/football/?query={q}"
