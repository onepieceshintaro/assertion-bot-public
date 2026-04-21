"""DB抽象レイヤー（アサーション練習・公開版）。
共有 Supabase 上のテーブル prefix: `assertion_`
"""
import os
from functools import lru_cache

import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _get_database_url() -> str:
    try:
        url = st.secrets.get("DATABASE_URL")
        if url:
            return url
    except Exception:
        pass
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return "sqlite:///assertion.db"


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = _normalize_url(_get_database_url())
    return create_engine(url, pool_pre_ping=True, future=True)


def is_postgres() -> bool:
    return "postgresql" in str(get_engine().url)


def init_db() -> None:
    """3テーブルを作成（冪等）。"""
    engine = get_engine()
    pg = is_postgres()

    # SERIAL vs AUTOINCREMENT
    pk_id = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    with engine.begin() as conn:
        # assertion_records
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS assertion_records (
                id {pk_id},
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_datetime TEXT,
                mode TEXT,
                situation TEXT,
                thoughts TEXT,
                why_unpleasant TEXT,
                other_fault TEXT,
                other_reason TEXT,
                self_fault TEXT,
                script_variants TEXT,
                chosen_script TEXT,
                todo TEXT,
                insight TEXT,
                relationship TEXT,
                outcome_said INTEGER,
                outcome_result TEXT,
                outcome_learnings TEXT,
                raw_conversation TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_assertion_user "
            "ON assertion_records(user_id, created_at)"
        ))

        # assertion_weekly_reports（アプリ間衝突避けのため prefix）
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS assertion_weekly_reports (
                user_id TEXT NOT NULL,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                n_records INTEGER,
                markdown TEXT NOT NULL,
                PRIMARY KEY (user_id, week_start)
            )
        """))

        # assertion_risk_scores
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS assertion_risk_scores (
                id {pk_id},
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                user_message TEXT,
                triggered INTEGER,
                level TEXT,
                source TEXT,
                overall INTEGER,
                self_harm INTEGER,
                harm_to_others INTEGER,
                abuse INTEGER,
                acute INTEGER,
                reasoning TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_assertion_risk_user "
            "ON assertion_risk_scores(user_id, created_at)"
        ))
