import aiohttp
import logging
from scipy.stats import poisson
from config import ODDS_API_KEY, API_FOOTBALL_KEY, ODDS_API_BASE, API_FOOTBALL_BASE, USD_TO_UZS

logger = logging.getLogger(__name__)

FOOTBALL_SPORTS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_uefa_champs_league",
    "soccer_uefa_europa_league", "soccer_turkey_super_league",
]


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
            status = fix.get("fixture", {}).get("status", {}).get("short", "")
            if status not in live_statuses:
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
                "score": f"{home_g}:{away_g}",
                "minute": f"{elapsed}'",
                "p1_odds": 0.0, "x_odds": 0.0, "p2_odds": 0.0,
                "league": league.get("name", "Football"),
                "source": "api_football",
            })
        # Enrich with odds if available
        if results and ODDS_API_KEY:
            for sport in FOOTBALL_SPORTS[:3]:
                try:
                    odds = await _get_odds_for_sport(sport)
                    for m in results:
                        match_odds = _find_odds(m, odds)
                        if match_odds:
                            m["p1_odds"] = match_odds[0]
                            m["x_odds"] = match_odds[1]
                            m["p2_odds"] = match_odds[2]
                except Exception:
                    pass
        return results
    except Exception as e:
        logger.error(f"_from_api_football: {e}")
        return []


async def _get_odds_for_sport(sport: str) -> list:
    url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal", "bookmakers": "onexbet"}
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
                    "home_team": ev["home_team"],
                    "away_team": ev["away_team"],
                    "score": "—",
                    "minute": "LIVE",
                    "p1_odds": oc.get(ev["home_team"], 0),
                    "x_odds": oc.get("Draw", 0),
                    "p2_odds": oc.get(ev["away_team"], 0),
                    "league": sport.replace("soccer_", "").replace("_", " ").title(),
                    "source": "odds_api",
                })
        return results[:15]
    except Exception as e:
        logger.error(f"_from_odds_api: {e}")
        return []


def _demo_matches() -> list:
    return [
        {"home_team": "Real Madrid", "away_team": "Barcelona", "score": "1:0", "minute": "67'",
         "p1_odds": 2.10, "x_odds": 3.40, "p2_odds": 3.20, "league": "La Liga", "source": "demo"},
        {"home_team": "Man City", "away_team": "Liverpool", "score": "0:1", "minute": "45'",
         "p1_odds": 1.85, "x_odds": 3.70, "p2_odds": 4.10, "league": "Premier League", "source": "demo"},
        {"home_team": "Bayern", "away_team": "Dortmund", "score": "2:2", "minute": "78'",
         "p1_odds": 1.60, "x_odds": 4.00, "p2_odds": 5.50, "league": "Bundesliga", "source": "demo"},
        {"home_team": "PSG", "away_team": "Marseille", "score": "3:1", "minute": "83'",
         "p1_odds": 1.45, "x_odds": 4.50, "p2_odds": 7.00, "league": "Ligue 1", "source": "demo"},
        {"home_team": "Juventus", "away_team": "Inter", "score": "0:0", "minute": "22'",
         "p1_odds": 2.80, "x_odds": 3.10, "p2_odds": 2.60, "league": "Serie A", "source": "demo"},
    ]


# ─── PREDICTION MODEL ────────────────────────────────────

def calculate_prediction(match: dict) -> dict:
    p1_o = match.get("p1_odds", 0) or 0
    x_o = match.get("x_odds", 0) or 0
    p2_o = match.get("p2_odds", 0) or 0

    if p1_o < 1.01 or x_o < 1.01 or p2_o < 1.01:
        return _empty_pred()

    # Remove bookmaker margin
    imp1, impx, imp2 = 1 / p1_o, 1 / x_o, 1 / p2_o
    total = imp1 + impx + imp2
    prob_p1, prob_x, prob_p2 = imp1 / total, impx / total, imp2 / total

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

    # Expected goals remaining
    avg = 2.5
    lh = avg * 0.55 * prob_p1 / max(prob_p1 + prob_p2, 0.01) * remaining
    la = avg * 0.45 * prob_p2 / max(prob_p1 + prob_p2, 0.01) * remaining

    diff = hg - ag
    if diff < 0:
        lh *= 1.35; la *= 0.80
    elif diff > 0:
        lh *= 0.80; la *= 1.25

    # Score distribution
    fp1 = fx = fp2 = 0.0
    btts = 0.0
    over25 = 0.0
    for h in range(7):
        for a in range(7):
            p = poisson.pmf(h, lh) * poisson.pmf(a, la)
            th, ta = hg + h, ag + a
            if th > ta:
                fp1 += p
            elif th == ta:
                fx += p
            else:
                fp2 += p
            if th >= 1 and ta >= 1:
                btts += p
            if th + ta >= 3:
                over25 += p

    total_p = fp1 + fx + fp2
    if total_p > 0:
        fp1 /= total_p; fx /= total_p; fp2 /= total_p

    btts = min(btts, 0.99)
    over25 = min(over25, 0.99)

    btts_odds = round(1 / btts * 0.90, 2) if btts > 0.05 else 0
    over_odds = round(1 / over25 * 0.90, 2) if over25 > 0.05 else 0

    candidates = [
        {"name": "П1 (победа дома)", "prob": fp1, "odds": p1_o},
        {"name": "X (ничья)", "prob": fx, "odds": x_o},
        {"name": "П2 (победа гостей)", "prob": fp2, "odds": p2_o},
        {"name": "Обе забьют (ДА)", "prob": btts, "odds": btts_odds},
        {"name": "ТБ 2.5 голов", "prob": over25, "odds": over_odds},
    ]
    for c in candidates:
        if c["odds"] > 1:
            c["value"] = (c["prob"] - 1 / c["odds"]) * 100
        else:
            c["value"] = -99

    value_bets = [c for c in candidates if c["value"] > 0 and c["odds"] >= 1.50]
    best = max(value_bets, key=lambda c: c["value"]) if value_bets else max(candidates, key=lambda c: c["prob"])

    return {
        "prob_p1": round(fp1 * 100, 1),
        "prob_x": round(fx * 100, 1),
        "prob_p2": round(fp2 * 100, 1),
        "btts": round(btts * 100, 1),
        "over25": round(over25 * 100, 1),
        "best_bet": best["name"],
        "best_odds": best["odds"],
        "best_prob": round(best["prob"] * 100, 1),
        "value_pct": round(best["value"], 1),
        "is_value": best["value"] > 0,
        "all": candidates,
    }


def _empty_pred() -> dict:
    return {"prob_p1": 0, "prob_x": 0, "prob_p2": 0, "btts": 0, "over25": 0,
            "best_bet": "—", "best_odds": 0, "best_prob": 0, "value_pct": 0,
            "is_value": False, "all": []}


def format_match_card(match: dict, pred: dict, idx: int) -> str:
    home, away = match["home_team"], match["away_team"]
    score = match.get("score", "—")
    minute = match.get("minute", "LIVE")
    league = match.get("league", "Football")
    source = match.get("source", "")

    p1_o = match.get("p1_odds", 0)
    x_o = match.get("x_odds", 0)
    p2_o = match.get("p2_odds", 0)

    odds_str = ""
    if p1_o > 1:
        odds_str = f"П1: <b>{p1_o:.2f}</b>  X: <b>{x_o:.2f}</b>  П2: <b>{p2_o:.2f}</b>"
    else:
        odds_str = "<i>Нет данных по коэффициентам</i>"

    value_line = ""
    if pred["is_value"] and pred["value_pct"] > 0:
        value_line = f"🟢 <b>VALUE BET +{pred['value_pct']:.1f}%</b> — кэф превышает реальную вер."
    elif pred["value_pct"] < 0 and pred["best_odds"] > 1:
        value_line = f"🟡 Нет value ({pred['value_pct']:.1f}%)"

    stake_uzs = 127000  # 10 USD в UZS
    if pred["best_odds"] > 1:
        win_uzs = int(stake_uzs * pred["best_odds"])
        profit_uzs = win_uzs - stake_uzs
        stake_block = (
            f"\n💵 <b>Расчёт ставки (10 USD = 127 000 UZS):</b>\n"
            f"  Ставка: <code>127 000 UZS</code>\n"
            f"  Выигрыш: <code>{win_uzs:,} UZS</code>\n"
            f"  Прибыль: <code>+{profit_uzs:,} UZS</code>"
        ).replace(",", " ")
    else:
        stake_block = ""

    link = build_1xbet_link(home, away)
    demo_note = "\n⚠️ <i>Демо-данные. Добавьте API-ключи для реальных матчей.</i>" if source == "demo" else ""

    return (
        f"⚽ <b>{home} vs {away}</b>\n"
        f"🏆 {league}  |  ⏱ Счёт: <b>{score}</b>  Мин: {minute}\n\n"
        f"📊 <b>Коэффициенты 1xBet:</b>\n{odds_str}\n\n"
        f"🤖 <b>AI-анализ:</b>\n"
        f"  П1: {pred['prob_p1']}%  X: {pred['prob_x']}%  П2: {pred['prob_p2']}%\n"
        f"  Обе забьют: {pred['btts']}%  |  ТБ 2.5: {pred['over25']}%\n\n"
        f"🎯 <b>Рекомендация:</b> {pred['best_bet']} @ {pred['best_odds']:.2f}\n"
        f"📈 Вероятность по модели: {pred['best_prob']}%\n"
        f"{value_line}"
        f"{stake_block}\n\n"
        f"🔗 <a href='{link}'>Перейти к матчу на 1xBet UZ</a>"
        f"{demo_note}\n\n"
        f"<i>⚠️ Прогнозы информационные. Ставки — на ваш страх и риск.</i>"
    )


def build_1xbet_link(home: str, away: str) -> str:
    q = f"{home} {away}".replace(" ", "+")
    return f"https://1xbet.uz/en/line/football/?query={q}"
