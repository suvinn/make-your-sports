"""
사주 풀이 문장 생성 (LLM)

역할 분리 원칙:
- 종목 선택은 recommend.py의 결정적 규칙이 한다(같은 사주 → 같은 종목, 데이터에 있는 종목만).
- LLM은 '이미 계산된 사주'만 받아 맨 위 요약 풀이(2~3문장)를 쓴다(종목·조건과 무관 → 사주 단위 캐시).
- 종목별 설명은 ohaeng_sports.json의 why 고정 문장(recommend.explain)을 쓴다 — 일관성·표현 통제.
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


def template_reading(saju: dict, mapping: dict) -> dict:
    """LLM 없이 쓰는 기본 풀이. 부족 오행이 여럿이어도 한 문단으로 묶는다."""
    low = saju["deficiency_ranks"][0]
    els = mapping["elements"]
    low_txt = "·".join(f"{e}({els[e]['hanja']})" for e in low)
    kw = ", ".join(els[e]["keyword"].split("·")[0] for e in low)
    day = saju["day_stem"]
    summary = (f"일간 {day}은(는) {STEM_IMAGE.get(day, '')}에 비유되는 {saju['day_element']}의 기운이에요. "
               f"여덟 글자 중 {low_txt} 기운이 상대적으로 적어서, {kw}의 성격을 가진 취미로 균형을 맞춰보는 걸 추천해요.")
    return {"summary": summary, "source": "template"}


def _tool():
    return {
        "name": "write_reading",
        "description": "사주 풀이 요약을 작성한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string",
                            "description": "2~3문장. 일간의 이미지와 오행 분포(많은 기운·적은 기운)를 짚고, 어떤 성격의 취미로 균형을 맞추면 좋을지 제안."},
            },
            "required": ["summary"],
        },
    }


def _prompt(saju, mapping):
    """풀이는 사주에만 의존한다(추천 종목·조건·정렬과 무관) → 같은 사주면 언제나 같은 캐시를 쓴다."""
    avoid = ", ".join(f"'{a}'" for a in mapping["recommend_rules"]["copy_guide"]["avoid"])
    counts = ", ".join(f"{e} {n}" for e, n in saju["counts"].items())
    pillars = " ".join(f"{k}:{v}" for k, v in saju["pillars"].items() if v)
    kws = ", ".join(f"{e}={v['keyword']}" for e, v in mapping["elements"].items())
    return (
        "너는 재미로 보는 사주 앱의 풀이 작가다. 아래 계산 결과를 바탕으로 따뜻하고 가벼운 말투(해요체)로 2~3문장을 쓴다.\n"
        "규칙:\n"
        "- 계산 결과(기둥, 오행 개수, 부족 오행)를 바꾸거나 새로 계산하지 않는다.\n"
        "- 특정 종목 이름은 쓰지 않는다(종목은 화면에 따로 나온다). 부족한 오행의 상징에 맞는 '취미의 성격'만 제안한다.\n"
        "- 건강 효과, 운세 개선, 성격·성향이 맞는다는 단정, '보충할 수 있다' 같은 효과 표현은 하지 않는다. "
        f"다음 표현도 쓰지 않는다: {avoid}.\n"
        "- '사주에서는 ~로 봐요', '~해보는 건 어떨까요'처럼 해석·제안의 어조를 유지한다.\n\n"
        f"사주 기둥: {pillars}\n"
        f"일간: {saju['day_stem']} ({saju['day_element']}, 이미지: {STEM_IMAGE.get(saju['day_stem'], '')})\n"
        f"오행 개수: {counts}\n"
        f"부족 순위(적은 순): {' > '.join('·'.join(r) for r in saju['deficiency_ranks'])}\n"
        f"오행 상징: {kws}"
    )


def llm_reading(saju: dict, mapping: dict, api_key: str | None = None, model: str | None = None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=400,
        tools=[_tool()],
        tool_choice={"type": "tool", "name": "write_reading"},
        messages=[{"role": "user", "content": _prompt(saju, mapping)}],
    )
    data = next(b.input for b in msg.content if b.type == "tool_use")
    banned = mapping["recommend_rules"]["copy_guide"]["avoid"]
    text = (data.get("summary") or "").strip()
    if not text or any(b.rstrip("다.") in text for b in banned):
        raise ValueError("풀이가 비었거나 금지 표현 포함")
    return {"summary": text, "source": "llm"}


def make_reading(saju: dict, mapping: dict, api_key: str | None = None) -> dict:
    """사주 요약 풀이: LLM → 실패 시 기본 풀이. 종목별 설명은 매핑표의 고정 문장을 쓴다(여기서 만들지 않음)."""
    base = template_reading(saju, mapping)
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return base
    try:
        return llm_reading(saju, mapping, key)
    except Exception:
        return base
