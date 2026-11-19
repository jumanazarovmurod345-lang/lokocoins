"""LOGO COINS (RubUP Style Redesign)
Backend with Flask, WebApp API, Admin Panel, Manual Card Top-ups with receipts & Telegram Bot.
"""

import os
import json
import hmac
import hashlib
import logging
import threading
import time
import uuid
import urllib.parse
from pathlib import Path
import asyncio

from dotenv import load_dotenv
from flask import Flask, send_from_directory, jsonify, request as freq, abort
from werkzeug.utils import secure_filename
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import database as db

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else None
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

bot_app: Application = None
bot_loop = None

# ── Telegram initData validation ─────────────────────────────
def validate_init_data(raw: str) -> dict | None:
    if not raw or not TOKEN:
        # Для тестов в обычном браузере, если нужно (демо-пользователь)
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        recv_hash = parsed.pop("hash", "")
        if not recv_hash:
            return None

        check_str = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calc, recv_hash):
            return None

        auth_date = int(parsed.get("auth_date", 0))
        # Проверяем не старше 7 дней
        if time.time() - auth_date > 86400 * 7:
            return None

        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def get_current_user_from_req(req) -> dict | None:
    # 1. Проверяем заголовок или body init_data
    init_data = ""
    if req.is_json:
        init_data = (req.get_json(silent=True) or {}).get("init_data", "")
    if not init_data:
        init_data = req.form.get("init_data", "")
    if not init_data:
        init_data = req.headers.get("X-Init-Data", "")

    user = validate_init_data(init_data)
    if user and user.get("id"):
        return user

    # Для удобства локальной отладки/демо в браузере без телеграма:
    if not TOKEN or TOKEN == "your_bot_token_here":
        return {"id": 999999, "first_name": "Тестер", "username": "tester"}
    return None


def is_admin(user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    return False


# ── Flask App ─────────────────────────────────────────────────
web = Flask(__name__, static_folder=None)
web.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload


@web.get("/")
def home():
    return send_from_directory(WEB_DIR, "index.html")


@web.get("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@web.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@web.get("/health")
def health():
    return jsonify(status="ok", app="LOGO COINS RubUP")


# ── API: User state & settings ────────────────────────────────
@web.post("/api/user")
def api_user():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    data = db.get_or_create(user["id"], user.get("first_name", ""), user.get("username", ""))
    settings = db.get_all_settings()
    is_adm = is_admin(user["id"])

    # Последние заявки пользователя
    my_requests = db.get_user_deposit_requests(user["id"])

    return jsonify(
        ok=True,
        user={
            "id": user["id"],
            "first_name": data["first_name"],
            "username": data["username"],
            "coins": data["coins"],
            "stars": data["stars"],
            "deposited": data["deposited"],
            "last_free": data["last_free"],
            "rank_claims": data["rank_claims"],
            "inventory": data["inventory"],
            "promos": data["promos"],
            "is_admin": is_adm,
        },
        settings={
            "card_number": settings.get("card_number", ""),
            "card_holder": settings.get("card_holder", ""),
            "card_bank": settings.get("card_bank", ""),
            "rate_rub_to_star": float(settings.get("rate_rub_to_star", "2.0")),
        },
        deposit_requests=my_requests,
    )


# ── API: Save in-game progress (coins, cases, promo) ──────────
@web.post("/api/save")
def api_save():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    body = freq.get_json(silent=True) or {}
    state = body.get("state", {})
    current = db.get_or_create(user["id"])

    # Звёзды списываются или прибавляются при победе
    stars_delta = int(state.get("stars_delta", 0))
    if stars_delta > 5000:  # Лимит защиты
        stars_delta = 0

    new_stars = max(0, current["stars"] + stars_delta)

    safe = {
        "coins": max(0, int(state.get("coins", 0))),
        "stars": new_stars,
        "last_free": int(state.get("last_free", 0)),
        "rank_claims": state.get("rank_claims", {}),
        "inventory": state.get("inventory", []),
        "promos": state.get("promos", {}),
    }
    db.save_state(user["id"], safe)
    return jsonify(ok=True, stars=new_stars, coins=safe["coins"], deposited=current["deposited"])


# ── API: Create Deposit Request with Receipt Upload ───────────
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

@web.post("/api/deposit/create")
def api_deposit_create():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    try:
        amount_rub = int(freq.form.get("amount_rub", 0))
    except ValueError:
        return jsonify(ok=False, error="Некорректная сумма"), 400

    if amount_rub < 50:
        return jsonify(ok=False, error="Минимальная сумма пополнения — 50 ₽"), 400
    if amount_rub > 300000:
        return jsonify(ok=False, error="Максимальная сумма пополнения — 300 000 ₽"), 400

    rate = float(db.get_setting("rate_rub_to_star", "2.0"))
    if rate <= 0:
        rate = 2.0
    stars_to_credit = int(amount_rub / rate)

    file = freq.files.get("receipt")
    if not file or not file.filename:
        return jsonify(ok=False, error="Пожалуйста, прикрепите скриншот чека"), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(ok=False, error="Разрешены только изображения (PNG, JPG, WEBP) или PDF"), 400

    filename = f"receipt_{user['id']}_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    file_path = UPLOAD_DIR / filename
    file.save(file_path)

    req_id = db.create_deposit_request(
        tid=user["id"],
        amount_rub=amount_rub,
        stars=stars_to_credit,
        receipt_file=filename
    )

    # Уведомляем администратора в Telegram, если бот активен
    notify_admin_new_deposit(req_id, user, amount_rub, stars_to_credit, filename)

    return jsonify(
        ok=True,
        request_id=req_id,
        amount_rub=amount_rub,
        stars=stars_to_credit,
        status="pending",
        message="Заявка отправлена! Администратор проверит поступление средств и начислит звёзды."
    )


# ── Helper: Notify Admin in TG ───────────────────────────────
def notify_admin_new_deposit(req_id: int, user: dict, amount_rub: int, stars: int, receipt_file: str):
    if not OWNER_ID or not bot_app:
        return

    async def _send():
        try:
            uname = f"@{user.get('username')}" if user.get('username') else user.get('first_name', '')
            text = (
                f"🔔 <b>Новая заявка на пополнение #{req_id}</b>\n\n"
                f"👤 Игрок: {uname} (ID: <code>{user['id']}</code>)\n"
                f"💰 Сумма: <b>{amount_rub} ₽</b>\n"
                f"⭐ Начисление: <b>{stars} звёзд</b>\n"
                f"🧾 Чек сохранён.\n\n"
                f"Откройте админ-панель в боте для проверки чека и подтверждения!"
            )
            await bot_app.bot.send_message(
                chat_id=OWNER_ID,
                text=text,
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error("Failed to notify admin: %s", e)

    if bot_loop:
        asyncio.run_coroutine_threadsafe(_send(), bot_loop)


def notify_user_deposit_result(user_id: int, req_id: int, status: str, stars: int):
    if not bot_app:
        return

    async def _send():
        try:
            if status == "approved":
                text = (
                    f"✅ <b>Пополнение успешно подтверждено!</b>\n\n"
                    f"Заявка #{req_id} одобрена.\n"
                    f"На ваш баланс зачислено: <b>+{stars} ⭐</b>!\n\n"
                    f"Откройте приложение — звёзды уже на балансе."
                )
            else:
                text = (
                    f"❌ <b>Заявка на пополнение #{req_id} отклонена.</b>\n\n"
                    f"Платёж не был найден или чек не соответствует оплате. "
                    f"Если произошла ошибка, напишите в поддержку @merertd."
                )
            await bot_app.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error("Failed to notify user %d: %s", user_id, e)

    if bot_loop:
        asyncio.run_coroutine_threadsafe(_send(), bot_loop)


# ── Admin Panel API Endpoints ────────────────────────────────
@web.post("/api/admin/requests")
def api_admin_requests():
    user = get_current_user_from_req(freq)
    if not user or not is_admin(user["id"]):
        return jsonify(ok=False, error="forbidden"), 403

    status_filter = freq.json.get("status") if freq.is_json else None
    reqs = db.get_all_deposit_requests(status_filter)
    users = db.get_all_users()
    settings = db.get_all_settings()

    return jsonify(
        ok=True,
        requests=reqs,
        users_count=len(users),
        users=users[:50],  # топ пользователей
        settings=settings,
    )


@web.post("/api/admin/process")
def api_admin_process():
    user = get_current_user_from_req(freq)
    if not user or not is_admin(user["id"]):
        return jsonify(ok=False, error="forbidden"), 403

    body = freq.get_json(silent=True) or {}
    req_id = int(body.get("request_id", 0))
    action = body.get("action", "")  # 'approve' or 'reject'

    success, msg, req_data = db.process_deposit_request(req_id, action, user["id"])
    if not success:
        return jsonify(ok=False, error=msg), 400

    # Оповещаем пользователя в Telegram
    notify_user_deposit_result(
        user_id=req_data["telegram_id"],
        req_id=req_id,
        status=req_data["status"],
        stars=req_data["stars"]
    )

    return jsonify(ok=True, message=msg, request=req_data)


@web.post("/api/admin/settings")
def api_admin_settings():
    user = get_current_user_from_req(freq)
    if not user or not is_admin(user["id"]):
        return jsonify(ok=False, error="forbidden"), 403

    body = freq.get_json(silent=True) or {}
    if "card_number" in body:
        db.set_setting("card_number", str(body["card_number"]).strip())
    if "card_holder" in body:
        db.set_setting("card_holder", str(body["card_holder"]).strip())
    if "card_bank" in body:
        db.set_setting("card_bank", str(body["card_bank"]).strip())
    if "rate_rub_to_star" in body:
        db.set_setting("rate_rub_to_star", str(float(body["rate_rub_to_star"])))

    return jsonify(ok=True, settings=db.get_all_settings())


# ── Web Server Runner ─────────────────────────────────────────
def run_web():
    logging.info("Starting Web Server on port %d...", PORT)
    web.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ── Telegram Bot Handlers ─────────────────────────────────────
def get_main_keyboard():
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append([
            InlineKeyboardButton("🎮 Открыть LOGO COINS", web_app=WebAppInfo(url=WEBAPP_URL))
        ])
    else:
        rows.append([
            InlineKeyboardButton("🎮 Запустить игру", url="https://t.me")
        ])
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg:
        return

    db.get_or_create(user.id, user.first_name or "", user.username or "")

    if WEBAPP_URL.startswith("https://"):
        try:
            await ctx.bot.set_chat_menu_button(
                chat_id=user.id,
                menu_button=MenuButtonWebApp(
                    text="🎮 ИГРАТЬ",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            )
        except Exception as e:
            logging.warning("Menu button error: %s", e)

    welcome_text = (
        f"Привет, {user.first_name}! 🚀\n\n"
        f"Добро пожаловать в <b>LOGO COINS</b> — обновлённый игровой портал в стиле RubUP!\n\n"
        f"🔥 <b>Игры:</b> Апгрейд, Ракета, Шторм, Дровосек\n"
        f"📦 <b>Кейсы:</b> Вайс-Сити, Розыск, Сейф и другие\n"
        f"💳 <b>Пополнение:</b> Перевод на карту с гарантированной проверкой чека\n"
        f"🎁 <b>Заработок & Бонусы:</b> Бесплатные звёзды, подарки и промокоды\n\n"
        f"Нажми кнопку ниже, чтобы начать играть!"
    )

    await msg.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )


def main():
    global bot_app, bot_loop

    threading.Thread(target=run_web, daemon=True).start()

    if not TOKEN or TOKEN == "your_bot_token_here":
        logging.warning("BOT_TOKEN is not set. Running web server only.")
        threading.Event().wait()
        return

    logging.info("Starting Telegram Bot...")
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", cmd_start))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_loop = loop

    bot_app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
