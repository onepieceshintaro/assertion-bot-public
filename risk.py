"""危機検知（cbt-botのパターンを流用＋ハラスメント軸を追加）。"""
import json
import re

RISK_MODEL = "claude-haiku-4-5-20251001"
CRISIS_SCORE_THRESHOLD = 7
ABUSE_SCORE_THRESHOLD = 7

RISK_SCORING_PROMPT = """あなたは、ユーザーのメッセージから**緊急性・深刻度**を評価するAIです。
0〜10のスコアで4軸を評価してください。

# 軸の定義
- self_harm: 自傷・希死念慮（0=全くない、10=今まさに実行しそう）
- harm_to_others: 他者への危害（0=ない、10=今まさに実行しそう）
- abuse: 受けているハラスメント・暴力の深刻度（0=ない、10=生命の危険）
- acute: 急性（今すぐ介入が必要な度合い）

# 出力形式
必ず以下のJSONのみ（コードブロックや説明文なし）:

{
  "self_harm": 0,
  "harm_to_others": 0,
  "abuse": 0,
  "acute": 0,
  "overall": 0,
  "reasoning": "評価の理由を1〜2文で"
}

overallはself_harm/harm_to_others/abuse/acuteの最大値。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def score_risk(user_message: str, client) -> dict:
    """発話のリスクをLLMで評価。失敗時はオールゼロ（フェイルセーフ）。"""
    default = {
        "self_harm": 0, "harm_to_others": 0, "abuse": 0,
        "acute": 0, "overall": 0, "reasoning": "",
    }
    try:
        resp = client.messages.create(
            model=RISK_MODEL,
            max_tokens=300,
            system=RISK_SCORING_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = resp.content[0].text if resp.content else ""
        data = _extract_json(raw) or {}
        return {
            "self_harm": int(data.get("self_harm", 0)),
            "harm_to_others": int(data.get("harm_to_others", 0)),
            "abuse": int(data.get("abuse", 0)),
            "acute": int(data.get("acute", 0)),
            "overall": int(data.get("overall", 0)),
            "reasoning": data.get("reasoning", ""),
        }
    except Exception:
        return default


def is_crisis(score: dict, threshold: int = CRISIS_SCORE_THRESHOLD) -> bool:
    """希死念慮・他害など、医療的危機レベルか。"""
    return (
        score.get("self_harm", 0) >= threshold
        or score.get("harm_to_others", 0) >= threshold
        or score.get("acute", 0) >= threshold
    )


def is_abuse(score: dict, threshold: int = ABUSE_SCORE_THRESHOLD) -> bool:
    """ハラスメント・暴力レベルか（アサーションで解決する範囲を超えている）。"""
    return score.get("abuse", 0) >= threshold
