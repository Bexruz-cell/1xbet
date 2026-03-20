import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8030119188"))
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
MIN_COEFFICIENT = float(os.getenv("MIN_COEFFICIENT", "1.70"))
DEFAULT_STARS_PRICE = int(os.getenv("DEFAULT_STARS_PRICE", "100"))

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

USD_TO_UZS = 12700
