"""週次レポート生成。cbt-botのパターンを流用。"""
import json
from datetime import date, timedelta

import pandas as pd


REPORT_SYSTEM_PROMPT = """あなたは、ユーザーの1週間のアサーション記録を**冷静に振り返る**サポーターです。

目的：
- 今週どんな場面で「伝えたいこと」があったかを整理する
- 伝えられた／伝えられなかったパターンを見る
- 来週の小さな一歩を見つける

避けること：
- 感情を煽る言葉
- 診断や医療的判断
- 「もっとこうすべき」という押し付け

守ること：
- 事実ベースの観察を優先
- ユーザーの言葉（todo, insight, chosen_script）を尊重して引用
- 「伝えられなかった」も等しく肯定的に扱う（言えなかった自分を責めない）

出力フォーマット（Markdown）：

## 📝 今週のまとめ
2〜3文で、今週全体を落ち着いた文体で要約。

## 🎯 繰り返し現れた相手・場面
箇条書きで3〜5個。同じ相手・似た場面が複数回出ていれば注目。

## 🗣 伝えられたこと／伝えられなかったこと
outcome_said の情報を元に、今週の実行パターンを整理。
「伝えられた」は肯定的に、「伝えられなかった」もそれ自体を肯定して扱う。

## 💡 今週の気づき
insight と todo から特に役立ちそうなもの2〜3個を短く引用。

## 🌱 来週試してみたい小さな一歩
実行可能なものを2〜3個。大きすぎない、今週の記録から自然に導かれるもの。

## 💬 ひとこと
1〜2文。労わり＋事実確認の締め。
"""


def current_week_range(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def filter_week(df: pd.DataFrame, week_start: date, week_end: date) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    d["event_datetime"] = pd.to_datetime(
        d["event_datetime"].fillna(d.get("created_at")),
        format="mixed",
        errors="coerce",
    )
    mask = (d["event_datetime"].dt.date >= week_start) & \
           (d["event_datetime"].dt.date <= week_end)
    return d[mask].sort_values("event_datetime").reset_index(drop=True)


def _format_records_for_prompt(df: pd.DataFrame) -> str:
    lines = []
    for _, r in df.iterrows():
        dt = r["event_datetime"].strftime("%m/%d %a %H:%M")
        lines.append(f"### {dt}")
        if r.get("relationship"):
            lines.append(f"- 相手: {r['relationship']}")
        if r.get("situation"):
            lines.append(f"- 出来事: {r['situation']}")
        if r.get("thoughts"):
            lines.append(f"- 思ったこと: {r['thoughts']}")
        if r.get("why_unpleasant"):
            lines.append(f"- 嫌だった理由: {r['why_unpleasant']}")

        # 選んだセリフ
        try:
            chosen = json.loads(r.get("chosen_script") or "null")
            if chosen:
                lines.append(f"- 選んだセリフのトーン: {chosen.get('tone_label', '')}")
                if chosen.get("step2_empathy_feeling"):
                    lines.append(f"  伝えるセリフ: {chosen['step2_empathy_feeling']}")
        except Exception:
            pass

        # 伝えたかどうか
        said = r.get("outcome_said")
        if said is not None:
            lines.append(f"- 伝えたか: {'はい' if said else 'いいえ（言えなかった）'}")
        if r.get("outcome_result"):
            lines.append(f"- 結果: {r['outcome_result']}")

        if r.get("todo"):
            lines.append(f"- TODO: {r['todo']}")
        if r.get("insight"):
            lines.append(f"- 気づき: {r['insight']}")
        lines.append("")
    return "\n".join(lines)


def generate_weekly_report(
    df_week: pd.DataFrame, client, model: str = "claude-sonnet-4-5"
) -> str:
    if df_week.empty:
        return "_この週はまだ記録がありません。_"

    records_text = _format_records_for_prompt(df_week)
    start = df_week["event_datetime"].min().strftime("%Y/%m/%d")
    end = df_week["event_datetime"].max().strftime("%Y/%m/%d")

    user_prompt = (
        f"期間: {start}〜{end}\n"
        f"記録数: {len(df_week)}件\n\n"
        f"以下が今週のアサーション記録です。上記のフォーマットで振り返ってください。\n\n"
        f"{records_text}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": REPORT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text
