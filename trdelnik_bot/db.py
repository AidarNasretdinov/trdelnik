import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "orders.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER,
                name            TEXT,
                phone           TEXT,
                location        TEXT,
                items           TEXT,
                total           INTEGER,
                status          TEXT    DEFAULT 'new',
                customer_msg_id INTEGER,
                owner_msg_id    INTEGER,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def create_order(
    telegram_user_id: int,
    name: str,
    phone: str,
    location: str,
    items: list,
    total: int,
) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO orders
               (telegram_user_id, name, phone, location, items, total)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (telegram_user_id, name, phone, location, json.dumps(items, ensure_ascii=False), total),
        )
        conn.commit()
        return cur.lastrowid


def get_order(order_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["items"] = json.loads(d["items"])
        return d


def update_order(order_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [order_id]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE orders SET {sets} WHERE id = ?", vals)
        conn.commit()


def list_orders_today() -> list[dict]:
    """Все заказы за сегодня (по московскому времени)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders WHERE date(created_at, '+3 hours') = date('now', '+3 hours') ORDER BY id ASC"
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["items"] = json.loads(d["items"])
            result.append(d)
        return result


def list_orders_by_date(date_str: str) -> list[dict]:
    """Заказы за конкретную дату в формате YYYY-MM-DD (МСК UTC+3)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders WHERE date(created_at, '+3 hours') = ? ORDER BY id ASC",
            (date_str,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["items"] = json.loads(d["items"])
            result.append(d)
        return result


def list_orders(status: str | None = None, limit: int = 20) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if status:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["items"] = json.loads(d["items"])
            result.append(d)
        return result
