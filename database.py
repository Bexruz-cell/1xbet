import aiosqlite
import logging
from datetime import datetime

DB_PATH = "bot_data.db"
logger = logging.getLogger(__name__)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                has_access INTEGER DEFAULT 0,
                access_type TEXT DEFAULT 'none',
                stars_paid INTEGER DEFAULT 0,
                added_by_admin INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                joined_at TEXT,
                access_granted_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                match TEXT NOT NULL,
                prediction TEXT NOT NULL,
                coefficient REAL NOT NULL,
                value_pct REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # Default settings
        await db.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('stars_price', '100')
        """)
        await db.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_enabled', '1')
        """)
        await db.commit()


# ─── SETTINGS ────────────────────────────────────────────

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else ""


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        await db.commit()


async def get_stars_price() -> int:
    val = await get_setting("stars_price")
    try:
        return int(val)
    except Exception:
        return 100


async def set_stars_price(price: int):
    await set_setting("stars_price", str(price))


# ─── USERS ───────────────────────────────────────────────

async def register_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at)
            VALUES (?,?,?,?)
        """, (user_id, username or "", full_name or "", datetime.now().isoformat()))
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


async def has_access(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user:
        return False
    return bool(user["has_access"]) and not bool(user["is_blocked"])


async def grant_access(user_id: int, access_type: str = "paid", stars_paid: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET has_access=1, access_type=?, stars_paid=?, access_granted_at=?
            WHERE user_id=?
        """, (access_type, stars_paid, datetime.now().isoformat(), user_id))
        await db.commit()


async def add_user_by_admin(user_id: int):
    """Grant free access by admin."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure user row exists
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at)
            VALUES (?,?,?,?)
        """, (user_id, "", "Added by admin", datetime.now().isoformat()))
        await db.execute("""
            UPDATE users SET has_access=1, access_type='admin', added_by_admin=1, access_granted_at=?
            WHERE user_id=?
        """, (datetime.now().isoformat(), user_id))
        await db.commit()


async def revoke_access(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET has_access=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def block_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (user_id,))
        await db.commit()


async def unblock_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_blocked=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def get_all_users(limit: int = 50, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active = (await (await db.execute("SELECT COUNT(*) FROM users WHERE has_access=1 AND is_blocked=0")).fetchone())[0]
        paid = (await (await db.execute("SELECT COUNT(*) FROM users WHERE access_type='paid'")).fetchone())[0]
        admin_added = (await (await db.execute("SELECT COUNT(*) FROM users WHERE access_type='admin'")).fetchone())[0]
        blocked = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1")).fetchone())[0]
        total_stars = (await (await db.execute("SELECT SUM(stars_paid) FROM users")).fetchone())[0] or 0
        return {
            "total": total, "active": active, "paid": paid,
            "admin_added": admin_added, "blocked": blocked, "total_stars": total_stars
        }


# ─── PREDICTIONS ─────────────────────────────────────────

async def save_prediction(user_id: int, match: str, prediction: str, coefficient: float, value_pct: float):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO predictions (user_id, match, prediction, coefficient, value_pct, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, match, prediction, coefficient, value_pct, datetime.now().isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"save_prediction: {e}")


async def get_predictions(user_id: int, limit: int = 10) -> list:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT match, prediction, coefficient, value_pct, created_at FROM predictions WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ) as cur:
                rows = await cur.fetchall()
                return [{"match": r[0], "prediction": r[1], "coefficient": r[2], "value_pct": r[3], "created_at": r[4]} for r in rows]
    except Exception as e:
        logger.error(f"get_predictions: {e}")
        return []
