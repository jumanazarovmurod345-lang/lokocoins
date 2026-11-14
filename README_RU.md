# 🪙 LOGO COINS — Telegram Mini App (All-in-One)

Готовый игровой Telegram Mini App проект в удобной монолитной структуре (как в ShiftStore):
- **Flask** сервер, отдающий веб-приложение `web/index.html`.
- **python-telegram-bot**, запускающий бота и открывающий игру через кнопку меню.

## 🚀 Быстрый запуск

### 1. Установите зависимости:
```bash
pip install -r requirements.txt
```

### 2. Настройте файл `.env`:
Откройте файл `.env` в Блокноте и вставьте данные:
```ini
BOT_TOKEN=8893154925:AAGfHqwk8LZPsFD2HrdatrIcXkMhRGWKb0o
OWNER_ID=7206933215
BOT_USERNAME=ваш_юзернейм_бота
WEBAPP_URL=https://ваша-ссылка-на-приложение
PORT=10000
```

### 3. Запустите:
```bash
python bot.py
```

### 4. Деплой (Render / Railway / VPS):
В проекте есть `Procfile` (`web: python bot.py`), поэтому он готов к мгновенному деплою на любой хостинг (Render.com, Railway.app, Heroku, Amvera).
В настройках хостинга укажите переменные из `.env`.
