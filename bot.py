"""LOGO COINS — Telegram Mini App с реальными платежами (Telegram Stars).

Flask отдаёт web/, бот обрабатывает /start и платежи.
API endpoints для Mini App:
  POST /api/user           — получить баланс (initData)
  POST /api/save           — сохранить состояние
  POST /api/create-invoice — создать счёт Telegram Stars
"""

import os
import json
import hmac
import hashlib
import logging
import threading
import time
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_from_directory, jsonify, request as freq
import httpx
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
    PreCheckoutQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
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


# ── Telegram initData validation ─────────────────────────────
def validate_init_data(raw: str) -> dict | None:
    """Проверяет подпись Telegram WebApp initData.
    Возвращает dict пользователя или None.
    """
    if not raw or not TOKEN:
        return None
    try:
        parsed = dict(
            urllib.parse.parse_qsl(raw, keep_blank_values=True)
        )
        recv_hash = parsed.pop("hash", "")
        if not recv_hash:
            return None

        check_str = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )
        secret = hmac.new(
            b"WebAppData", TOKEN.encode(), hashlib.sha256
        ).digest()
        calc = hmac.new(
            secret, check_str.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calc, recv_hash):
            return None

        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return None

        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


# ── Flask ─────────────────────────────────────────────────────
web = Flask(__name__, static_folder=None)


@web.get("/")
def home():
    return send_from_directory(WEB_DIR, "index.html")


@web.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@web.get("/health")
def health():
    return jsonify(status="ok", app="LOGO COINS")


# ── API: получить данные пользователя ────────────────────────
@web.post("/api/user")
def api_user():
    body = freq.get_json(silent=True) or {}
    user = validate_init_data(body.get("init_data", ""))
    if not user or not user.get("id"):
        return jsonify(ok=False, error="auth"), 401

    data = db.get_or_create(
        user["id"], user.get("first_name", ""), user.get("username", "")
    )
    return jsonify(
        ok=True,
        user={
            "coins": data["coins"],
            "stars": data["stars"],
            "deposited": data["deposited"],
            "last_free": data["last_free"],
            "rank_claims": data["rank_claims"],
            "inventory": data["inventory"],
            "promos": data["promos"],
        },
    )


# ── API: сохранить состояние ─────────────────────────────────
@web.post("/api/save")
def api_save():
    body = freq.get_json(silent=True) or {}
    user = validate_init_data(body.get("init_data", ""))
    if not user or not user.get("id"):
        return jsonify(ok=False, error="auth"), 401

    state = body.get("state", {})
    current = db.get_or_create(user["id"])

    # Дельта звёзд: отрицательная = потратил, положительная = выиграл в игре
    stars_delta = int(state.get("stars_delta", 0))
    # Анти-чит: разрешаем выиграть макс. 5000 звёзд за одну синхронизацию
    if stars_delta > 5000:
        stars_delta = 0
    new_stars = max(0, current["stars"] + stars_delta)

    safe = {
        "coins": max(0, int(state.get("coins", 0))),
        "stars": new_stars,
        "deposited": current["deposited"],       # только через платежи!
        "last_free": int(state.get("last_free", 0)),
        "rank_claims": state.get("rank_claims", {}),
        "inventory": state.get("inventory", []),
        "promos": state.get("promos", {}),
    }
    db.save_state(user["id"], safe)
    return jsonify(ok=True, stars=new_stars, deposited=current["deposited"])


# ── API: создать счёт Telegram Stars ─────────────────────────
@web.post("/api/create-invoice")
def api_create_invoice():
    body = freq.get_json(silent=True) or {}
    user = validate_init_data(body.get("init_data", ""))
    if not user or not user.get("id"):
        return jsonify(ok=False, error="auth"), 401

    amount = int(body.get("amount", 0))
    if amount < 1 or amount > 10000:
        return jsonify(ok=False, error="invalid amount"), 400

    payload = json.dumps(
        {
            "a": "topup",
            "uid": user["id"],
            "s": amount,
            "t": int(time.time()),
        }
    )

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{TOKEN}/createInvoiceLink",
                json={
                    "title": f"Пополнение {amount} ⭐",
                    "description": (
                        f"Начисление {amount} звёзд в LOGO COINS"
                    ),
                    "payload": payload,
                    "currency": "XTR",
                    "prices": [
                        {"label": f"{amount} Stars", "amount": amount}
                    ],
                },
            )
        result = resp.json()
        if result.get("ok"):
            return jsonify(ok=True, url=result["result"])
        logging.error("Invoice API error: %s", result)
        return (
            jsonify(
                ok=False,
                error=result.get("description", "API error"),
            ),
            502,
        )
    except Exception as e:
        logging.error("Invoice creation error: %s", e)
        return jsonify(ok=False, error="service unavailable"), 503


def run_web():
    logging.info("Web server starting on port %d …", PORT)
    web.run(
        host="0.0.0.0", port=PORT, debug=False, use_reloader=False
    )


# ── Telegram Bot handlers ────────────────────────────────────
def get_main_keyboard():
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append(
            [
                InlineKeyboardButton(
                    "🎮 Открыть LOGO COINS",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "🎮 Запустить игру (Demo)", url="https://t.me"
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


async def cmd_start(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
):
    msg = update.effective_message
    user = update.effective_user
    if not msg:
        return

    db.get_or_create(
        user.id, user.first_name or "", user.username or ""
    )

    if WEBAPP_URL.startswith("https://"):
        try:
            await ctx.bot.set_chat_menu_button(
                chat_id=user.id,
                menu_button=MenuButtonWebApp(
                    text="🎮 ИГРАТЬ",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            )
        except Exception as e:
            logging.warning("Menu button error: %s", e)

    await msg.reply_text(
        f"Привет, {user.first_name}!\n\n"
        "Это *LOGO COINS* — мини-приложение с играми "
        "и реальной оплатой через Telegram Stars ⭐\n\n"
        "🪓 Дровосек — бесплатно зарабатывай LOGO\n"
        "⚡ Апгрейд — умножай ставку на колесе\n"
        "🚀 Ракета — успей забрать выигрыш до взрыва\n"
        "📦 Кейсы — открываются за звёзды\n\n"
        "Звёзды покупаются за реальные Telegram Stars.\n"
        f"Поддержка: @merertd\n\n"
        "Нажми кнопку ниже или «ИГРАТЬ» слева внизу.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


async def pre_checkout(
    update: Update, _ctx: ContextTypes.DEFAULT_TYPE
):
    """Автоматически подтверждаем все pre-checkout запросы."""
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(
    update: Update, _ctx: ContextTypes.DEFAULT_TYPE
):
    """Обработка успешного платежа Telegram Stars."""
    payment = update.message.successful_payment
    user = update.effective_user
    amount = payment.total_amount

    db.get_or_create(
        user.id, user.first_name or "", user.username or ""
    )
    db.add_stars(user.id, amount)
    db.log_payment(
        user.id,
        amount,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
    )

    logging.info(
        "💰 Payment: user=%d  amount=%d  charge=%s",
        user.id,
        amount,
        payment.telegram_payment_charge_id,
    )

    await update.message.reply_text(
        f"✅ Оплата прошла успешно!\n\n"
        f"Начислено: *{amount}* ⭐\n"
        f"Откройте игру — баланс уже обновлён.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )


# ── Main ──────────────────────────────────────────────────────
def main():
    threading.Thread(target=run_web, daemon=True).start()

    if not TOKEN or TOKEN == "your_bot_token_here":
        logging.warning(
            "BOT_TOKEN not set — web-server-only mode"
        )
        threading.Event().wait()
        return

    logging.info("Starting Telegram Bot …")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT, successful_payment
        )
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
