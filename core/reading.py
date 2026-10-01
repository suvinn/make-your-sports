"""
사주 풀이 문장 생성 (LLM)

역할 분리 원칙:
- 종목 선택은 recommend.py의 결정적 규칙이 한다(같은 사주 → 같은 종목, 데이터에 있는 종목만).
- LLM은 '이미 계산된 사주'와 '이미 고른 종목'을 엮어 읽기 좋은 풀이 문장만 쓴다.
- 실패하거나 키가 없으면 template_reading()으로 대체(서비스가 멈추지 않게).
"""
from __future__ import annotations

import os

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

STEM_IMAGE = {  # 일간 이미지(재미용 비유)
    "甲": "곧게 자라는 큰 나무", "乙": "유연한 풀과 덩굴", "丙": "환한 태양", "丁": "은은한 촛불",
    "戊": "넓은 산과 대지", "己": "부드러운 밭의 흙", "庚": "단단한 바위와 쇠", "辛": "빛나는 보석",
    "壬": "넓은 바다와 강", "癸": "맑은 이슬과 비",
}


def template_reading(saju: dict, picks: list, mapping: dict) -> dict:
    """LLM 없이 쓰는 기본 풀이. 부족 오행이 여럿이어도 한 문단으로 묶는다."""
    low = saju["deficiency_ranks"][0]
    els = mapping["elements"]
    low_txt = "·".join(f"{e}({els[e]['hanja']})" for e in low)
    kw = ", ".join(els[e]["keyword"].split("·")[0] for e in low)
    day = saju["day_stem"]
    summary = (f"일간 {day}은(는) {STEM_IMAGE.get(day, '')}에 비유되는 {saju['day_element']}의 기운이에요. "
               f"여덟 글자 중 {low_txt} 기운이 상대적으로 적어서, {kw}의 성격을 가진 취미로 균형을 맞춰보는 걸 추천해요.")
    return {"summary": summary, "comments": {p["sport"]: p["reason"] for p in picks}, "source": "template"}


def _tool():
    return {
        "name": "write_reading",
        "description": "사주 풀이 요약과 추천 종목별 한 줄 코멘트를 작성한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string",
                            "description": "2~3문장. 일간의 이미지와 오행 분포(많은 기운·적은 기운)를 짚고, 어떤 성격의 취미로 균형을 맞추면 좋을지 제안."},
                "comments": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"sport": {"type": "string"}, "comment": {"type": "string",
                                   "description": "1문장(40자 안팎). 이 사람의 사주(일간·부족 오행)와 그 종목을 연결."}},
                    "required": ["sport", "comment"]}},
            },
            "required": ["summary", "comments"],
        },
    }


def _prompt(saju, picks, mapping):
    avoid = ", ".join(f"'{a}'" for a in mapping["recommend_rules"]["copy_guide"]["avoid"])
    counts = ", ".join(f"{e} {n}" for e, n in saju["counts"].items())
    pillars = " ".join(f"{k}:{v}" for k, v in saju["pillars"].items() if v)
    lines = "\n".join(
        f"- {p['sport']}: {p['element']}({'주' if p['role'] == '주' else '보조'}) 종목 / 주 오행 {p['primary']} / 기본 설명: {p['reason']}"
        for p in picks)
    return (
        "너는 재미로 보는 사주 앱의 풀이 작가다. 아래 계산 결과를 바탕으로 따뜻하고 가벼운 말투(해요체)로 쓴다.\n"
        "규칙:\n"
        "- 계산 결과(기둥, 오행 개수, 부족 오행)를 바꾸거나 새로 계산하지 않는다.\n"
        "- 아래 목록에 있는 종목만 언급한다. 새 종목을 추천하지 않는다.\n"
        f"- 건강 효과, 운세 개선, 성격 단정은 하지 않는다. 다음 표현은 쓰지 않는다: {avoid}.\n"
        "- '사주에서는 ~로 봐요', '~해보는 건 어떨까요'처럼 해석·제안의 어조를 유지한다.\n"
        "- 보조 종목은 '그 오행 기운을 곁들인 종목'으로 설명한다(그 오행 자체의 종목이라고 하지 않는다).\n\n"
        f"사주 기둥: {pillars}\n"
        f"일간: {saju['day_stem']} ({saju['day_element']}, 이미지: {STEM_IMAGE.get(saju['day_stem'], '')})\n"
        f"오행 개수: {counts}\n"
        f"부족 순위(적은 순): {' > '.join('·'.join(r) for r in saju['deficiency_ranks'])}\n"
        f"추천 종목(이미 결정됨):\n{lines}"
    )


def llm_reading(saju: dict, picks: list, mapping: dict, api_key: str | None = None, model: str | None = None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=700,
        tools=[_tool()],
        tool_choice={"type": "tool", "name": "write_reading"},
        messages=[{"role": "user", "content": _prompt(saju, picks, mapping)}],
    )
    data = next(b.input for b in msg.content if b.type == "tool_use")
    allowed = {p["sport"] for p in picks}
    comments = {c["sport"]: c["comment"].strip() for c in data.get("comments", [])
                if c.get("sport") in allowed and c.get("comment")}
    banned = mapping["recommend_rules"]["copy_guide"]["avoid"]
    text = data.get("summary", "").strip()
    if not text or any(b.rstrip("다.") in text for b in banned):
        raise ValueError("풀이가 비었거나 금지 표현 포함")
    comments = {k: v for k, v in comments.items() if not any(b.rstrip("다.") in v for b in banned)}
    return {"summary": text, "comments": comments, "source": "llm"}


def make_reading(saju: dict, picks: list, mapping: dict, api_key: str | None = None) -> dict:
    """LLM 풀이 → 실패 시 기본 풀이. 종목 코멘트가 빠진 종목은 기본 설명으로 채움."""
    base = template_reading(saju, picks, mapping)
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key or not picks:
        return base
    try:
        r = llm_reading(saju, picks, mapping, key)
    except Exception:
        return base
    r["comments"] = {**base["comments"], **r["comments"]}
    return r
