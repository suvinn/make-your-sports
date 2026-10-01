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
def test_step1_tier_hierarchy():
    # 목 1순위, 금 2순위: 1순위 주(1) > 1순위 보조(2) > 2순위 주(3) > 2순위 보조(4)
    pool = score_sports(MAPPING, [["목"], ["금"]], 2)
    tier = {p["sport"]: p["tier"] for p in pool}
    assert tier["요가"] == 1                 # 목(주)
    assert tier["필라테스"] == 2             # 토(주)·목(보조)
    assert tier["테니스"] == 3               # 금(주)
    assert tier["골프"] == 4                 # 토(주)·금(보조)
    # 1순위 보조(댄스)와 2순위 주(테니스)가 더 이상 동점이 아님
    assert tier["댄스"] < tier["테니스"]
    order = [p["tier"] for p in pool]
    assert order == sorted(order)


def test_step1_rank1_only_excludes_rank2():
    pool = score_sports(MAPPING, [["목"], ["금"]], 1)
    assert "테니스" not in {p["sport"] for p in pool}


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


def test_empty_region_widens_to_neighbors_first():
    res = recommend({"year": 2000, "month": 1, "day": 13, "hour": 12, "minute": 0},
                    {"sido": "부산광역시", "sigungu": ["북구"], "days": "weekend"})
    assert res["fallback"]["level"] == "nearby" and res["step2"]["picks"]
    assert all(p["class"]["sido"] == "부산광역시" for p in res["step2"]["picks"])
    assert all(set(p["class"]["weekdays"]) <= set("토일") for p in res["step2"]["picks"])   # 요일 조건 유지


def test_region_without_data_falls_back_nationwide_by_distance():
    res = recommend({"year": 2000, "month": 1, "day": 13, "hour": 12, "minute": 0}, {"sido": "광주광역시"})
    assert res["fallback"]["level"] == "nationwide" and res["step2"]["picks"]
    assert all(p["class"]["distance_km"] is not None for p in res["step2"]["picks"])


def test_no_fallback_when_results_exist():
    res = recommend({"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 0},
                    {"sido": "서울특별시", "sigungu": ["마포구"]})
    assert res["fallback"] is None


def test_zero_price_excluded_under_budget():
    con = sqlite3.connect(DB_PATH)
    rows = query_classes(con, ["수영", "필라테스"], Conditions(sido="서울특별시", max_price=50000))
    assert rows and all(r["price"] and 0 < r["price"] <= 50000 for r in rows)


def test_zero_price_kept_as_uncertain_without_budget():
    con = sqlite3.connect(DB_PATH)
    rows = query_classes(con, ["수영"], Conditions(sido="서울특별시", sigungu=["영등포구"]))
    zero = [r for r in rows if r["price"] == 0]
    assert zero and all("가격" in r["uncertain"] for r in zero)


def test_near_search_sorted_by_distance_within_radius():
    res = recommend({"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 0},
                    Conditions(near_lat=37.5551, near_lon=126.9368, near_label="신촌"))
    picks = res["step2"]["picks"]
    assert picks
    r = res["conditions"]["radius_km"]
    assert all(p["class"]["distance_km"] is not None and p["class"]["distance_km"] <= r for p in picks)
    assert "근처 반경" in res["step2"]["notes"][0]


@pytest.mark.parametrize("sort,field", [("price", "price"), ("time", "start_time")])
def test_sort_orders_cards_with_missing_values_last(sort, field):
    res = recommend({"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 0},
                    {"sido": "서울특별시", "days": "화목"}, sort=sort)
    vals = [p["class"][field] for p in res["step2"]["picks"]]
    known = [v for v in vals if v]
    assert known == sorted(known)
    assert vals[:len(known)] == known          # 값 없는 강좌는 뒤로


def test_sort_does_not_change_which_sports():
    b = {"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 0}
    c = {"sido": "서울특별시", "days": "화목"}
    a = {p["sport"] for p in recommend(b, c)["step2"]["picks"]}
    assert a == {p["sport"] for p in recommend(b, c, sort="price")["step2"]["picks"]}
