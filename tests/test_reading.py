"""
사주 풀이 모듈 테스트 (LLM 호출 없이)
"""
import core.reading as cr
from core.recommend import load_mapping, recommend

MAPPING = load_mapping()


def _res():
    return recommend({"year": 2000, "month": 1, "day": 13, "hour": 12, "minute": 0}, {"sido": "서울특별시"})


def test_template_reading_single_paragraph_for_multiple_low_elements():
    res = _res()
    assert res["saju"]["deficiency_ranks"][0] == ["목", "금", "수"]
    r = cr.template_reading(res["saju"], res["step2"]["picks"], MAPPING)
    assert r["source"] == "template"
    assert r["summary"].count("사주예요") <= 1 and "목(木)·금(金)·수(水)" in r["summary"]


def test_picks_spread_across_tied_low_elements():
    picks = _res()["step2"]["picks"]
    assert {p["element"] for p in picks} == {"목", "금", "수"}


def test_make_reading_without_key_uses_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = _res()
    assert cr.make_reading(res["saju"], res["step2"]["picks"], MAPPING)["source"] == "template"


def test_llm_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network")
    monkeypatch.setattr(cr, "llm_reading", boom)
    res = _res()
    assert cr.make_reading(res["saju"], res["step2"]["picks"], MAPPING, api_key="x")["source"] == "template"


def test_static_reason_leads_with_matched_element():
    from core.recommend import explain
    info = MAPPING["sports"]["골프"]          # 토(주)·금(보조)
    txt = explain(info, "금")                 # 금이 부족해서 추천된 경우
    assert txt.startswith(info["why"]["금"]) and info["why"]["토"] in txt
    assert explain(info, "토").startswith(info["why"]["토"])


def test_every_sport_has_why_for_its_elements():
    for s, info in MAPPING["sports"].items():
        for e in (info["primary"], info.get("secondary")):
            if e:
                assert e in info["why"], (s, e)
