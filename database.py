"""SQLite database for LokoCoins Bot
Balances, inventory, statuses, deposit requests, tap leaderboard, and settings.
"""

import sqlite3
import json
import os
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id       INTEGER PRIMARY KEY,
                first_name        TEXT    DEFAULT '',
                username          TEXT    DEFAULT '',
                coins             INTEGER DEFAULT 500,
                stars             INTEGER DEFAULT 0,
                deposited         INTEGER DEFAULT 0,
                uzs_deposited     INTEGER DEFAULT 0,
                status            TEXT    DEFAULT 'Гость',
                claimed_statuses  TEXT    DEFAULT '[]',
                tap_score         INTEGER DEFAULT 0,
                tap_best          INTEGER DEFAULT 0,
                referrer_id       INTEGER DEFAULT 0,
                referral_count    INTEGER DEFAULT 0,
                last_free         INTEGER DEFAULT 0,
                rank_claims       TEXT    DEFAULT '{}',
                inventory         TEXT    DEFAULT '[]',
                promos            TEXT    DEFAULT '{}',
                created_at        REAL    DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS deposit_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL,
                amount_uzs   INTEGER NOT NULL,
                stars        INTEGER NOT NULL,
                method       TEXT    DEFAULT 'card', -- 'card' or 'gift'
                receipt_file TEXT    DEFAULT '',
                status       TEXT    DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
                created_at   REAL    DEFAULT (strftime('%s','now')),
                processed_at REAL    DEFAULT NULL,
                admin_id     INTEGER DEFAULT NULL,
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id         INTEGER NOT NULL,
                amount              INTEGER NOT NULL,
                charge_id           TEXT,
                provider_charge_id  TEXT,
                created_at          REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            );
        """)

        # Add missing columns if database was created previously
        existing_cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        cols_to_add = [
            ("uzs_deposited", "INTEGER DEFAULT 0"),
            ("status", "TEXT DEFAULT 'Гость'"),
            ("claimed_statuses", "TEXT DEFAULT '[]'"),
            ("tap_score", "INTEGER DEFAULT 0"),
            ("tap_best", "INTEGER DEFAULT 0"),
            ("referrer_id", "INTEGER DEFAULT 0"),
            ("referral_count", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_def in cols_to_add:
            if col_name not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass

        dep_cols = [r["name"] for r in c.execute("PRAGMA table_info(deposit_requests)").fetchall()]
        if "amount_uzs" not in dep_cols and "amount_rub" in dep_cols:
            try:
                c.execute("ALTER TABLE deposit_requests ADD COLUMN amount_uzs INTEGER DEFAULT 0")
                c.execute("UPDATE deposit_requests SET amount_uzs = amount_rub * 2000 WHERE amount_uzs = 0")
            except Exception:
                pass
        if "method" not in dep_cols:
            try:
                c.execute("ALTER TABLE deposit_requests ADD COLUMN method TEXT DEFAULT 'card'")
            except Exception:
                pass

        # Official LokoCoins bank settings
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_number', '5614 6812 5962 4009')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_holder', 'Nafisa Xalimova')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_bank', 'Uzcard / Humo')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('rate_uzs_to_star', '2000')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_uzs', '13000')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_stars', '15')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_contact', 'merertd')")


def get_setting(key: str, default: str = "") -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with _conn() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_all_settings() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_user(tid: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE telegram_id=?", (tid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["rank_claims"] = json.loads(d.get("rank_claims") or "{}")
    d["inventory"] = json.loads(d.get("inventory") or "[]")
    d["promos"] = json.loads(d.get("promos") or "{}")
    d["claimed_statuses"] = json.loads(d.get("claimed_statuses") or "[]")
    return d


def get_or_create(tid: int, first_name: str = "", username: str = "", referrer_id: int = 0) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE telegram_id=?", (tid,)).fetchone()
        if not row:
            c.execute(
                """INSERT INTO users (telegram_id, first_name, username, referrer_id, coins, stars, status) 
                   VALUES (?, ?, ?, ?, 500, 0, 'Гость')""",
                (tid, first_name, username, referrer_id if referrer_id != tid else 0)
            )
            if referrer_id and referrer_id != tid:
                c.execute("UPDATE users SET referral_count = referral_count + 1 WHERE telegram_id=?", (referrer_id,))
        else:
            if first_name or username:
                c.execute(
                    "UPDATE users SET first_name=?, username=? WHERE telegram_id=?",
                    (first_name, username, tid)
                )
    return get_user(tid)


def get_all_users() -> list:
    with _conn() as c:
        rows = c.execute(
            """SELECT telegram_id, first_name, username, coins, stars, deposited, uzs_deposited, status, created_at 
               FROM users ORDER BY deposited DESC LIMIT 200"""
        ).fetchall()
    return [dict(r) for r in rows]


def save_state(tid: int, state: dict):
    with _conn() as c:
        c.execute(
            """UPDATE users SET
                coins=?, stars=?, last_free=?,
                rank_claims=?, inventory=?, promos=?
               WHERE telegram_id=?""",
            (
                int(state.get("coins", 0)),
                int(state.get("stars", 0)),
                int(state.get("last_free", 0)),
                json.dumps(state.get("rank_claims", {})),
                json.dumps(state.get("inventory", [])),
                json.dumps(state.get("promos", {})),
                tid,
            ),
        )


def add_inventory_item(tid: int, item: dict):
    user = get_user(tid)
    if not user:
        return
    inv = user.get("inventory", [])
    item["won_at"] = int(time.time())
    inv.insert(0, item)
    with _conn() as c:
        c.execute("UPDATE users SET inventory=? WHERE telegram_id=?", (json.dumps(inv), tid))


def check_and_update_status(tid: int) -> dict:
    """Updates user status based on deposited amount and awards status bonuses."""
    user = get_user(tid)
    if not user:
        return {}

    stars = user.get("deposited", 0)
    uzs = user.get("uzs_deposited", 0)
    claimed = set(user.get("claimed_statuses", []))

    # Determine status level
    # Гость — при первом входе.
    # Местный — если пополнил от 50 звёзд или эквивалент, получает 500 LokoCoins.
    # Крутой — если пополнил от 150 звёзд или 150000 сум, получает 25 звёзд на баланс.
    # Элита — если пополнил от 500 звёзд или 250000 сум, получает 250 звёзд + 1000 LokoCoins.

    current_status = "Гость"
    coins_bonus = 0
    stars_bonus = 0

    if stars >= 500 or uzs >= 250000:
        current_status = "Элита"
    elif stars >= 150 or uzs >= 150000:
        current_status = "Крутой"
    elif stars >= 50 or uzs >= 100000:
        current_status = "Местный"

    # Process bonuses sequentially for newly unlocked tiers
    unlocked_awards = []
    if (stars >= 50 or uzs >= 100000) and "Местный" not in claimed:
        coins_bonus += 500
        claimed.add("Местный")
        unlocked_awards.append("Местный (+500 🪙)")

    if (stars >= 150 or uzs >= 150000) and "Крутой" not in claimed:
        stars_bonus += 25
        claimed.add("Крутой")
        unlocked_awards.append("Крутой (+25 ⭐)")

    if (stars >= 500 or uzs >= 250000) and "Элита" not in claimed:
        stars_bonus += 250
        coins_bonus += 1000
        claimed.add("Элита")
        unlocked_awards.append("Элита (+250 ⭐, +1000 🪙)")

    with _conn() as c:
        c.execute(
            """UPDATE users SET 
               status=?, 
               claimed_statuses=?, 
               stars = stars + ?, 
               coins = coins + ? 
               WHERE telegram_id=?""",
            (current_status, json.dumps(list(claimed)), stars_bonus, coins_bonus, tid)
        )

    updated_user = get_user(tid)
    return {
        "status": current_status,
        "stars_bonus": stars_bonus,
        "coins_bonus": coins_bonus,
        "unlocked_awards": unlocked_awards,
        "user": updated_user
    }


def submit_tap_score(tid: int, score: int) -> dict:
    """Updates user's tap score and calculates current leaderboard."""
    score = int(score)
    with _conn() as c:
        c.execute(
            "UPDATE users SET tap_score=?, tap_best=MAX(tap_best, ?) WHERE telegram_id=?",
            (score, score, tid)
        )
    return get_tap_leaderboard()


def get_tap_leaderboard(limit: int = 15) -> dict:
    with _conn() as c:
        rows = c.execute(
            """SELECT telegram_id, first_name, username, tap_best 
               FROM users WHERE tap_best > 0 
               ORDER BY tap_best DESC LIMIT ?""",
            (limit,)
        ).fetchall()

    leaders = []
    for idx, r in enumerate(rows, start=1):
        name = r["first_name"] or r["username"] or f"Игрок #{r['telegram_id']}"
        reward = ""
        if idx == 1:
            reward = "50 ⭐"
        elif idx == 2:
            reward = "25 ⭐"
        elif idx == 3:
            reward = "1000 🪙"
        leaders.append({
            "rank": idx,
            "telegram_id": r["telegram_id"],
            "name": name,
            "score": r["tap_best"],
            "reward": reward
        })

    return {"leaders": leaders}


# --- Deposit Requests (Card & Gift) ---

def create_deposit_request(tid: int, amount_uzs: int, stars: int, receipt_file: str = "", method: str = "card") -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO deposit_requests (telegram_id, amount_uzs, stars, receipt_file, method, status) 
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (tid, amount_uzs, stars, receipt_file, method)
        )
        return cur.lastrowid


def get_user_deposit_requests(tid: int) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM deposit_requests WHERE telegram_id=? ORDER BY id DESC LIMIT 20",
            (tid,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_deposit_requests(status_filter: str = None) -> list:
    with _conn() as c:
        query = """
            SELECT r.*, u.first_name, u.username 
            FROM deposit_requests r
            LEFT JOIN users u ON r.telegram_id = u.telegram_id
        """
        params = []
        if status_filter:
            query += " WHERE r.status=? "
            params.append(status_filter)
        query += " ORDER BY r.id DESC LIMIT 100"
        rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def process_deposit_request(req_id: int, action: str, admin_id: int) -> tuple[bool, str, dict]:
    """Action: 'approve' or 'reject'. Returns (success, message, request_data)."""
    with _conn() as c:
        row = c.execute("SELECT * FROM deposit_requests WHERE id=?", (req_id,)).fetchone()
        if not row:
            return False, "Заявка не найдена", {}
        req = dict(row)
        if req["status"] != "pending":
            return False, f"Заявка уже обработана (статус: {req['status']})", req

        if action == "approve":
            c.execute(
                "UPDATE deposit_requests SET status='approved', processed_at=strftime('%s','now'), admin_id=? WHERE id=?",
                (admin_id, req_id)
            )
            stars = req["stars"]
            uzs = req.get("amount_uzs") or 0
            # Начисляем звёзды и учитываем депозит
            c.execute(
                """UPDATE users SET 
                   stars = stars + ?, 
                   deposited = deposited + ?, 
                   uzs_deposited = uzs_deposited + ? 
                   WHERE telegram_id=?""",
                (stars, stars, uzs, req["telegram_id"])
            )
            req["status"] = "approved"

            # Проверяем обновление статуса (Гость -> Местный -> Крутой -> Элита)
            check_and_update_status(req["telegram_id"])

            return True, f"Заявка #{req_id} одобрена, начислено {stars} ⭐", req
        elif action == "reject":
            c.execute(
                "UPDATE deposit_requests SET status='rejected', processed_at=strftime('%s','now'), admin_id=? WHERE id=?",
                (admin_id, req_id)
            )
            req["status"] = "rejected"
            return True, f"Заявка #{req_id} отклонена", req
        else:
            return False, "Неизвестное действие", req


init()
