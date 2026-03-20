# ⚽ Football AI Predictions Bot v2.0

AI-бот для Telegram с прогнозами на LIVE-футбол, платёжной системой Telegram Stars и полноценной админ-панелью.

## 🚀 Функции

### Для пользователей
- 🔴 LIVE-матчи в реальном времени
- 🤖 AI-прогнозы (модель Poisson)
- 📊 Коэффициенты 1xBet UZ
- 💰 Value-беты (шанс прохода выше кэфа)
- 🇺🇿 Расчёты в UZS
- 📋 История прогнозов

### Оплата
- ⭐ Доступ через Telegram Stars
- Цена настраивается в реальном времени через админ-панель

### Для администратора
- ➕ Добавить пользователя бесплатно по ID
- 🚫 Заблокировать / разблокировать
- ❌ Забрать доступ
- ⭐ Изменить цену Stars (готовые варианты + ручной ввод)
- 📊 Статистика: пользователи, Stars, оплаты
- 📢 Рассылка всем активным пользователям

## ⚙️ Установка

### GitHub Secrets

Перейдите: `Settings → Secrets and variables → Actions → New repository secret`

| Secret | Значение |
|--------|----------|
| `BOT_TOKEN` | Токен от @BotFather |
| `ADMIN_ID` | Ваш Telegram ID |
| `ODDS_API_KEY` | Ключ от [the-odds-api.com](https://the-odds-api.com) |
| `API_FOOTBALL_KEY` | Ключ от [api-football.com](https://api-football.com) |
| `DEFAULT_STARS_PRICE` | `100` |

## 📁 Структура

```
├── main.py         — Запуск
├── handlers.py     — Все обработчики + FSM
├── keyboards.py    — Клавиатуры
├── utils.py        — API + AI модель
├── database.py     — SQLite
├── config.py       — Конфигурация
└── .github/workflows/bot.yml
```
