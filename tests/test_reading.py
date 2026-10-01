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


def test_llm_comments_missing_sports_filled_from_mapping(monkeypatch):
    res = _res()
    picks = res["step2"]["picks"]
    monkeypatch.setattr(cr, "llm_reading", lambda *a, **k: {
        "summary": "풀이", "comments": {picks[0]["sport"]: "AI 코멘트"}, "source": "llm"})
    r = cr.make_reading(res["saju"], picks, MAPPING, api_key="x")
    assert r["comments"][picks[0]["sport"]] == "AI 코멘트"
    assert all(p["sport"] in r["comments"] for p in picks)
