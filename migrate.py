"""ローカル assertion.db → Supabase Postgres への一回限りの移行スクリプト。

使い方：
  python migrate.py               # 本番
  python migrate.py --dry-run     # 件数だけ確認
  python migrate.py --source PATH # 移行元SQLiteを指定（既定: ../assertion-bot/assertion.db）
"""
import argparse
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sqlalchemy import text  # noqa: E402

from db import get_engine, init_db, is_postgres  # noqa: E402


DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "assertion-bot" / "assertion.db"


ASSERTION_COLUMNS = [
    "user_id", "created_at", "event_datetime", "mode",
    "situation", "thoughts", "why_unpleasant",
    "other_fault", "other_reason", "self_fault",
    "script_variants", "chosen_script",
    "todo", "insight", "relationship",
    "outcome_said", "outcome_result", "outcome_learnings",
    "raw_conversation",
]
WEEKLY_COLUMNS = [
    "user_id", "week_start", "week_end",
    "generated_at", "n_records", "markdown",
]
RISK_COLUMNS = [
    "user_id", "created_at", "user_message", "triggered", "level", "source",
    "overall", "self_harm", "harm_to_others", "abuse", "acute", "reasoning",
]


def _fetch(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[dict]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {r[1] for r in cur.fetchall()}
    if not existing:
        return []
    parts = []
    for c in columns:
        if c in existing:
            parts.append(c)
        else:
            parts.append(f"NULL AS {c}")
    sql = f"SELECT {', '.join(parts)} FROM {table}"
    cur = conn.execute(sql)
    return [dict(r) for r in cur.fetchall()]


def _resolve_default_user_id() -> str:
    try:
        from _user import get_or_create_user_id
        return get_or_create_user_id()
    except Exception:
        p = Path.home() / ".note_apps_user_id"
        if p.exists():
            uid = p.read_text(encoding="utf-8").strip()
            if len(uid) == 32:
                return uid
        print("❌ user_id を解決できません。")
        sys.exit(1)


def _fill_user_id(rows: list[dict], default_uid: str | None) -> str | None:
    for r in rows:
        if not r.get("user_id"):
            if default_uid is None:
                default_uid = _resolve_default_user_id()
                print(f"ℹ️  user_id 未設定をフォールバックIDで埋めます: {default_uid[:8]}…")
            r["user_id"] = default_uid
    return default_uid


def _upsert_assertion(conn, rows: list[dict]) -> None:
    # id は自動採番なので含めない（新規INSERTとして扱う）
    cols = [c for c in ASSERTION_COLUMNS]
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = text(
        f"INSERT INTO assertion_records ({col_list}) VALUES ({placeholders})"
    )
    for r in rows:
        conn.execute(sql, {c: r.get(c) for c in cols})


def _upsert_weekly(conn, rows: list[dict]) -> None:
    col_list = ", ".join(WEEKLY_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in WEEKLY_COLUMNS)
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in WEEKLY_COLUMNS
        if c not in ("user_id", "week_start")
    )
    sql = text(f"""
        INSERT INTO assertion_weekly_reports ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (user_id, week_start) DO UPDATE SET {update_set}
    """)
    for r in rows:
        conn.execute(sql, {c: r.get(c) for c in WEEKLY_COLUMNS})


def _upsert_risk(conn, rows: list[dict]) -> None:
    col_list = ", ".join(RISK_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in RISK_COLUMNS)
    sql = text(
        f"INSERT INTO assertion_risk_scores ({col_list}) VALUES ({placeholders})"
    )
    for r in rows:
        conn.execute(sql, {c: r.get(c) for c in RISK_COLUMNS})


def migrate(sqlite_path: Path, dry_run: bool = False) -> None:
    if not sqlite_path.exists():
        print(f"❌ SQLite ファイルが見つかりません: {sqlite_path}")
        sys.exit(1)

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    try:
        a_rows = _fetch(src, "assertion_records", ASSERTION_COLUMNS)
        w_rows = _fetch(src, "weekly_reports", WEEKLY_COLUMNS)
        r_rows = _fetch(src, "risk_scores", RISK_COLUMNS)
    finally:
        src.close()

    default_uid = None
    default_uid = _fill_user_id(a_rows, default_uid)
    default_uid = _fill_user_id(w_rows, default_uid)
    default_uid = _fill_user_id(r_rows, default_uid)

    print(f"📦 source: {sqlite_path}")
    print(f"   assertion_records: {len(a_rows)} 件")
    print(f"   weekly_reports   : {len(w_rows)} 件")
    print(f"   risk_scores      : {len(r_rows)} 件")

    if dry_run:
        print("  --dry-run 指定のため書き込みはスキップ。")
        return

    engine = get_engine()
    try:
        safe_url = engine.url.render_as_string(hide_password=True)
    except Exception:
        safe_url = f"{engine.url.drivername}://***@{engine.url.host}:{engine.url.port}"
    print(f"🎯 target: {safe_url}")
    if not is_postgres():
        print("⚠️  ターゲットが Postgres ではありません。DATABASE_URL を確認してください。")

    init_db()

    with engine.begin() as conn:
        _upsert_assertion(conn, a_rows)
        _upsert_weekly(conn, w_rows)
        _upsert_risk(conn, r_rows)

    print(f"✅ 完了: {len(a_rows) + len(w_rows) + len(r_rows)} 件を書き込みました。")


def main() -> None:
    ap = argparse.ArgumentParser(description="SQLite → Supabase 移行（assertion-bot）")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    migrate(args.source, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
