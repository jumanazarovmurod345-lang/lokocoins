"""LokoCoins Bot - Complete Redesign
Backend with Flask, WebApp API, Games (Upgrade, Rocket Crash, Tap Leaderboard), 
Cases with NFT chances, Profile & Inventory, Statuses, and Card (UZS) / Star Gift Top-ups.
"""

import os
import json
import random
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
from flask import Flask, send_from_directory, jsonify, request as freq
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
    CallbackQueryHandler,
    ContextTypes,
)

import database as db

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config ──────────────────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else 7206933215
BOT_USERNAME = os.getenv("BOT_USERNAME", "LokoCoins_Bot").strip().lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

bot_app: Application = None
bot_loop = None

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

# ── Telegram initData validation ────────────────────────────────────────
def validate_init_data(raw: str) -> dict | None:
    if not raw:
        return None
    if not TOKEN or TOKEN == "ваш_токен_от_BotFather":
        # Dev / browser testing demo user
        return {"id": OWNER_ID or 7206933215, "first_name": "Игрок Loko", "username": "lokoplayer"}
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
        if time.time() - auth_date > 86400 * 7:
            return None

        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def get_current_user_from_req(req) -> dict | None:
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

    # Fallback for browser direct testing
    return {"id": OWNER_ID or 7206933215, "first_name": "Игрок", "username": "lokoplayer"}


def is_admin(user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    return False


# ── Flask Web App ────────────────────────────────────────────────────────
web = Flask(__name__, static_folder=None)
web.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


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
    return jsonify(status="ok", app="LokoCoins", version="2.0")


# ── API: User state & settings ──────────────────────────────────────────
@web.post("/api/user")
def api_user():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    data = db.get_or_create(user["id"], user.get("first_name", ""), user.get("username", ""))
    # Auto-check status tier and award bonuses
    status_info = db.check_and_update_status(user["id"])
    if status_info and status_info.get("user"):
        data = status_info["user"]

    settings = db.get_all_settings()
    is_adm = is_admin(user["id"])
    my_requests = db.get_user_deposit_requests(user["id"])
    tap_leaderboard = db.get_tap_leaderboard()

    return jsonify(
        ok=True,
        user={
            "id": user["id"],
            "first_name": data["first_name"],
            "username": data["username"],
            "coins": data["coins"],
            "stars": data["stars"],
            "deposited": data["deposited"],
            "uzs_deposited": data.get("uzs_deposited", 0),
            "status": data.get("status", "Гость"),
            "claimed_statuses": data.get("claimed_statuses", []),
            "tap_score": data.get("tap_score", 0),
            "tap_best": data.get("tap_best", 0),
            "referrer_id": data.get("referrer_id", 0),
            "referral_count": data.get("referral_count", 0),
            "last_free": data.get("last_free", 0),
            "inventory": data.get("inventory", []),
            "promos": data.get("promos", []),
            "is_admin": is_adm,
            "bot_username": BOT_USERNAME,
        },
        settings={
            "card_number": settings.get("card_number", "5614 6812 5962 4009"),
            "card_holder": settings.get("card_holder", "Nafisa Xalimova"),
            "card_bank": settings.get("card_bank", "Uzcard / Humo"),
            "rate_uzs_to_star": int(settings.get("rate_uzs_to_star", "2000")),
            "min_uzs": int(settings.get("min_uzs", "13000")),
            "min_stars": int(settings.get("min_stars", "15")),
            "admin_contact": settings.get("admin_contact", "merertd"),
        },
        deposit_requests=my_requests,
        leaderboard=tap_leaderboard.get("leaders", []),
    )


# ── API: Game 1 - Upgrade (Колесо Апгрейда) ──────────────────────────────
@web.post("/api/upgrade/play")
def api_upgrade_play():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    body = freq.get_json(silent=True) or {}
    bet = int(body.get("bet", 0))
    currency = body.get("currency", "stars") # "stars" or "coins"
    multiplier = float(body.get("multiplier", 2.0))

    if bet <= 0:
        return jsonify(ok=False, error="Некорректная сумма ставки"), 400
    if multiplier not in [1.5, 2.0, 3.0, 5.0, 10.0, 20.0]:
        return jsonify(ok=False, error="Некорректный множитель"), 400

    u = db.get_user(user["id"])
    bal = u["stars"] if currency == "stars" else u["coins"]
    if bal < bet:
        return jsonify(ok=False, error="Недостаточно средств на балансе"), 400

    # Win rate formula: standard exciting casino upgrade RTP ~ 90%
    win_chance = 0.90 / multiplier
    is_win = random.random() < win_chance

    # Violet win zone takes `sector_size = (win_chance * 360)` degrees, centered from 0 to sector_size
    sector_size = win_chance * 360.0
    if is_win:
        # Stop inside the violet winning zone [2, sector_size - 2]
        angle = random.uniform(2.0, max(2.1, sector_size - 2.0))
        win_amount = int(bet * multiplier)
    else:
        # Stop inside the losing zone [sector_size + 2, 358.0]
        angle = random.uniform(min(357.9, sector_size + 2.0), 358.0)
        win_amount = 0

    delta = win_amount - bet
    if currency == "stars":
        new_bal = max(0, u["stars"] + delta)
        u["stars"] = new_bal
    else:
        new_bal = max(0, u["coins"] + delta)
        u["coins"] = new_bal

    db.save_state(user["id"], u)

    return jsonify(
        ok=True,
        won=is_win,
        angle=round(angle, 2),
        sector_size=round(sector_size, 2),
        win_amount=win_amount,
        new_balance=new_bal,
        currency=currency,
    )


# ── API: Game 2 - Rocket Crash (Ракета) ──────────────────────────────────
@web.post("/api/rocket/play")
def api_rocket_play():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    body = freq.get_json(silent=True) or {}
    bet = int(body.get("bet", 0))
    currency = body.get("currency", "stars")
    cashout_at = float(body.get("cashout_at", 0)) # 0 if manual or auto

    if bet <= 0:
        return jsonify(ok=False, error="Укажите ставку"), 400

    u = db.get_user(user["id"])
    bal = u["stars"] if currency == "stars" else u["coins"]
    if bal < bet:
        return jsonify(ok=False, error="Недостаточно средств"), 400

    # Crash point generator (standard provably fair curve, 95% RTP)
    r = random.random()
    if r < 0.04:
        crash_point = 1.00 # Instant bust
    else:
        crash_point = round(max(1.01, 0.96 / (1.0 - r)), 2)
        if crash_point > 100.0:
            crash_point = round(random.uniform(50.0, 100.0), 2)

    # Check if user cashed out
    won = False
    win_amount = 0
    if 1.0 < cashout_at <= crash_point:
        won = True
        win_amount = int(bet * cashout_at)

    delta = win_amount - bet
    if currency == "stars":
        new_bal = max(0, u["stars"] + delta)
        u["stars"] = new_bal
    else:
        new_bal = max(0, u["coins"] + delta)
        u["coins"] = new_bal

    db.save_state(user["id"], u)

    return jsonify(
        ok=True,
        crash_point=crash_point,
        won=won,
        win_amount=win_amount,
        new_balance=new_bal,
    )


# ── API: Game 3 - Tap Game & Leaderboard (Тапалка) ───────────────────────
@web.post("/api/tap/submit")
def api_tap_submit():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    body = freq.get_json(silent=True) or {}
    score = int(body.get("score", 0))
    if score < 0 or score > 100000:
        return jsonify(ok=False, error="Недопустимый счет"), 400

    leaderboard = db.submit_tap_score(user["id"], score)
    u = db.get_user(user["id"])

    return jsonify(
        ok=True,
        score=score,
        tap_best=u.get("tap_best", score),
        leaderboard=leaderboard.get("leaders", []),
    )


# ── API: Cases (4 Кейса с NFT шансами) ──────────────────────────────────
CASE_CONFIG = {
    "beach": {
        "name": "Пляжный кейс",
        "price": 15,
        "desc": "Почти всегда падает редкий Пляжный Мишка + LokoCoins",
        "items": [
            {"id": "bear_beach", "name": "Пляжный Мишка", "icon": "🧸", "rarity": "Редкий", "is_nft": False, "stars_val": 15, "weight": 85},
            {"id": "bear_golden", "name": "Золотой Мишка", "icon": "👑🧸", "rarity": "Эпический", "is_nft": False, "stars_val": 30, "weight": 12},
            {"id": "bear_cyber", "name": "Кибер-Мишка", "icon": "🤖🧸", "rarity": "Легендарный", "is_nft": False, "stars_val": 75, "weight": 3},
        ],
        "coins_min": 150,
        "coins_max": 500,
    },
    "gift": {
        "name": "Подарочный кейс",
        "price": 20,
        "desc": "Официальные Telegram-подарки от 15 звёзд + LokoCoins",
        "items": [
            {"id": "gift_cake", "name": "Праздничный Торт", "icon": "🎂", "rarity": "Обычный", "is_nft": False, "stars_val": 15, "weight": 55},
            {"id": "gift_box", "name": "Подарочный Бокс", "icon": "🎁", "rarity": "Редкий", "is_nft": False, "stars_val": 25, "weight": 30},
            {"id": "gift_heart", "name": "Звёздное Сердце", "icon": "💖", "rarity": "Эпический", "is_nft": False, "stars_val": 50, "weight": 12},
            {"id": "gift_crown", "name": "Императорская Корона", "icon": "👑", "rarity": "Легендарный", "is_nft": False, "stars_val": 100, "weight": 3},
        ],
        "coins_min": 300,
        "coins_max": 800,
    },
    "rare": {
        "name": "Редкий кейс",
        "price": 25,
        "desc": "Telegram-подарки + шанс 0.5% на эксклюзивный NFT-подарок!",
        "nft_chance": 0.005, # 0.5% шанс на NFT
        "nft_item": {"id": "nft_rare_voucher", "name": "Telegram NFT Gift #777", "icon": "💎⚡", "rarity": "NFT", "is_nft": True, "stars_val": 250},
        "items": [
            {"id": "rare_gem", "name": "Сияющий Рубин", "icon": "💎", "rarity": "Редкий", "is_nft": False, "stars_val": 25, "weight": 55},
            {"id": "rare_rocket", "name": "Золотая Ракета", "icon": "🚀", "rarity": "Эпический", "is_nft": False, "stars_val": 45, "weight": 35},
            {"id": "rare_phoenix", "name": "Огненный Феникс", "icon": "🔥🦅", "rarity": "Легендарный", "is_nft": False, "stars_val": 80, "weight": 10},
        ],
        "coins_min": 500,
        "coins_max": 1200,
    },
    "elite": {
        "name": "Элитный кейс",
        "price": 50,
        "desc": "Премиальные Telegram-подарки + шанс 1.0% на редчайший NFT!",
        "nft_chance": 0.010, # 1% шанс на NFT
        "nft_item": {"id": "nft_elite_trophy", "name": "Loko Masterpiece NFT #001", "icon": "🏆✨", "rarity": "NFT", "is_nft": True, "stars_val": 500},
        "items": [
            {"id": "elite_trophy", "name": "Кубок Победителя", "icon": "🏆", "rarity": "Эпический", "is_nft": False, "stars_val": 50, "weight": 50},
            {"id": "elite_car", "name": "Спорткар Киберпанк", "icon": "🏎️", "rarity": "Легендарный", "is_nft": False, "stars_val": 120, "weight": 40},
            {"id": "elite_star", "name": "Сверхновая Звезда", "icon": "🌟", "rarity": "Мифический", "is_nft": False, "stars_val": 250, "weight": 10},
        ],
        "coins_min": 1000,
        "coins_max": 3000,
    },
}


@web.post("/api/case/open")
def api_case_open():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    body = freq.get_json(silent=True) or {}
    case_id = body.get("case_id", "")
    if case_id not in CASE_CONFIG:
        return jsonify(ok=False, error="Кейс не найден"), 404

    cfg = CASE_CONFIG[case_id]
    u = db.get_user(user["id"])

    if u["stars"] < cfg["price"]:
        return jsonify(ok=False, error=f"Недостаточно звёзд. Цена: {cfg['price']} ⭐"), 400

    # Check NFT roll if applicable
    won_item = None
    nft_roll = random.random()
    if cfg.get("nft_chance") and nft_roll < cfg["nft_chance"]:
        won_item = dict(cfg["nft_item"])
    else:
        # Weighted item choice
        items = cfg["items"]
        weights = [it.get("weight", 1) for it in items]
        chosen = random.choices(items, weights=weights, k=1)[0]
        won_item = dict(chosen)

    bonus_coins = random.randint(cfg.get("coins_min", 100), cfg.get("coins_max", 500))

    # Deduct stars, add bonus coins, save item to user's inventory
    u["stars"] -= cfg["price"]
    u["coins"] += bonus_coins
    db.save_state(user["id"], u)
    db.add_inventory_item(user["id"], won_item)

    # Build roulette reel items for animation
    reel = []
    pool = list(cfg["items"])
    if cfg.get("nft_item"):
        pool.append(cfg["nft_item"])
    for _ in range(40):
        reel.append(random.choice(pool))
    reel.append(won_item)
    for _ in range(5):
        reel.append(random.choice(pool))

    return jsonify(
        ok=True,
        item=won_item,
        bonus_coins=bonus_coins,
        new_stars=u["stars"],
        new_coins=u["coins"],
        reel=reel,
        stop_index=40,
    )


# ── API: Promo codes ────────────────────────────────────────────────────
PROMO_CODES = {
    "LOKO10": {"stars": 10, "coins": 500, "text": "+10 ⭐ и +500 🪙"},
    "BONUS": {"stars": 5, "coins": 300, "text": "+5 ⭐ и +300 🪙"},
    "START": {"stars": 0, "coins": 1000, "text": "+1000 🪙"},
    "ELITE": {"stars": 25, "coins": 1000, "text": "+25 ⭐ и +1000 🪙"},
}


@web.post("/api/promo/apply")
def api_promo_apply():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    body = freq.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip().toUpperCase() if hasattr(str, 'toUpperCase') else str(body.get("code", "")).strip().upper()
    if not code:
        return jsonify(ok=False, error="Введите промокод"), 400

    u = db.get_user(user["id"])
    promos = u.get("promos", {})
    if isinstance(promos, list):
        promos = {k: True for k in promos}

    if code in promos:
        return jsonify(ok=False, error="Вы уже активировали этот промокод"), 400

    if code not in PROMO_CODES:
        return jsonify(ok=False, error="Неверный или устаревший промокод"), 400

    reward = PROMO_CODES[code]
    u["stars"] += reward["stars"]
    u["coins"] += reward["coins"]
    promos[code] = int(time.time())
    u["promos"] = promos
    db.save_state(user["id"], u)

    return jsonify(
        ok=True,
        message=f"Промокод активирован! {reward['text']}",
        stars=u["stars"],
        coins=u["coins"],
    )


# ── API: Create Deposit Request (Card UZS or TG Gift) ───────────────────
@web.post("/api/deposit/create")
def api_deposit_create():
    user = get_current_user_from_req(freq)
    if not user:
        return jsonify(ok=False, error="auth_required"), 401

    method = freq.form.get("method", "card") # 'card' or 'gift'

    if method == "gift":
        # Telegram gift method
        try:
            stars = int(freq.form.get("stars", 15))
        except ValueError:
            stars = 15

        if stars < 15:
            return jsonify(ok=False, error="Минимальный подарок — от 15 звёзд"), 400

        req_id = db.create_deposit_request(
            tid=user["id"],
            amount_uzs=0,
            stars=stars,
            receipt_file="TELEGRAM_GIFT",
            method="gift"
        )
        notify_admin_new_deposit(req_id, user, 0, stars, "Подарок в Telegram", method="gift")

        return jsonify(
            ok=True,
            request_id=req_id,
            method="gift",
            stars=stars,
            message="Заявка создана! Отправьте подарок администратору в Telegram.",
        )

    # Card payment in UZS
    try:
        amount_uzs = int(freq.form.get("amount_uzs", 0))
    except ValueError:
        return jsonify(ok=False, error="Некорректная сумма в сумах"), 400

    if amount_uzs < 13000:
        return jsonify(ok=False, error="Минимальное пополнение — 13 000 сум (или от 15 звёзд)"), 400
    if amount_uzs > 50000000:
        return jsonify(ok=False, error="Превышен лимит пополнения"), 400

    rate = int(db.get_setting("rate_uzs_to_star", "2000"))
    if rate <= 0:
        rate = 2000
    stars_to_credit = int(amount_uzs / rate)

    file = freq.files.get("receipt")
    if not file or not file.filename:
        return jsonify(ok=False, error="Пожалуйста, прикрепите чек перевода"), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(ok=False, error="Разрешены форматы: JPG, PNG, WEBP, PDF"), 400

    filename = f"receipt_{user['id']}_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    file_path = UPLOAD_DIR / filename
    file.save(file_path)

    req_id = db.create_deposit_request(
        tid=user["id"],
        amount_uzs=amount_uzs,
        stars=stars_to_credit,
        receipt_file=filename,
        method="card"
    )

    notify_admin_new_deposit(req_id, user, amount_uzs, stars_to_credit, filename, method="card")

    return jsonify(
        ok=True,
        request_id=req_id,
        amount_uzs=amount_uzs,
        stars=stars_to_credit,
        status="pending",
        message="Чек отправлен! Администратор проверит платёж и начислит звёзды."
    )


# ── Notification Helpers ────────────────────────────────────────────────
def notify_admin_new_deposit(req_id: int, user: dict, amount_uzs: int, stars: int, receipt_file: str, method: str = "card"):
    if not OWNER_ID or not bot_app:
        return

    async def _send():
        try:
            uname = f"@{user.get('username')}" if user.get('username') else user.get('first_name', '')
            if method == "gift":
                text = (
                    f"🎁 <b>Заявка на пополнение звёздами (TG-Подарок) #{req_id}</b>\n\n"
                    f"👤 Игрок: {uname} (ID: <code>{user['id']}</code>)\n"
                    f"⭐ Ожидаемый подарок: <b>{stars} звёзд</b>\n\n"
                    f"Проверьте входящий подарок в вашем Telegram и начислите баланс в админ-панели!"
                )
            else:
                text = (
                    f"🔔 <b>Новая заявка на пополнение картой #{req_id}</b>\n\n"
                    f"👤 Игрок: {uname} (ID: <code>{user['id']}</code>)\n"
                    f"💳 Сумма: <b>{amount_uzs:,} сум</b>\n"
                    f"⭐ К зачислению: <b>{stars} звёзд</b>\n"
                    f"🧾 Чек прикреплён.\n\n"
                    f"Откройте админ-панель для проверки чека и зачисления."
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
                    f"Заявка #{req_id} одобрена администратором.\n"
                    f"Вам начислено: <b>+{stars} ⭐</b>!\n\n"
                    f"Звёзды уже на вашем балансе в приложении."
                )
            else:
                text = (
                    f"❌ <b>Заявка на пополнение #{req_id} отклонена.</b>\n\n"
                    f"Платёж не был найден или чек не подтверждён. "
                    f"Если произошла ошибка, напишите администратору @merertd."
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


# ── Admin Panel API Endpoints ───────────────────────────────────────────
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
        users=users[:50],
        settings=settings,
    )


@web.post("/api/admin/process")
def api_admin_process():
    user = get_current_user_from_req(freq)
    if not user or not is_admin(user["id"]):
        return jsonify(ok=False, error="forbidden"), 403

    body = freq.get_json(silent=True) or {}
    req_id = int(body.get("request_id", 0))
    action = body.get("action", "")

    success, msg, req_data = db.process_deposit_request(req_id, action, user["id"])
    if not success:
        return jsonify(ok=False, error=msg), 400

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
    for key in ["card_number", "card_holder", "card_bank", "rate_uzs_to_star", "min_uzs", "min_stars", "admin_contact"]:
        if key in body:
            db.set_setting(key, str(body[key]).strip())

    return jsonify(ok=True, settings=db.get_all_settings())


# ── Web Server Runner ───────────────────────────────────────────────────
def run_web():
    logging.info("Starting Web Server on port %d...", PORT)
    web.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ── Telegram Bot Handlers ───────────────────────────────────────────────
def get_main_keyboard():
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append([
            InlineKeyboardButton("🚀 Открыть LokoCoins", web_app=WebAppInfo(url=WEBAPP_URL))
        ])
    else:
        rows.append([
            InlineKeyboardButton("🚀 Открыть LokoCoins", url="https://t.me")
        ])
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg:
        return

    # Check referral code from /start ref_12345
    ref_id = 0
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref_id = int(ctx.args[0].replace("ref_", ""))
        except ValueError:
            ref_id = 0

    db.get_or_create(user.id, user.first_name or "", user.username or "", referrer_id=ref_id)

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
        f"Добро пожаловать в <b>LokoCoins</b> — премиальный игровой портал!\n\n"
        f"🎮 <b>Игры:</b> Апгрейд (с колесом), Ракета (Crash), Тапалка с призами\n"
        f"📦 <b>Кейсы:</b> Пляжный, Подарочный, Редкий и Элитный с NFT!\n"
        f"💳 <b>Пополнение:</b> Карта Uzcard/Humo (2000 сум = 1 ⭐) или Telegram-подарки\n"
        f"👤 <b>Профиль:</b> Инвентарь, Статусы (Гость, Местный, Крутой, Элита) и рефералы\n\n"
        f"Нажми кнопку ниже, чтобы начать игру!"
    )

    await msg.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )


def main():
    global bot_app, bot_loop

    threading.Thread(target=run_web, daemon=True).start()

    if not TOKEN or TOKEN == "ваш_токен_от_BotFather":
        logging.warning("BOT_TOKEN is not configured. Running web server only.")
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
