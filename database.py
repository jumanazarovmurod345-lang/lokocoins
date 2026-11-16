"""SQLite database for LOGO COINS — user balances & payment log."""

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
                promos       TEXT    DEFAULT '{}'
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


def get_user(tid: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE telegram_id=?", (tid,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["rank_claims"] = json.loads(d["rank_claims"])
    d["inventory"] = json.loads(d["inventory"])
    d["promos"] = json.loads(d["promos"])
    return d


def get_or_create(tid: int, first_name: str = "", username: str = "") -> dict:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users (telegram_id, first_name, username) "
            "VALUES (?,?,?)",
            (tid, first_name, username),
        )
    return get_user(tid)


def save_state(tid: int, state: dict):
    """Bulk-save mutable user fields."""
    with _conn() as c:
        c.execute(
            """UPDATE users SET
                coins=?, stars=?, deposited=?, last_free=?,
                rank_claims=?, inventory=?, promos=?
               WHERE telegram_id=?""",
            (
                int(state.get("coins", 0)),
                int(state.get("stars", 0)),
                int(state.get("deposited", 0)),
                int(state.get("last_free", 0)),
                json.dumps(state.get("rank_claims", {})),
                json.dumps(state.get("inventory", [])),
                json.dumps(state.get("promos", {})),
                tid,
            ),
        )


def add_stars(tid: int, amount: int):
    """Credit stars from a real payment (also bumps deposited)."""
    with _conn() as c:
        c.execute(
            "UPDATE users SET stars=stars+?, deposited=deposited+? "
            "WHERE telegram_id=?",
            (amount, amount, tid),
        )


def log_payment(
    tid: int, amount: int, charge_id: str, provider_charge_id: str
):
    with _conn() as c:
        c.execute(
            "INSERT INTO payments "
            "(telegram_id, amount, charge_id, provider_charge_id) "
            "VALUES (?,?,?,?)",
            (tid, amount, charge_id, provider_charge_id),
        )


# Create tables on import
init()
