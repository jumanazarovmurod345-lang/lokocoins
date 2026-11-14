import os
import logging
import threading
from pathlib import Path

from flask import Flask, send_from_directory, jsonify, request
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

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Load configuration
TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else None
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

# Flask Server for Mini App
web_server = Flask(__name__, static_folder=None)

@web_server.get("/")
def home():
    return send_from_directory(WEB_DIR, "index.html")

@web_server.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)

@web_server.get("/health")
def health():
    return jsonify({"status": "ok", "app": "LOGO COINS"})

def run_web_server():
    logging.info(f"Starting Web Server on port {PORT}...")
    web_server.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def get_main_keyboard():
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append([
            InlineKeyboardButton(
                "🎮 Открыть LOGO COINS",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                "🎮 Запустить игру (Demo)",
                url="https://t.me"
            )
        ])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message:
        return

    # Set Telegram Menu Button (web app in bottom left corner)
    if WEBAPP_URL.startswith("https://"):
        try:
            await context.bot.set_chat_menu_button(
                chat_id=user.id,
                menu_button=MenuButtonWebApp(
                    text="🎮 ИГРАТЬ",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            )
        except Exception as e:
            logging.warning(f"Could not set menu button: {e}")

    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"Добро пожаловать в **LOGO COINS** — флагманскую игровую платформу в Telegram!\n\n"
        f"🪙 **LogoCoin & Telegram Stars ⭐** — играй и умножай баланс!\n"
        f"🪓 **Chop Master v2** — срубай вековые деревья топором и копи комбо!\n"
        f"⚡ **Апгрейдер** — ставь монеты, звёзды или подарки на колесо фортуны!\n"
        f"🚀 **Ракета (Crash)** — следи за множителем и успей забрать выигрыш!\n"
        f"🎁 **Кейсы с рулеткой** — крути барабан за звёзды и бесплатный кейс каждые 24ч!\n\n"
        f"Нажми кнопку ниже или кнопку **«ИГРАТЬ»** в левом нижнем углу 👇"
    )

    await message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

def main():
    # Start web server in background thread
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()

    if not TOKEN or TOKEN == "your_bot_token_here":
        logging.warning("BOT_TOKEN is not set in .env! Web server is running, bot is waiting for token.")
        # Keep main thread alive for web server
        server_thread.join()
        return

    logging.info("Starting Telegram Bot...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
