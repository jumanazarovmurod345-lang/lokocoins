"""SQLite database for LOGO COINS & RubUP redesign — balances, deposits, checks, admin & settings."""

import sqlite3
import json
import os

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
                telegram_id  INTEGER PRIMARY KEY,
                first_name   TEXT    DEFAULT '',
                username     TEXT    DEFAULT '',
                coins        INTEGER DEFAULT 0,
                stars        INTEGER DEFAULT 0,
                deposited    INTEGER DEFAULT 0,
                last_free    INTEGER DEFAULT 0,
                rank_claims  TEXT    DEFAULT '{}',
                inventory    TEXT    DEFAULT '[]',
                promos       TEXT    DEFAULT '{}',
                created_at   REAL    DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS deposit_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL,
                amount_rub   INTEGER NOT NULL,
                stars        INTEGER NOT NULL,
                receipt_file TEXT    NOT NULL,
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
        # Default settings if not present
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_number', '2200 7000 1234 5678')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_holder', 'ИВАН И.')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('card_bank', 'Сбербанк / Т-Банк')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('rate_rub_to_star', '2.0')") # 2 руб = 1 звезда


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
    return d


def get_or_create(tid: int, first_name: str = "", username: str = "") -> dict:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users (telegram_id, first_name, username) VALUES (?,?,?)",
            (tid, first_name, username),
        )
        if first_name or username:
            c.execute(
                "UPDATE users SET first_name=?, username=? WHERE telegram_id=?",
                (first_name, username, tid)
            )
    return get_user(tid)


def get_all_users() -> list:
    with _conn() as c:
        rows = c.execute("SELECT telegram_id, first_name, username, coins, stars, deposited, created_at FROM users ORDER BY deposited DESC").fetchall()
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


def add_stars(tid: int, amount: int):
    with _conn() as c:
        c.execute(
            "UPDATE users SET stars=stars+?, deposited=deposited+? WHERE telegram_id=?",
            (amount, amount, tid),
        )


# --- Deposit Requests (Manual check confirmation) ---

def create_deposit_request(tid: int, amount_rub: int, stars: int, receipt_file: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO deposit_requests (telegram_id, amount_rub, stars, receipt_file, status) VALUES (?,?,?,?,'pending')",
            (tid, amount_rub, stars, receipt_file)
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
            # Начисляем звёзды ровно один раз
            c.execute(
                "UPDATE users SET stars=stars+?, deposited=deposited+? WHERE telegram_id=?",
                (req["stars"], req["stars"], req["telegram_id"])
            )
            req["status"] = "approved"
            return True, f"Заявка #{req_id} подтверждена, начислено {req['stars']} ⭐", req
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
