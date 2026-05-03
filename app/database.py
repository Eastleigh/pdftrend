import os
from pathlib import Path

import aiosqlite

_default_db = "/data/trends.db" if Path("/data").exists() else str(
    Path(__file__).parent.parent / "trends.db"
)
DB_PATH = os.getenv("TRENDS_DB", _default_db)


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_premium INTEGER DEFAULT 0,
                stripe_payment_id TEXT,
                premium_since TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS saved_trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                category TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query TEXT NOT NULL,
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS recent_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                query TEXT NOT NULL,
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                stripe_session_id TEXT,
                stripe_payment_intent TEXT,
                amount_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS daily_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_identifier TEXT NOT NULL,
                search_date TEXT NOT NULL,
                search_count INTEGER DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_saved_trends_user ON saved_trends(user_id);
            CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(user_id);
            CREATE INDEX IF NOT EXISTS idx_recent_searches_session ON recent_searches(session_id);
            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_searches_unique
                ON daily_searches(user_identifier, search_date);
        """)

        # Migrate existing users table if columns missing
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "is_premium" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
        if "stripe_payment_id" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN stripe_payment_id TEXT")
        if "premium_since" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN premium_since TIMESTAMP")

        await db.commit()
    finally:
        await db.close()
