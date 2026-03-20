import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice,
    PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from config import ADMIN_ID, MIN_COEFFICIENT
from keyboards import (
    main_menu_keyboard, pay_keyboard, matches_keyboard,
    match_detail_keyboard, settings_keyboard, back_menu_keyboard,
    admin_keyboard, admin_users_keyboard, admin_user_detail_keyboard,
    admin_stars_keyboard, cancel_keyboard
)
from utils import fetch_live_matches, calculate_prediction, format_match_card
from database import (
    register_user, get_user, has_access, grant_access,
    add_user_by_admin, revoke_access, block_user, unblock_user,
    get_all_users, get_stats, get_stars_price, set_stars_price,
    save_prediction, get_predictions
)

logger = logging.getLogger(__name__)
router = Router()

# In-memory
_matches_cache: list = []
_user_settings: dict = {}  # {user_id: {min_coef, value_only}}


class AdminStates(StatesGroup):
    waiting_add_user_id = State()
    waiting_block_user_id = State()
    waiting_unblock_user_id = State()
    waiting_revoke_user_id = State()
    waiting_custom_stars = State()
    waiting_broadcast = State()


WELCOME_PHOTO = "https://i.imgur.com/4M7IWwP.jpeg"


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def get_user_settings(user_id: int) -> dict:
    if user_id not in _user_settings:
        _user_settings[user_id] = {"min_coef": 1.70, "value_only": False}
    return _user_settings[user_id]


# ═══════════════════════════════════════════════════════════
#  /START
# ═══════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    await register_user(user_id, message.from_user.username, message.from_user.full_name)

    if is_admin(user_id):
        await _show_main_menu(message, send=True)
        return

    user = await get_user(user_id)
    if user and user.get("is_blocked"):
        await message.answer("🚫 Ваш аккаунт заблокирован.")
        return

    if user and user.get("has_access"):
        await _show_main_menu(message, send=True)
    else:
        await _show_paywall(message, send=True)


async def _show_main_menu(obj, send=False):
    text = (
        "⚽ <b>Football AI Predictions</b>\n\n"
        "🔴 Прогнозы на LIVE-матчи с коэффициентами 1xBet\n"
        "🤖 AI-модель (Poisson + теория вероятностей)\n"
        "💵 Расчёт ставок в <b>UZS</b> 🇺🇿\n\n"
        "Выберите действие:"
    )
    kbd = main_menu_keyboard()
    if send:
        if isinstance(obj, Message):
            try:
                await obj.answer_photo(photo=WELCOME_PHOTO, caption=text, reply_markup=kbd, parse_mode="HTML")
            except Exception:
                await obj.answer(text, reply_markup=kbd, parse_mode="HTML")
    else:
        try:
            await obj.message.edit_caption(caption=text, reply_markup=kbd, parse_mode="HTML")
        except Exception:
            await obj.message.edit_text(text=text, reply_markup=kbd, parse_mode="HTML")


async def _show_paywall(obj, send=False):
    stars = await get_stars_price()
    text = (
        "⚽ <b>Football AI Predictions Bot</b>\n\n"
        "🤖 AI-прогнозы на LIVE-матчи\n"
        "📊 Коэффициенты 1xBet в реальном времени\n"
        "💰 Value-беты с расчётом в <b>UZS</b>\n\n"
        f"🔒 Доступ открывается после оплаты <b>{stars} ⭐ Telegram Stars</b>\n\n"
        "Нажмите кнопку ниже, чтобы оплатить и получить доступ:"
    )
    kbd = pay_keyboard(stars)
    if send:
        if isinstance(obj, Message):
            try:
                await obj.answer_photo(photo=WELCOME_PHOTO, caption=text, reply_markup=kbd, parse_mode="HTML")
            except Exception:
                await obj.answer(text, reply_markup=kbd, parse_mode="HTML")
    else:
        try:
            await obj.message.edit_caption(caption=text, reply_markup=kbd, parse_mode="HTML")
        except Exception:
            await obj.message.edit_text(text=text, reply_markup=kbd, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════
#  PAYMENT (Telegram Stars)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "buy_access")
async def cb_buy_access(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    await register_user(user_id, callback.from_user.username, callback.from_user.full_name)

    user = await get_user(user_id)
    if user and user.get("has_access"):
        await callback.answer("✅ У вас уже есть доступ!", show_alert=True)
        return

    stars = await get_stars_price()
    await callback.answer()
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title="⚽ Доступ к Football AI Bot",
            description=f"Полный доступ к AI-прогнозам на LIVE-футбол. Коэффициенты 1xBet, value-беты, расчёт в UZS.",
            payload="access_payment",
            currency="XTR",
            prices=[LabeledPrice(label="Доступ к боту", amount=stars)],
        )
    except Exception as e:
        logger.error(f"send_invoice error: {e}")
        await callback.message.answer("❌ Ошибка при создании счёта. Попробуйте позже.")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def payment_success(message: Message, bot: Bot):
    user_id = message.from_user.id
    stars_paid = message.successful_payment.total_amount
    await register_user(user_id, message.from_user.username, message.from_user.full_name)
    await grant_access(user_id, access_type="paid", stars_paid=stars_paid)
    logger.info(f"User {user_id} paid {stars_paid} stars and got access")

    # Notify admin
    try:
        name = message.from_user.full_name or message.from_user.username or str(user_id)
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>Новая оплата!</b>\n"
            f"👤 {name} (<code>{user_id}</code>)\n"
            f"⭐ Оплатил: {stars_paid} Stars",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await message.answer(
        "✅ <b>Оплата прошла успешно!</b>\n\n"
        "🎉 Добро пожаловать в Football AI Predictions Bot!\n"
        "Теперь у вас есть полный доступ.",
        parse_mode="HTML"
    )
    await _show_main_menu(message, send=True)


# ═══════════════════════════════════════════════════════════
#  MAIN MENU CALLBACKS
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    await register_user(user_id, callback.from_user.username, callback.from_user.full_name)
    if not is_admin(user_id) and not await has_access(user_id):
        await _show_paywall(callback)
        await callback.answer()
        return
    await _show_main_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "about_free")
async def cb_about_free(callback: CallbackQuery):
    await callback.message.edit_caption(
        caption=(
            "ℹ️ <b>Что умеет Football AI Bot?</b>\n\n"
            "🔴 Парсит <b>LIVE-матчи</b> прямо сейчас\n"
            "📊 Показывает коэффициенты <b>1xBet UZ</b>\n"
            "🤖 Считает вероятности через <b>AI-модель (Poisson)</b>\n"
            "💡 Находит <b>value-беты</b> (где кэф выше реальной вер.)\n"
            "💵 Расчёт ставок в <b>UZS</b> 🇺🇿\n"
            "📋 История ваших прогнозов\n"
            "⚙️ Настройки фильтров\n\n"
            "⚠️ Прогнозы информационные, ставки — на ваш риск."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_pay")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_pay")
async def cb_back_to_pay(callback: CallbackQuery):
    await _show_paywall(callback)
    await callback.answer()


@router.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery):
    if not is_admin(callback.from_user.id) and not await has_access(callback.from_user.id):
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    await callback.message.edit_caption(
        caption=(
            "ℹ️ <b>Football AI Predictions Bot</b>\n\n"
            "Версия: 2.0\n"
            "🤖 Модель: Poisson Distribution + Value Betting\n"
            "📡 Данные: API-Football + The Odds API\n"
            "🏦 Букмекер: 1xBet Uzbekistan\n"
            "💵 Валюта: UZS 🇺🇿\n\n"
            "Данные обновляются каждые 5 минут."
        ),
        reply_markup=back_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  LIVE MATCHES
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "live_matches")
async def cb_live_matches(callback: CallbackQuery):
    global _matches_cache
    user_id = callback.from_user.id
    if not is_admin(user_id) and not await has_access(user_id):
        await _show_paywall(callback)
        await callback.answer()
        return
    await callback.answer("⏳ Загружаю LIVE-матчи...")
    try:
        matches = await fetch_live_matches()
        _matches_cache = matches
        displayed = matches

        if not displayed:
            await callback.message.edit_caption(
                caption="⚠️ Нет активных LIVE-матчей. Попробуйте через 2 минуты.",
                reply_markup=back_menu_keyboard(), parse_mode="HTML")
            return

        source_note = " <i>(демо)</i>" if displayed[0].get("source") == "demo" else ""
        await callback.message.edit_caption(
            caption=f"🔴 <b>LIVE-матчи — {len(displayed)} игр{source_note}</b>\n\nВыберите матч для прогноза:",
            reply_markup=matches_keyboard(displayed), parse_mode="HTML")
    except Exception as e:
        logger.error(f"cb_live_matches: {e}")
        await callback.message.edit_caption(
            caption="❌ Данные недоступны. Попробуйте через 2 минуты.",
            reply_markup=back_menu_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("match_"))
async def cb_match_detail(callback: CallbackQuery):
    global _matches_cache
    user_id = callback.from_user.id
    if not is_admin(user_id) and not await has_access(user_id):
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    try:
        idx = int(callback.data.split("_")[1])
    except Exception:
        await callback.answer("Ошибка")
        return
    if not _matches_cache or idx >= len(_matches_cache):
        await callback.answer("Матч не найден. Обновите список.", show_alert=True)
        return
    await callback.answer("🤖 Считаю прогноз (AI анализирует историю)...")
    match = _matches_cache[idx]
    try:
        pred = await calculate_prediction(match)
        text = format_match_card(match, pred)
        await save_prediction(
            user_id=user_id,
            match=f"{match['home_team']} vs {match['away_team']}",
            prediction=f"{pred['best_bet']} @ {pred['best_odds']:.2f}",
            coefficient=pred["best_odds"],
            value_pct=pred["value_pct"],
        )
        await callback.message.edit_caption(
            caption=text[:1024],
            reply_markup=match_detail_keyboard(idx),
            parse_mode="HTML", disable_web_page_preview=False)
    except Exception as e:
        logger.error(f"cb_match_detail: {e}")
        await callback.message.edit_caption(
            caption="❌ Ошибка расчёта прогноза.",
            reply_markup=match_detail_keyboard(idx), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════
#  MY PREDICTIONS
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "my_predictions")
async def cb_my_predictions(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id) and not await has_access(user_id):
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    preds = await get_predictions(user_id, limit=10)
    if not preds:
        caption = "📋 <b>История прогнозов пуста.</b>\nНажмите «LIVE Матчи» для первого прогноза."
    else:
        lines = ["📋 <b>Последние прогнозы:</b>\n"]
        for p in preds:
            ico = "🟢" if p["value_pct"] > 0 else "🔴"
            lines.append(
                f"{ico} <b>{p['match']}</b>\n"
                f"  ➤ {p['prediction']} | Value: {p['value_pct']:+.1f}%\n"
                f"  🕐 {p['created_at'][:16]}\n"
            )
        caption = "\n".join(lines)
    await callback.message.edit_caption(
        caption=caption[:1024], reply_markup=back_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id) and not await has_access(user_id):
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    s = get_user_settings(user_id)
    await callback.message.edit_caption(
        caption=(f"⚙️ <b>Настройки</b>\n\nМин. коэффициент: <b>{s['min_coef']:.2f}</b>\n"
                 f"Только value-беты: <b>{'✅ Да' if s['value_only'] else '❌ Нет'}</b>"),
        reply_markup=settings_keyboard(s["min_coef"], s["value_only"]), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "coef_up")
async def cb_coef_up(callback: CallbackQuery):
    s = get_user_settings(callback.from_user.id)
    s["min_coef"] = round(min(s["min_coef"] + 0.1, 5.0), 2)
    await callback.message.edit_caption(
        caption=f"⚙️ <b>Настройки</b>\n\nМин. коэффициент: <b>{s['min_coef']:.2f}</b>\nТолько value-беты: <b>{'✅ Да' if s['value_only'] else '❌ Нет'}</b>",
        reply_markup=settings_keyboard(s["min_coef"], s["value_only"]), parse_mode="HTML")
    await callback.answer(f"✅ Кэф: {s['min_coef']:.2f}")


@router.callback_query(F.data == "coef_down")
async def cb_coef_down(callback: CallbackQuery):
    s = get_user_settings(callback.from_user.id)
    s["min_coef"] = round(max(s["min_coef"] - 0.1, 1.10), 2)
    await callback.message.edit_caption(
        caption=f"⚙️ <b>Настройки</b>\n\nМин. коэффициент: <b>{s['min_coef']:.2f}</b>\nТолько value-беты: <b>{'✅ Да' if s['value_only'] else '❌ Нет'}</b>",
        reply_markup=settings_keyboard(s["min_coef"], s["value_only"]), parse_mode="HTML")
    await callback.answer(f"✅ Кэф: {s['min_coef']:.2f}")


@router.callback_query(F.data == "toggle_value")
async def cb_toggle_value(callback: CallbackQuery):
    s = get_user_settings(callback.from_user.id)
    s["value_only"] = not s["value_only"]
    await callback.message.edit_caption(
        caption=f"⚙️ <b>Настройки</b>\n\nМин. коэффициент: <b>{s['min_coef']:.2f}</b>\nТолько value-беты: <b>{'✅ Да' if s['value_only'] else '❌ Нет'}</b>",
        reply_markup=settings_keyboard(s["min_coef"], s["value_only"]), parse_mode="HTML")
    await callback.answer("✅ Сохранено")


@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    await callback.message.edit_caption(
        caption=(
            "💬 <b>Поддержка</b>\n\n"
            "По вопросам работы бота обратитесь к администратору.\n\n"
            "📡 Источники данных:\n"
            "• <a href='https://the-odds-api.com'>The Odds API</a> — коэффициенты\n"
            "• <a href='https://www.api-football.com'>API-Football</a> — LIVE-матчи\n\n"
            "⚠️ Все прогнозы носят информационный характер."
        ),
        reply_markup=back_menu_keyboard(), parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ADMIN PANEL
# ═══════════════════════════════════════════════════════════

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🛠 <b>Админ-панель</b>",
        reply_markup=admin_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=admin_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer("🛠 <b>Админ-панель</b>", reply_markup=admin_keyboard(), parse_mode="HTML")
    await callback.answer()


# ─── Stats ────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    stats = await get_stats()
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total']}</b>\n"
        f"✅ С доступом: <b>{stats['active']}</b>\n"
        f"💰 Оплатили Stars: <b>{stats['paid']}</b>\n"
        f"🎁 Добавлены админом: <b>{stats['admin_added']}</b>\n"
        f"🚫 Заблокированы: <b>{stats['blocked']}</b>\n"
        f"⭐ Всего Stars собрано: <b>{stats['total_stars']}</b>\n\n"
        f"⭐ Текущая цена: <b>{await get_stars_price()} Stars</b>"
    )
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ]), parse_mode="HTML")
    await callback.answer()


# ─── Users list ───────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await _show_users_page(callback, 0)


@router.callback_query(F.data.startswith("admin_users_page_"))
async def cb_admin_users_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    page = int(callback.data.split("_")[-1])
    await _show_users_page(callback, page)


async def _show_users_page(callback: CallbackQuery, page: int):
    users = await get_all_users(limit=10, offset=page * 10)
    stats = await get_stats()
    total = stats["total"]
    if not users:
        await callback.message.edit_text("Пользователей нет.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]]))
        await callback.answer()
        return
    await callback.message.edit_text(
        f"👥 <b>Пользователи</b> (стр. {page + 1}, всего: {total})",
        reply_markup=admin_users_keyboard(users, page, total), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_"))
async def cb_admin_user_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    if len(parts) < 3:
        return
    try:
        uid = int(parts[2])
    except Exception:
        return
    user = await get_user(uid)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    name = user.get("full_name") or user.get("username") or str(uid)
    access_type = user.get("access_type", "none")
    status = "✅ Есть доступ" if user["has_access"] and not user["is_blocked"] else ("🚫 Заблокирован" if user["is_blocked"] else "❌ Нет доступа")
    text = (
        f"👤 <b>{name}</b>\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📌 Статус: {status}\n"
        f"💳 Тип: {access_type}\n"
        f"⭐ Stars: {user.get('stars_paid', 0)}\n"
        f"📅 Зарегистрирован: {(user.get('joined_at') or '')[:16]}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_user_detail_keyboard(uid, bool(user["has_access"]), bool(user["is_blocked"])),
        parse_mode="HTML")
    await callback.answer()


# ─── Grant / Revoke / Block ───────────────────────────────

@router.callback_query(F.data.startswith("admin_grant_"))
async def cb_admin_grant(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[2])
    await add_user_by_admin(uid)
    await callback.answer(f"✅ Доступ выдан {uid}", show_alert=True)
    await cb_admin_user_detail(callback)


@router.callback_query(F.data.startswith("admin_revoke_"))
async def cb_admin_revoke_btn(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[2])
    await revoke_access(uid)
    await callback.answer(f"❌ Доступ забран у {uid}", show_alert=True)
    await cb_admin_user_detail(callback)


@router.callback_query(F.data.startswith("admin_block_") and ~F.data.endswith("_user"))
async def cb_admin_block_btn(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[2])
    await block_user(uid)
    await callback.answer(f"🚫 Пользователь {uid} заблокирован", show_alert=True)
    await cb_admin_user_detail(callback)


@router.callback_query(F.data.startswith("admin_unblock_") and ~F.data.endswith("_user"))
async def cb_admin_unblock_btn(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[2])
    await unblock_user(uid)
    await callback.answer(f"✅ Пользователь {uid} разблокирован", show_alert=True)
    await cb_admin_user_detail(callback)


# ─── Add user by ID (FSM) ─────────────────────────────────

@router.callback_query(F.data == "admin_add_user")
async def cb_admin_add_user_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_add_user_id)
    await callback.message.edit_text(
        "➕ <b>Добавить пользователя бесплатно</b>\n\nВведите Telegram ID пользователя:",
        reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminStates.waiting_add_user_id)
async def admin_process_add_user(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("❌ Введите корректный числовой ID:")
        return
    uid = int(text)
    await add_user_by_admin(uid)
    await state.clear()
    await message.answer(
        f"✅ Пользователь <code>{uid}</code> добавлен и получил бесплатный доступ.",
        reply_markup=admin_keyboard(), parse_mode="HTML")
    # Notify user
    try:
        await bot.send_message(uid,
            "🎁 <b>Вам выдан бесплатный доступ к Football AI Bot!</b>\n\nНапишите /start",
            parse_mode="HTML")
    except Exception:
        pass


# ─── Block / Unblock / Revoke via input ───────────────────

@router.callback_query(F.data == "admin_block_user")
async def cb_admin_block_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_block_user_id)
    await callback.message.edit_text(
        "🚫 <b>Заблокировать пользователя</b>\n\nВведите Telegram ID:",
        reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminStates.waiting_block_user_id)
async def admin_process_block(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("❌ Введите корректный ID:")
        return
    uid = int(text)
    await block_user(uid)
    await state.clear()
    await message.answer(f"🚫 Пользователь <code>{uid}</code> заблокирован.", reply_markup=admin_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "admin_unblock_user")
async def cb_admin_unblock_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_unblock_user_id)
    await callback.message.edit_text(
        "✅ <b>Разблокировать пользователя</b>\n\nВведите Telegram ID:",
        reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminStates.waiting_unblock_user_id)
async def admin_process_unblock(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("❌ Введите корректный ID:")
        return
    uid = int(text)
    await unblock_user(uid)
    await state.clear()
    await message.answer(f"✅ Пользователь <code>{uid}</code> разблокирован.", reply_markup=admin_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "admin_revoke_user")
async def cb_admin_revoke_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_revoke_user_id)
    await callback.message.edit_text(
        "❌ <b>Забрать доступ у пользователя</b>\n\nВведите Telegram ID:",
        reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminStates.waiting_revoke_user_id)
async def admin_process_revoke(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("❌ Введите корректный ID:")
        return
    uid = int(text)
    await revoke_access(uid)
    await state.clear()
    await message.answer(f"❌ Доступ у <code>{uid}</code> забран.", reply_markup=admin_keyboard(), parse_mode="HTML")


# ─── Stars price ──────────────────────────────────────────

@router.callback_query(F.data == "admin_stars_price")
async def cb_admin_stars_price(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    current = await get_stars_price()
    await callback.message.edit_text(
        f"⭐ <b>Цена доступа</b>\n\nТекущая цена: <b>{current} Stars</b>\n\nВыберите новую цену:",
        reply_markup=admin_stars_keyboard(current), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("set_stars_") and ~F.data.endswith("custom"))
async def cb_set_stars_preset(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    val = int(callback.data.split("_")[-1])
    await set_stars_price(val)
    await callback.answer(f"✅ Цена установлена: {val} Stars", show_alert=True)
    await callback.message.edit_text(
        f"⭐ <b>Цена доступа</b>\n\nТекущая цена: <b>{val} Stars</b>\n\nВыберите новую цену:",
        reply_markup=admin_stars_keyboard(val), parse_mode="HTML")


@router.callback_query(F.data == "set_stars_custom")
async def cb_set_stars_custom(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_custom_stars)
    await callback.message.edit_text(
        "✏️ Введите новую цену в Stars (число от 1 до 2500):",
        reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminStates.waiting_custom_stars)
async def admin_process_custom_stars(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip() if message.text else ""
    if not text.isdigit() or not (1 <= int(text) <= 2500):
        await message.answer("❌ Введите число от 1 до 2500:")
        return
    val = int(text)
    await set_stars_price(val)
    await state.clear()
    await message.answer(f"✅ Цена установлена: <b>{val} Stars</b>", reply_markup=admin_keyboard(), parse_mode="HTML")


# ─── Broadcast ────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\nВведите текст сообщения (поддерживается HTML).\nОтправится всем пользователям с доступом:",
        reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminStates.waiting_broadcast)
async def admin_process_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    text = message.text or message.caption or ""
    if not text:
        await message.answer("❌ Сообщение пустое.")
        return
    await state.clear()
    users = await get_all_users(limit=1000)
    active = [u for u in users if u["has_access"] and not u["is_blocked"]]
    sent = 0
    failed = 0
    for u in active:
        try:
            await bot.send_message(u["user_id"], text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"📢 Рассылка завершена.\n✅ Отправлено: {sent}\n❌ Ошибок: {failed}",
        reply_markup=admin_keyboard())


# ─── Noop ─────────────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
