"""
db.py – User-Datenbank für Buchgenerierungs-Limit
===================================================
Speichert pro Telegram-User: Anzahl der Generierungen + Timestamps.
Verbindung über DATABASE_URL (Railway Postgres Plugin).
"""

import os
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("maerchenbuch.db")

MAX_GENERATIONS = 3


def _get_conn():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL ist nicht gesetzt.")
    return psycopg2.connect(url, sslmode="require")


def init_db():
    """Legt die Tabelle an, falls sie noch nicht existiert."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id BIGINT PRIMARY KEY,
                    generation_count  INTEGER NOT NULL DEFAULT 0,
                    first_seen        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_generation   TIMESTAMPTZ
                );
            """)
        conn.commit()
    logger.info("DB initialisiert.")


def get_generation_count(user_id: int) -> int:
    """Gibt die bisherige Anzahl der Buchgenerierungen zurück."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT generation_count FROM users WHERE telegram_user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    return row[0] if row else 0


def increment_generation(user_id: int) -> int:
    """
    Erhöht den Zähler um 1 (legt den User an falls nötig).
    Gibt den neuen Stand zurück.
    """
    now = datetime.now(timezone.utc)
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (telegram_user_id, generation_count, first_seen, last_generation)
                VALUES (%s, 1, %s, %s)
                ON CONFLICT (telegram_user_id) DO UPDATE
                    SET generation_count = users.generation_count + 1,
                        last_generation  = EXCLUDED.last_generation
                RETURNING generation_count;
            """, (user_id, now, now))
            new_count = cur.fetchone()[0]
        conn.commit()
    logger.info(f"User {user_id}: generation_count -> {new_count}")
    return new_count


def is_limit_reached(user_id: int) -> bool:
    """True wenn der User das Maximum bereits erreicht hat."""
    return get_generation_count(user_id) >= MAX_GENERATIONS
