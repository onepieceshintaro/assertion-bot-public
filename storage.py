"""アサーション記録保存（SQLAlchemy・SQLite/Postgres両対応）。"""
import json
from datetime import datetime

import pandas as pd
from sqlalchemy import text

from db import get_engine, init_db as _init_db


def _owner_user_id() -> str:
    from _user import get_or_create_user_id
    return get_or_create_user_id()


def init_db() -> None:
    _init_db()


def save_record(
    record: dict, conversation: list,
    event_datetime: str | None = None,
    mode: str | None = None,
    script_variants: list | None = None,
    chosen_script: dict | None = None,
    user_id: str | None = None,
) -> int:
    if event_datetime is None:
        event_datetime = datetime.now().isoformat()
    if user_id is None:
        user_id = _owner_user_id()

    params = {
        "user_id": user_id,
        "created_at": datetime.now().isoformat(),
        "event_datetime": event_datetime,
        "mode": mode,
        "situation": record.get("situation"),
        "thoughts": record.get("thoughts"),
        "why_unpleasant": record.get("why_unpleasant"),
        "other_fault": record.get("other_fault"),
        "other_reason": record.get("other_reason"),
        "self_fault": record.get("self_fault"),
        "script_variants": (
            json.dumps(script_variants, ensure_ascii=False) if script_variants else None
        ),
        "chosen_script": (
            json.dumps(chosen_script, ensure_ascii=False) if chosen_script else None
        ),
        "todo": record.get("todo"),
        "insight": record.get("insight"),
        "relationship": record.get("relationship"),
        "raw_conversation": json.dumps(conversation, ensure_ascii=False),
    }

    sql = text("""
        INSERT INTO assertion_records
        (user_id, created_at, event_datetime, mode,
         situation, thoughts, why_unpleasant,
         other_fault, other_reason, self_fault,
         script_variants, chosen_script,
         todo, insight, relationship, raw_conversation)
        VALUES
        (:user_id, :created_at, :event_datetime, :mode,
         :situation, :thoughts, :why_unpleasant,
         :other_fault, :other_reason, :self_fault,
         :script_variants, :chosen_script,
         :todo, :insight, :relationship, :raw_conversation)
        RETURNING id
    """)
    with get_engine().begin() as conn:
        row = conn.execute(sql, params).first()
        return int(row[0])


def update_outcome(
    record_id: int,
    said: bool | None,
    result: str | None,
    learnings: str | None,
    user_id: str | None = None,
):
    if user_id is None:
        user_id = _owner_user_id()
    sql = text("""
        UPDATE assertion_records
        SET outcome_said = :said,
            outcome_result = :result,
            outcome_learnings = :learnings
        WHERE id = :id AND user_id = :user_id
    """)
    with get_engine().begin() as conn:
        conn.execute(sql, {
            "said": None if said is None else (1 if said else 0),
            "result": result,
            "learnings": learnings,
            "id": record_id,
            "user_id": user_id,
        })


def update_chosen_script(record_id: int, chosen_script: dict,
                         user_id: str | None = None):
    if user_id is None:
        user_id = _owner_user_id()
    sql = text("""
        UPDATE assertion_records
        SET chosen_script = :chosen_script
        WHERE id = :id AND user_id = :user_id
    """)
    with get_engine().begin() as conn:
        conn.execute(sql, {
            "chosen_script": json.dumps(chosen_script, ensure_ascii=False),
            "id": record_id,
            "user_id": user_id,
        })


def load_records(user_id: str | None = None) -> pd.DataFrame:
    if user_id is None:
        user_id = _owner_user_id()
    with get_engine().connect() as conn:
        return pd.read_sql(
            text("SELECT * FROM assertion_records "
                 "WHERE user_id = :user_id ORDER BY created_at"),
            conn, params={"user_id": user_id},
        )


def save_weekly_report(week_start, week_end, markdown: str, n_records: int,
                       user_id: str | None = None):
    if user_id is None:
        user_id = _owner_user_id()
    sql = text("""
        INSERT INTO assertion_weekly_reports
        (user_id, week_start, week_end, generated_at, n_records, markdown)
        VALUES (:user_id, :week_start, :week_end, :generated_at, :n_records, :markdown)
        ON CONFLICT (user_id, week_start) DO UPDATE SET
            week_end = EXCLUDED.week_end,
            generated_at = EXCLUDED.generated_at,
            n_records = EXCLUDED.n_records,
            markdown = EXCLUDED.markdown
    """)
    with get_engine().begin() as conn:
        conn.execute(sql, {
            "user_id": user_id,
            "week_start": str(week_start),
            "week_end": str(week_end),
            "generated_at": datetime.now().isoformat(),
            "n_records": n_records,
            "markdown": markdown,
        })


def load_weekly_report(week_start, user_id: str | None = None) -> dict | None:
    if user_id is None:
        user_id = _owner_user_id()
    sql = text(
        "SELECT week_start, week_end, generated_at, n_records, markdown "
        "FROM assertion_weekly_reports "
        "WHERE week_start = :week_start AND user_id = :user_id"
    )
    with get_engine().connect() as conn:
        row = conn.execute(sql, {
            "week_start": str(week_start), "user_id": user_id,
        }).first()
    if not row:
        return None
    return {
        "week_start": row[0], "week_end": row[1],
        "generated_at": row[2], "n_records": row[3],
        "markdown": row[4],
    }


def load_all_weekly_reports(user_id: str | None = None) -> pd.DataFrame:
    if user_id is None:
        user_id = _owner_user_id()
    with get_engine().connect() as conn:
        return pd.read_sql(
            text("SELECT * FROM assertion_weekly_reports "
                 "WHERE user_id = :user_id ORDER BY week_start DESC"),
            conn, params={"user_id": user_id},
        )


def save_risk_score(user_message: str, result: dict, user_id: str | None = None):
    if user_id is None:
        user_id = _owner_user_id()
    s = result.get("score", {}) or {}
    sql = text("""
        INSERT INTO assertion_risk_scores
        (user_id, created_at, user_message, triggered, level, source, overall,
         self_harm, harm_to_others, abuse, acute, reasoning)
        VALUES
        (:user_id, :created_at, :user_message, :triggered, :level, :source, :overall,
         :self_harm, :harm_to_others, :abuse, :acute, :reasoning)
    """)
    with get_engine().begin() as conn:
        conn.execute(sql, {
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "user_message": user_message,
            "triggered": 1 if result.get("triggered") else 0,
            "level": result.get("level"),
            "source": result.get("source"),
            "overall": s.get("overall", 0),
            "self_harm": s.get("self_harm", 0),
            "harm_to_others": s.get("harm_to_others", 0),
            "abuse": s.get("abuse", 0),
            "acute": s.get("acute", 0),
            "reasoning": s.get("reasoning", ""),
        })
