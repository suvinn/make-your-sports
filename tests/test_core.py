"""
실행: python -m pytest -q
"""
import sqlite3

import pytest

from core.saju import compute_saju
from core.recommend import (
    Conditions, DB_PATH, load_mapping, query_classes, recommend, score_sports, step1_candidates,
)

MAPPING = load_mapping()


# ---------------- 사주 계산 ----------------
def test_saju_matches_reference_site():
    # K-FORTUNE 만세력 결과와 대조: 2000-06-24 12:30 → 戊午/癸丑/壬午/庚辰, 목0 화2 토3 금1 수2
    s = compute_saju(2000, 6, 24, 12, 30, "서울특별시")
    assert s.pillars == {"year": "庚辰", "month": "壬午", "day": "癸丑", "hour": "戊午"}
    assert s.counts == {"목": 0, "화": 2, "토": 3, "금": 1, "수": 2}
    assert s.deficiency_ranks == [["목"], ["금"], ["화", "수"], ["토"]]
    assert s.day_element == "수"


def test_hour_unknown_uses_six_chars():
    s = compute_saju(1995, 11, 3, None)
    assert s.pillars["hour"] is None
    assert sum(s.counts.values()) == 6


@pytest.mark.parametrize("y,m,d,h,expected_year", [
    (2000, 2, 4, 10, "己卯"),   # 입춘(21:40) 이전 → 전년도
    (2000, 2, 4, 23, "庚辰"),   # 입춘 이후
    (1999, 2, 4, 10, "戊寅"),   # 입춘(16:57) 이전
    (2026, 2, 4, 10, "丙午"),   # 입춘(05:02) 이후
    (2000, 1, 15, 10, "己卯"),
])
def test_ipchun_year_boundary_fix(y, m, d, h, expected_year):
    assert compute_saju(y, m, d, h, 0).pillars["year"] == expected_year


# ---------------- 1단계 ----------------
def test_step1_primary_scores_higher_than_secondary():
    pool = score_sports(MAPPING, [["목"], ["금"]], 1)
    scores = {p["sport"]: p["score"] for p in pool}
    assert scores["요가"] == 3 and scores["필라테스"] == 2   # 필라테스는 토(주)·목(보조)
    assert "테니스" not in scores                             # 2순위는 아직 안 봄


def test_step1_candidates_capped_and_distinct():
    s = compute_saju(2000, 6, 24, 12, 30)
    c = step1_candidates(MAPPING, s)["candidates"]
    assert 3 <= len(c) <= 5
    assert len({x["sport"] for x in c}) == len(c)


def test_step1_expands_when_pool_small():
    # 1순위에 풀이 3개 미만이 되도록 가짜 매핑으로 확인
    fake = {"sports": {"수영": {"primary": "수", "secondary": None, "reason": ""},
                       "테니스": {"primary": "금", "secondary": None, "reason": ""},
                       "탁구": {"primary": "금", "secondary": None, "reason": ""}}}

    class S:  # SajuResult 대역
        deficiency_ranks = [["수"], ["금"]]
    r = step1_candidates(fake, S)
    assert r["upto_rank"] == 2 and len(r["pool"]) == 3


# ---------------- 2단계 ----------------
def test_day_filter_is_subset():
    con = sqlite3.connect(DB_PATH)
    rows = query_classes(con, ["수영"], Conditions(sido="서울특별시", days="화목"))
    assert rows and all(set(r["weekdays"]) <= set("화목") for r in rows)


def test_adult_filter_excludes_kids_only():
    con = sqlite3.connect(DB_PATH)
    rows = query_classes(con, ["수영"], Conditions(sido="서울특별시"))
    assert all(r["adult_ok"] != 0 for r in rows)


def test_recommend_one_class_per_sport_and_respects_conditions():
    res = recommend({"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 30},
                    {"sido": "서울특별시", "sigungu": "마포구", "days": "weekday",
                     "time_bands": ["저녁"], "max_price": 50000})
    picks = res["step2"]["picks"]
    assert picks
    assert len({p["sport"] for p in picks}) == len(picks)
    for p in picks:
        c = p["class"]
        assert c["sigungu"] == "마포구" and c["price"] <= 50000
        assert set(c["weekdays"]) <= set("월화수목금")


def test_recommend_expands_rank_when_strict():
    # 강좌가 거의 없는 지역+조건 → 다음 순위 오행으로 넓힌 기록이 남아야 함
    res = recommend({"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 30},
                    {"sido": "충청남도", "days": "weekend"})
    assert res["step2"]["upto_rank"] > 1 or len(res["step2"]["picks"]) >= 3
    assert res["step2"]["notes"]
