"""対話・セリフ生成・傾向診断の中核。"""
import os
import json
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from prompts import (
    MODE_CONFIGS, DEFAULT_MODE,
    CRISIS_KEYWORDS, ABUSE_KEYWORDS,
    CRISIS_RESPONSE, ABUSE_ADVISORY,
    SCRIPT_GENERATION_SYSTEM, TENDENCY_DIAGNOSIS_SYSTEM,
)
from risk import score_risk, is_crisis, is_abuse

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH, override=False)

# 優先順位：Streamlit Cloud secrets → 環境変数 → .env
api_key = None
try:
    import streamlit as st  # type: ignore
    api_key = st.secrets.get("ANTHROPIC_API_KEY")
except Exception:
    pass
if not api_key:
    api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    raise RuntimeError(
        "ANTHROPIC_API_KEY が見つかりません。"
        f"Streamlit の secrets.toml または {ENV_PATH} を確認してください。"
    )

client = Anthropic(api_key=api_key)

MODEL = "claude-sonnet-4-5"
HAIKU = "claude-haiku-4-5-20251001"


# ============================================================================
# 危機検知
# ============================================================================
def check_crisis_keywords(text: str) -> bool:
    return any(kw in text for kw in CRISIS_KEYWORDS)


def check_abuse_keywords(text: str) -> bool:
    return any(kw in text for kw in ABUSE_KEYWORDS)


def assess_risk(user_message: str) -> dict:
    """キーワード即時判定 + LLMスコアリングの二層防御。

    戻り値:
      - triggered: bool
      - level: "crisis" | "abuse" | None
      - source: "keyword" | "llm" | None
      - score: dict
    """
    # レイヤー1：キーワード
    if check_crisis_keywords(user_message):
        return {
            "triggered": True, "level": "crisis", "source": "keyword",
            "score": {"self_harm": 10, "harm_to_others": 0, "abuse": 0,
                      "acute": 10, "overall": 10,
                      "reasoning": "希死念慮キーワードが一致"},
        }
    if check_abuse_keywords(user_message):
        return {
            "triggered": True, "level": "abuse", "source": "keyword",
            "score": {"self_harm": 0, "harm_to_others": 0, "abuse": 8,
                      "acute": 5, "overall": 8,
                      "reasoning": "ハラスメント・暴力キーワードが一致"},
        }

    # レイヤー2：LLM
    score = score_risk(user_message, client)
    if is_crisis(score):
        return {"triggered": True, "level": "crisis", "source": "llm", "score": score}
    if is_abuse(score):
        return {"triggered": True, "level": "abuse", "source": "llm", "score": score}
    return {"triggered": False, "level": None, "source": None, "score": score}


# ============================================================================
# 対話
# ============================================================================
def chat(messages: list[dict], mode: str = DEFAULT_MODE) -> str:
    """アサーション対話の1ターン応答。"""
    # 最新ユーザー発言の危機チェックは呼び出し側で済ませる想定
    config = MODE_CONFIGS.get(mode, MODE_CONFIGS[DEFAULT_MODE])
    system_prompt = config["system_prompt"]

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=messages,
    )
    return response.content[0].text


# ============================================================================
# セリフ3案生成（ユーザーがオプトインで呼ぶ）
# ============================================================================
def generate_scripts(messages: list[dict]) -> list[dict]:
    """会話コンテキストからアサーティブなセリフ3案を生成。失敗時は空リスト。"""
    ask = {
        "role": "user",
        "content": "ここまでの対話を踏まえて、相手に伝えるアサーティブなセリフを"
                   "3案（やさしめ／バランス／しっかり）用意してください。",
    }
    try:
        resp = client.messages.create(
            model=HAIKU,
            max_tokens=2000,
            system=SCRIPT_GENERATION_SYSTEM,
            messages=messages + [ask],
        )
        raw = resp.content[0].text if resp.content else ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        scripts = data.get("scripts") or []
        # 簡易バリデーション
        return [
            s for s in scripts
            if isinstance(s, dict) and s.get("step2_empathy_feeling")
        ][:3]
    except Exception:
        return []


# ============================================================================
# 自己表現傾向の診断
# ============================================================================
def diagnose_tendency(records_summary: str) -> dict | None:
    """複数の記録サマリから、自己表現の傾向を診断する。

    records_summary: 直近N件の「出来事・思ったこと・選んだセリフ」を整形した文字列
    戻り値: {tendency, tendency_label, scores, pattern, strengths, growth_edges}
    失敗時は None。
    """
    try:
        resp = client.messages.create(
            model=HAIKU,
            max_tokens=800,
            system=TENDENCY_DIAGNOSIS_SYSTEM,
            messages=[{"role": "user", "content": records_summary}],
        )
        raw = resp.content[0].text if resp.content else ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception:
        return None
