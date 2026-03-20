from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# ─── USER KEYBOARDS ───────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 LIVE Матчи + Прогнозы", callback_data="live_matches")],
        [
            InlineKeyboardButton(text="📊 Мои прогнозы", callback_data="my_predictions"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        ],
        [
            InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
            InlineKeyboardButton(text="ℹ️ О боте", callback_data="about"),
        ],
    ])


def pay_keyboard(stars_price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Оплатить {stars_price} Stars", callback_data="buy_access")],
        [InlineKeyboardButton(text="ℹ️ Что умеет бот?", callback_data="about_free")],
    ])


def matches_keyboard(matches: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, match in enumerate(matches[:12]):
        home = match.get("home_team", "?")[:10]
        away = match.get("away_team", "?")[:10]
        score = match.get("score", "")
        label = f"⚽ {home} {score} {away}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"match_{i}")])
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="live_matches"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def match_detail_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить прогноз", callback_data=f"match_{idx}"),
            InlineKeyboardButton(text="◀️ К матчам", callback_data="live_matches"),
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])


def settings_keyboard(min_coef: float, value_only: bool) -> InlineKeyboardMarkup:
    v = "✅" if value_only else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📉 Мин. кэф: {min_coef:.2f}", callback_data="noop")],
        [
            InlineKeyboardButton(text="➖ -0.1", callback_data="coef_down"),
            InlineKeyboardButton(text="➕ +0.1", callback_data="coef_up"),
        ],
        [InlineKeyboardButton(text=f"{v} Только value-беты", callback_data="toggle_value")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])


def back_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])


# ─── ADMIN KEYBOARDS ──────────────────────────────────────

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users")],
        [
            InlineKeyboardButton(text="➕ Добавить по ID", callback_data="admin_add_user"),
            InlineKeyboardButton(text="🚫 Заблокировать", callback_data="admin_block_user"),
        ],
        [
            InlineKeyboardButton(text="⭐ Цена Stars", callback_data="admin_stars_price"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton(text="✅ Разблокировать", callback_data="admin_unblock_user"),
            InlineKeyboardButton(text="❌ Забрать доступ", callback_data="admin_revoke_user"),
        ],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])


def admin_users_keyboard(users: list, page: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    for u in users:
        icon = "✅" if u["has_access"] and not u["is_blocked"] else ("🚫" if u["is_blocked"] else "❌")
        name = u.get("full_name") or u.get("username") or str(u["user_id"])
        label = f"{icon} {name[:20]} ({u['user_id']})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"admin_user_{u['user_id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_users_page_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}", callback_data="noop"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_users_page_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_detail_keyboard(user_id: int, has_access: bool, is_blocked: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_access:
        buttons.append([InlineKeyboardButton(text="❌ Забрать доступ", callback_data=f"admin_revoke_{user_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="✅ Дать доступ", callback_data=f"admin_grant_{user_id}")])
    if is_blocked:
        buttons.append([InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"admin_unblock_{user_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"admin_block_{user_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к списку", callback_data="admin_users")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_stars_keyboard(current: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Текущая цена: {current} Stars", callback_data="noop")],
        [
            InlineKeyboardButton(text="50 ⭐", callback_data="set_stars_50"),
            InlineKeyboardButton(text="100 ⭐", callback_data="set_stars_100"),
            InlineKeyboardButton(text="150 ⭐", callback_data="set_stars_150"),
        ],
        [
            InlineKeyboardButton(text="200 ⭐", callback_data="set_stars_200"),
            InlineKeyboardButton(text="300 ⭐", callback_data="set_stars_300"),
            InlineKeyboardButton(text="500 ⭐", callback_data="set_stars_500"),
        ],
        [InlineKeyboardButton(text="✏️ Ввести свою цену", callback_data="set_stars_custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]
    ])
