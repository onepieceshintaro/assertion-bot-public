import json
import re
from datetime import datetime, date, time, timedelta

import streamlit as st
import pandas as pd

from assertion_engine import (
    chat, assess_risk, generate_scripts, diagnose_tendency,
    client as anthropic_client,
)
from storage import (
    init_db, save_record, load_records, update_outcome, update_chosen_script,
    save_weekly_report, load_weekly_report, load_all_weekly_reports,
    save_risk_score,
)
from prompts import (
    PHASE_ORDER, PHASE_LABELS, PHASE_HINTS, PHASE_NOTES,
    SKIPPABLE_PHASES,
    MODE_CONFIGS, DEFAULT_MODE, MODE_PRACTICAL, MODE_TRAINING,
    CRISIS_RESPONSE, ABUSE_ADVISORY,
)
from reports import current_week_range, filter_week, generate_weekly_report
from _user import render_account_sidebar

st.set_page_config(
    page_title="伝え方ノート",
    page_icon="🗣",
    layout="wide",
)

CURRENT_USER_ID = render_account_sidebar()
init_db()

st.markdown("""
<style>
header[data-testid="stHeader"] { background: white; }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# AI応答のメタ情報抽出
# ----------------------------------------------------------------------------
PHASE_PATTERN = re.compile(r"<!--\s*phase:\s*(\w+)\s*-->")


def parse_phase(text: str) -> str | None:
    m = PHASE_PATTERN.search(text)
    return m.group(1) if m else None


def strip_meta(text: str) -> str:
    text = PHASE_PATTERN.sub("", text)
    text = re.sub(r"```json\s*\{.*?\}\s*```", "", text, flags=re.DOTALL)
    return text.strip()


def extract_json(text: str) -> dict | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def progress_ratio(phase: str | None) -> float:
    if phase is None or phase == "crisis":
        return 0.0
    if phase not in PHASE_ORDER:
        return 0.0
    return (PHASE_ORDER.index(phase) + 1) / len(PHASE_ORDER)


# ----------------------------------------------------------------------------
# セッション状態の初期化
# ----------------------------------------------------------------------------
_defaults = {
    "messages": [],
    "current_phase": None,
    "record_saved": False,
    "mode": DEFAULT_MODE,
    "training_category": None,
    "scripts_generated": [],
    "chosen_script_idx": None,
    "last_record_id": None,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def reset_session():
    st.session_state.messages = []
    st.session_state.current_phase = None
    st.session_state.record_saved = False
    st.session_state.scripts_generated = []
    st.session_state.chosen_script_idx = None
    st.session_state.last_record_id = None
    st.session_state.view_radio = "💬 対話"


# ----------------------------------------------------------------------------
# サイドバー
# ----------------------------------------------------------------------------
with st.sidebar:
    _hub_url = "https://app-public-qpy8b2ziwgdf9h2vmu5hqp.streamlit.app/"
    if CURRENT_USER_ID:
        _hub_url += f"?u={CURRENT_USER_ID}"
    st.link_button(
        "🏠 HOME に戻る",
        _hub_url,
        use_container_width=True,
    )
    st.link_button(
        "💬 ご意見・感想",
        "https://docs.google.com/forms/d/e/1FAIpQLSetCb_dHG6JFsUzhK9ZYxydgh5cP8w07Q6NRO4ouEM7BvSTRw/viewform",
        use_container_width=True,
    )
    st.divider()
    view = st.radio(
        "表示",
        ["💬 対話", "📊 傾向を見る", "📝 週次レポート", "📖 過去の記録"],
        label_visibility="collapsed",
        key="view_radio",
    )
    st.divider()

    # モード選択（セッション中は変更不可）
    _mode_keys = list(MODE_CONFIGS.keys())
    _mode_display = [MODE_CONFIGS[k]["display_name"] for k in _mode_keys]
    _can_change = len(st.session_state.messages) == 0
    st.markdown("**モード**")
    if _can_change:
        sel = st.radio(
            "モード",
            _mode_display,
            index=_mode_keys.index(st.session_state.mode),
            label_visibility="collapsed",
            key="mode_radio",
        )
        st.session_state.mode = _mode_keys[_mode_display.index(sel)]
        st.caption(MODE_CONFIGS[st.session_state.mode]["description"])

        # 練習モードではカテゴリ選択
        if st.session_state.mode == MODE_TRAINING:
            st.session_state.training_category = st.selectbox(
                "練習カテゴリ",
                ["職場", "家族", "友人", "パートナー", "店員・知らない人"],
                key="training_cat_radio",
            )
    else:
        st.caption(f"現在：{MODE_CONFIGS[st.session_state.mode]['display_name']}")
        if st.session_state.mode == MODE_TRAINING and st.session_state.training_category:
            st.caption(f"カテゴリ：{st.session_state.training_category}")
        st.caption("※ モードはセッション開始後は固定")

    st.divider()

    # 進捗バー
    st.header("セッションの進捗")
    phase = st.session_state.current_phase
    if phase:
        label = PHASE_LABELS.get(phase, phase)
        ratio = progress_ratio(phase)
        st.markdown(f"""
        <div style="font-size: 14px; color: #555; margin-bottom: 6px;">
          現在：<b>{label}</b>
        </div>
        <div style="background: #eceff1; border-radius: 6px; height: 10px;
                    overflow: hidden; margin-bottom: 12px;">
          <div style="background: linear-gradient(90deg,#42a5f5,#1e88e5);
                      height: 100%; width: {ratio*100:.0f}%;
                      transition: width .4s ease;"></div>
        </div>
        """, unsafe_allow_html=True)

        current_idx = PHASE_ORDER.index(phase) if phase in PHASE_ORDER else -1
        for i, p in enumerate(PHASE_ORDER):
            suffix = "（任意）" if p in SKIPPABLE_PHASES else ""
            if p == phase:
                st.markdown(f"▶ **{PHASE_LABELS[p]}**{suffix}")
            elif i < current_idx:
                st.markdown(
                    f"✓ <span style='color:#999'>{PHASE_LABELS[p]}{suffix}</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"　<span style='color:#bbb'>{PHASE_LABELS[p]}{suffix}</span>",
                    unsafe_allow_html=True,
                )
    else:
        st.caption("まだ始まっていません。入力欄に、書けるところから自由にどうぞ。")
        for p in PHASE_ORDER[:-1]:
            suffix = "（任意）" if p in SKIPPABLE_PHASES else ""
            st.markdown(
                f"　<span style='color:#999'>{PHASE_LABELS[p]}{suffix}</span>",
                unsafe_allow_html=True,
            )

    st.divider()
    st.button(
        "新しいセッションを始める",
        use_container_width=True,
        on_click=reset_session,
    )

    st.divider()
    st.caption("※ このBotは医療行為ではありません")
    with st.expander("辛いときの相談窓口"):
        st.markdown("""
        **メンタル**
        - いのちの電話：0570-783-556
        - よりそいホットライン：0120-279-338
        - こころの健康相談統一ダイヤル：0570-064-556

        **ハラスメント・DV**
        - DV相談ナビ：0570-0-55210
        - 労働条件相談ほっとライン：0120-811-610
        """)


# ----------------------------------------------------------------------------
# 対話ビュー
# ----------------------------------------------------------------------------
if view == "💬 対話":
    st.markdown("### 伝え方ノート")
    st.caption("自分も相手も大切にする伝え方を、一緒に考えていきます。")

    # いつの出来事か
    with st.expander("📅 いつの出来事ですか？（過去の事例もOK）", expanded=False):
        quick = st.radio(
            "クイック選択",
            ["今さっき", "今日", "昨日", "今週", "先週", "1ヶ月以内",
             "もっと前", "日時を指定"],
            horizontal=True,
            label_visibility="collapsed",
            key="quick_when",
        )
        now = datetime.now()
        if quick == "今さっき":
            event_dt = now
        elif quick == "今日":
            event_dt = datetime.combine(now.date(), time(12, 0))
        elif quick == "昨日":
            event_dt = datetime.combine(now.date() - timedelta(days=1), time(12, 0))
        elif quick == "今週":
            event_dt = datetime.combine(now.date() - timedelta(days=3), time(12, 0))
        elif quick == "先週":
            event_dt = datetime.combine(now.date() - timedelta(days=10), time(12, 0))
        elif quick == "1ヶ月以内":
            event_dt = datetime.combine(now.date() - timedelta(days=20), time(12, 0))
        elif quick == "もっと前":
            event_dt = datetime.combine(now.date() - timedelta(days=60), time(12, 0))
        else:
            c1, c2 = st.columns(2)
            with c1:
                d_in = st.date_input("日付", value=now.date(), key="event_date")
            with c2:
                t_in = st.time_input(
                    "時刻",
                    value=now.time().replace(second=0, microsecond=0),
                    key="event_time",
                )
            event_dt = datetime.combine(d_in, t_in)

        st.caption(f"記録対象：**{event_dt.strftime('%Y-%m-%d %H:%M')}**")
        st.session_state.event_datetime = event_dt.isoformat()

    # 過去履歴表示
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                st.write(strip_meta(m["content"]))
            else:
                st.write(m["content"])

    # ----- フェーズごとの注釈 -----
    note = PHASE_NOTES.get(phase) if phase else None
    if note and phase != "done":
        st.info(f"**{PHASE_LABELS.get(phase, phase)}**：{note}")

    # ----- スキップボタン（該当フェーズのみ） -----
    if phase in SKIPPABLE_PHASES:
        if st.button(
            f"⏭ 「{PHASE_LABELS[phase]}」をスキップ",
            key=f"skip_{phase}",
            help="このステップは任意です。今の自分に必要なければ飛ばしましょう。",
        ):
            # スキップは「スキップします」というユーザー発言として扱う
            skip_msg = "（このステップはスキップします）"
            st.session_state.messages.append(
                {"role": "user", "content": skip_msg}
            )
            with st.spinner("..."):
                raw_reply = chat(
                    st.session_state.messages, mode=st.session_state.mode
                )
            st.session_state.messages.append(
                {"role": "assistant", "content": raw_reply}
            )
            new_phase = parse_phase(raw_reply)
            if new_phase:
                st.session_state.current_phase = new_phase
            st.rerun()

    # ----- script_review フェーズ：セリフ3案生成ボタン -----
    if phase == "script_review":
        st.markdown("### ✍ セリフを考える")
        st.caption(
            "ここまでの内容から、アサーティブなセリフの案をAIに3つ出してもらえます。"
            "自分で考えてもOK。**押さない選択も肯定されます**。"
        )
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button(
                "💡 AIにセリフ案を3つ出してもらう",
                use_container_width=True,
                key="btn_gen_scripts",
            ):
                with st.spinner("セリフを考えています..."):
                    scripts = generate_scripts(st.session_state.messages)
                if scripts:
                    st.session_state.scripts_generated = scripts
                    st.session_state.chosen_script_idx = None
                else:
                    st.warning("生成に失敗しました。もう一度お試しください。")
        with c2:
            if st.session_state.scripts_generated:
                if st.button(
                    "🔄 別案を生成",
                    use_container_width=True,
                    key="btn_regen_scripts",
                ):
                    with st.spinner("別の案を考えています..."):
                        scripts = generate_scripts(st.session_state.messages)
                    if scripts:
                        st.session_state.scripts_generated = scripts
                        st.session_state.chosen_script_idx = None

        if st.session_state.scripts_generated:
            st.markdown("**💡 セリフ案（あくまで参考。ピンと来たものを選んで編集してください）**")
            for i, sc in enumerate(st.session_state.scripts_generated):
                with st.container(border=True):
                    label = sc.get("tone_label", sc.get("tone", "?"))
                    desc = sc.get("description", "")
                    st.markdown(f"### 案 {i+1}：{label}")
                    if desc:
                        st.caption(desc)
                    if sc.get("step1_mindset"):
                        st.markdown(
                            f"**ステップ1（気持ちの整え方）**  \n{sc['step1_mindset']}"
                        )
                    if sc.get("step2_empathy_feeling"):
                        st.markdown(
                            f"**ステップ2（共感＋自分の感情）**  \n"
                            f"> {sc['step2_empathy_feeling']}"
                        )
                    if sc.get("step3_suggestion"):
                        st.markdown(
                            f"**ステップ3（具体的な提案）**  \n"
                            f"> {sc['step3_suggestion']}"
                        )
                    if sc.get("step4_response_positive"):
                        st.markdown(
                            f"**ステップ4a（相手が肯定的な反応の場合）**  \n"
                            f"> {sc['step4_response_positive']}"
                        )
                    if sc.get("step4_response_negative"):
                        st.markdown(
                            f"**ステップ4b（相手が否定的な反応の場合）**  \n"
                            f"> {sc['step4_response_negative']}"
                        )

                    if st.button(
                        f"この案（{label}）を採用",
                        key=f"choose_{i}",
                    ):
                        st.session_state.chosen_script_idx = i
                        st.success(f"案{i+1}（{label}）を採用しました。")

            st.caption(
                "**自分の言葉に置き換えて使うのが一番効果的**です。"
                "AIのセリフはたたき台、あなたの口に馴染むよう調整してください。"
            )

    # ----- 入力欄 -----
    placeholder = PHASE_HINTS.get(phase, "書けることだけで大丈夫ですよ")

    if phase != "done":
        # 練習モードの冒頭：カテゴリを自動で伝える
        if (
            not st.session_state.messages
            and st.session_state.mode == MODE_TRAINING
        ):
            cat = st.session_state.training_category or "職場"
            opener = f"「{cat}」のカテゴリで練習したいです。シチュエーションを1つ提示してください。"
            with st.spinner("シチュエーションを考えています..."):
                st.session_state.messages.append(
                    {"role": "user", "content": opener}
                )
                raw_reply = chat(
                    st.session_state.messages, mode=st.session_state.mode
                )
                st.session_state.messages.append(
                    {"role": "assistant", "content": raw_reply}
                )
                new_phase = parse_phase(raw_reply)
                if new_phase:
                    st.session_state.current_phase = new_phase
            st.rerun()

        if prompt := st.chat_input(placeholder):
            st.session_state.messages.append(
                {"role": "user", "content": prompt}
            )
            with st.chat_message("user"):
                st.write(prompt)

            with st.chat_message("assistant"):
                with st.spinner("..."):
                    # 危機検知：キーワード＋LLMスコアリング
                    risk_result = assess_risk(prompt)
                    save_risk_score(prompt, risk_result)

                    if risk_result["triggered"]:
                        if risk_result["level"] == "crisis":
                            raw_reply = CRISIS_RESPONSE
                        elif risk_result["level"] == "abuse":
                            raw_reply = ABUSE_ADVISORY
                        else:
                            raw_reply = CRISIS_RESPONSE
                    else:
                        raw_reply = chat(
                            st.session_state.messages,
                            mode=st.session_state.mode,
                        )

                st.write(strip_meta(raw_reply))

                st.session_state.messages.append(
                    {"role": "assistant", "content": raw_reply}
                )

                new_phase = parse_phase(raw_reply)
                if new_phase:
                    st.session_state.current_phase = new_phase

                # 完了時：JSON抽出・保存
                if new_phase == "done" and not st.session_state.record_saved:
                    record = extract_json(raw_reply)
                    if record:
                        try:
                            chosen = None
                            if st.session_state.chosen_script_idx is not None:
                                chosen = st.session_state.scripts_generated[
                                    st.session_state.chosen_script_idx
                                ]
                            row_id = save_record(
                                record,
                                st.session_state.messages,
                                event_datetime=st.session_state.get("event_datetime"),
                                mode=st.session_state.mode,
                                script_variants=st.session_state.scripts_generated or None,
                                chosen_script=chosen,
                            )
                            st.session_state.last_record_id = row_id
                            st.session_state.record_saved = True
                            st.success("アサーション記録を保存しました。お疲れさまでした。")
                        except Exception as e:
                            st.warning(f"保存に失敗: {e}")

            st.rerun()
    else:
        st.success("今回のセッションは完了です。お疲れさまでした。")

        # 選んだセリフの再掲
        if st.session_state.chosen_script_idx is not None:
            sc = st.session_state.scripts_generated[
                st.session_state.chosen_script_idx
            ]
            st.markdown("### 🗣 採用したセリフ")
            with st.container(border=True):
                st.markdown(f"**{sc.get('tone_label', '')}**")
                st.markdown("**共感＋自分の感情**")
                st.write(sc.get("step2_empathy_feeling", ""))
                st.markdown("**具体的な提案**")
                st.write(sc.get("step3_suggestion", ""))

        # 事後フィードバック（後日記入用）
        if st.session_state.last_record_id:
            with st.expander("📝 実際に伝えてみたら教えてください（後日でもOK）"):
                said = st.radio(
                    "伝えましたか？",
                    ["まだ／未定", "伝えられた", "伝えなかった"],
                    key="outcome_said_radio",
                )
                outcome_result = st.text_area(
                    "結果はどうでしたか？（任意）",
                    key="outcome_result_text",
                )
                outcome_learnings = st.text_area(
                    "次に活かせそうなこと（任意）",
                    key="outcome_learnings_text",
                )
                if st.button("記録する", key="btn_outcome_save"):
                    said_bool = None
                    if said == "伝えられた":
                        said_bool = True
                    elif said == "伝えなかった":
                        said_bool = False
                    update_outcome(
                        st.session_state.last_record_id,
                        said_bool, outcome_result or None,
                        outcome_learnings or None,
                    )
                    st.success("記録しました。")

        # スマホでもサイドバーを開かずに次の行動が取れるよう、
        # メインエリアにも HOME / 新規セッション のボタンを置く
        st.divider()
        _done_hub_url = "https://app-public-qpy8b2ziwgdf9h2vmu5hqp.streamlit.app/"
        if CURRENT_USER_ID:
            _done_hub_url += f"?u={CURRENT_USER_ID}"
        _col_done_1, _col_done_2 = st.columns(2)
        with _col_done_1:
            st.link_button(
                "🏠 HOMEに戻る",
                _done_hub_url,
                use_container_width=True,
            )
        with _col_done_2:
            st.button(
                "🆕 新しいセッション",
                use_container_width=True,
                on_click=reset_session,
                key="btn_new_session_inline",
            )


# ----------------------------------------------------------------------------
# 傾向を見るビュー
# ----------------------------------------------------------------------------
elif view == "📊 傾向を見る":
    st.markdown("### あなたの傾向")
    df = load_records()

    if df.empty:
        st.info("まだ記録がありません。左側の「💬 対話」から始めてください。")
    else:
        _ev = pd.to_datetime(df["event_datetime"], errors="coerce")
        _cr = pd.to_datetime(df["created_at"], errors="coerce")
        df["event_datetime"] = _ev.fillna(_cr)
        df = df.dropna(subset=["event_datetime"])

        # 相手ごとの分布
        st.subheader("👥 相手との関係別")
        rel_counts = df["relationship"].value_counts()
        if not rel_counts.empty:
            st.bar_chart(rel_counts)
        else:
            st.caption("相手情報がまだ記録されていません。")

        # 伝えられたか／伝えなかったか
        st.subheader("🗣 伝えられたか")
        said_df = df.dropna(subset=["outcome_said"])
        if said_df.empty:
            st.caption(
                "まだ事後フィードバックの記録がありません。"
                "対話完了後の「📝 実際に伝えてみたら教えてください」から記録できます。"
            )
        else:
            said_counts = said_df["outcome_said"].map(
                {1: "伝えられた", 0: "伝えなかった"}
            ).value_counts()
            st.bar_chart(said_counts)
            total = len(said_df)
            said_rate = int((said_df["outcome_said"] == 1).sum() / total * 100)
            st.caption(f"伝えられた率：{said_rate}%（{total}件中）")

        st.divider()

        # 傾向診断（記録が3件以上で有効）
        st.subheader("🔎 自己表現の傾向")
        if len(df) < 3:
            st.info(
                f"傾向診断は記録**3件以上**で有効になります。"
                f"現在：{len(df)}件（あと {max(0, 3 - len(df))}件）"
            )
        else:
            if st.button("💡 傾向を診断する"):
                with st.spinner("傾向を分析しています..."):
                    # 直近10件を要約してHaikuに投げる
                    recent = df.tail(10)
                    lines = []
                    for _, r in recent.iterrows():
                        dt = r["event_datetime"].strftime("%m/%d")
                        lines.append(f"### {dt}")
                        if r.get("situation"):
                            lines.append(f"- 出来事: {r['situation']}")
                        if r.get("thoughts"):
                            lines.append(f"- 思ったこと: {r['thoughts']}")
                        if r.get("why_unpleasant"):
                            lines.append(f"- 嫌だった理由: {r['why_unpleasant']}")
                        try:
                            chosen = json.loads(r.get("chosen_script") or "null")
                            if chosen and chosen.get("step2_empathy_feeling"):
                                lines.append(
                                    f"- 採用したセリフ（{chosen.get('tone_label','')}）: "
                                    f"{chosen.get('step2_empathy_feeling','')}"
                                )
                        except Exception:
                            pass
                        if r.get("outcome_said") is not None:
                            said = "伝えた" if r["outcome_said"] == 1 else "伝えなかった"
                            lines.append(f"- 結果: {said}")
                        lines.append("")
                    summary = "\n".join(lines)
                    diag = diagnose_tendency(summary)
                if diag:
                    st.markdown(f"### {diag.get('tendency_label', '')}")
                    scores = diag.get("scores", {})
                    c1, c2, c3 = st.columns(3)
                    c1.metric("非主張スコア", f"{scores.get('non_assertive', 0)}/10")
                    c2.metric("バランススコア", f"{scores.get('assertive', 0)}/10")
                    c3.metric("攻撃スコア", f"{scores.get('aggressive', 0)}/10")
                    st.markdown("**パターン**")
                    st.write(diag.get("pattern", ""))
                    if diag.get("strengths"):
                        st.markdown("**💪 あなたの強み**")
                        for s in diag["strengths"]:
                            st.markdown(f"- {s}")
                    if diag.get("growth_edges"):
                        st.markdown("**🌱 次に伸ばせそうなポイント**")
                        for g in diag["growth_edges"]:
                            st.markdown(f"- {g}")
                    st.caption(
                        "※ これは傾向の一つの見方です。決めつけず、気づきの材料として。"
                    )
                else:
                    st.warning("診断に失敗しました。少し時間を置いて試してみてください。")


# ----------------------------------------------------------------------------
# 週次レポート
# ----------------------------------------------------------------------------
elif view == "📝 週次レポート":
    st.markdown("### 週次レポート")
    st.caption("1週間のアサーション記録を Claude に要約してもらいます。")

    df_all = load_records()
    if not df_all.empty:
        _ev = pd.to_datetime(df_all["event_datetime"], errors="coerce")
        _cr = pd.to_datetime(df_all["created_at"], errors="coerce")
        df_all["event_datetime"] = _ev.fillna(_cr)
        df_all = df_all.dropna(subset=["event_datetime"])

    week_start, week_end = current_week_range()
    st.subheader(f"📅 今週（{week_start.strftime('%m/%d')}〜{week_end.strftime('%m/%d')}）")

    df_week = filter_week(df_all, week_start, week_end)
    cached = load_weekly_report(week_start)

    if df_week.empty:
        st.info("今週はまだ記録がありません。")
    else:
        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.caption(f"今週の記録数：**{len(df_week)}件**")
        with col_b:
            btn_label = "🔄 再生成" if cached else "📝 レポートを作成"
            if st.button(btn_label, use_container_width=True, type="primary"):
                with st.spinner("Claude が振り返っています..."):
                    try:
                        md = generate_weekly_report(df_week, anthropic_client)
                        save_weekly_report(week_start, week_end, md, len(df_week))
                        st.rerun()
                    except Exception as e:
                        st.error(f"生成に失敗：{e}")

        if cached:
            gen = pd.to_datetime(cached["generated_at"]).strftime("%Y-%m-%d %H:%M")
            st.caption(f"生成日時：{gen}　｜　対象記録：{cached['n_records']}件")
            st.divider()
            st.markdown(cached["markdown"])

    st.divider()
    st.subheader("📚 過去のレポート")
    past = load_all_weekly_reports()
    past = past[past["week_start"] != str(week_start)] if not past.empty else past
    if past.empty:
        st.caption("（まだ過去のレポートはありません）")
    else:
        for _, r in past.iterrows():
            ws = pd.to_datetime(r["week_start"]).strftime("%m/%d")
            we = pd.to_datetime(r["week_end"]).strftime("%m/%d")
            with st.expander(f"📝 {ws}〜{we}（{r['n_records']}件）"):
                st.markdown(r["markdown"])


# ----------------------------------------------------------------------------
# 過去の記録
# ----------------------------------------------------------------------------
elif view == "📖 過去の記録":
    st.markdown("### 過去の記録")
    df = load_records()
    if df.empty:
        st.info("まだ記録がありません。")
    else:
        _ev = pd.to_datetime(df["event_datetime"], errors="coerce")
        _cr = pd.to_datetime(df["created_at"], errors="coerce")
        df["event_datetime"] = _ev.fillna(_cr)
        df = df.dropna(subset=["event_datetime"])
        recent = df.sort_values("event_datetime", ascending=False)

        for _, row in recent.iterrows():
            dt_str = row["event_datetime"].strftime("%Y-%m-%d %H:%M")
            rel = row.get("relationship") or "相手不明"
            mode_name = MODE_CONFIGS.get(row.get("mode"), {}).get(
                "display_name", row.get("mode", "")
            )
            title = f"📝 {dt_str}｜{rel}"

            with st.expander(title):
                st.caption(f"モード：{mode_name}")
                st.markdown("**🌱 出来事**")
                st.write(row.get("situation") or "（未記録）")

                if row.get("thoughts"):
                    st.markdown("**💭 思ったこと**")
                    st.write(row["thoughts"])

                if row.get("why_unpleasant"):
                    st.markdown("**❓ なぜ嫌だったか**")
                    st.write(row["why_unpleasant"])

                if row.get("other_fault"):
                    st.markdown("**⚡ 相手の非**")
                    st.write(row["other_fault"])
                if row.get("other_reason"):
                    st.markdown("**🤝 相手の事情**")
                    st.write(row["other_reason"])
                if row.get("self_fault"):
                    st.markdown("**🪞 自分の非**")
                    st.write(row["self_fault"])

                try:
                    chosen = json.loads(row.get("chosen_script") or "null")
                except Exception:
                    chosen = None
                if chosen:
                    st.markdown("**🗣 採用したセリフ**")
                    st.info(
                        f"**{chosen.get('tone_label','')}**\n\n"
                        f"{chosen.get('step2_empathy_feeling','')}\n\n"
                        f"{chosen.get('step3_suggestion','')}"
                    )

                if row.get("outcome_said") is not None:
                    said = "✓ 伝えられた" if row["outcome_said"] == 1 else "— 伝えなかった"
                    st.markdown(f"**結果**：{said}")
                    if row.get("outcome_result"):
                        st.write(f"詳細：{row['outcome_result']}")

                if row.get("todo"):
                    st.markdown("**✍ TODO**")
                    st.write(row["todo"])
                if row.get("insight"):
                    st.markdown("**💡 気づき**")
                    st.write(row["insight"])
