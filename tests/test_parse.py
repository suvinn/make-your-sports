"""
규칙 기반 파서 + 지역 검증 테스트 (API 키 없이 실행됨)
"""
import pytest

from core.parse import parse_conditions, validate_conditions


def P(text):
    return parse_conditions(text, use_llm=False)


def test_basic_sentence():
    c = P("마포구에서 평일 저녁에 한 달 5만원 이하로 해보고 싶어요")["conditions"]
    assert (c.sido, c.sigungu, c.days, c.time_bands, c.max_price) == \
        ("서울특별시", ["마포구"], "weekday", ["저녁"], 50000)


def test_specific_alias_wins_over_broad():
    c = P("고양 일산 쪽 주말 오전, 3~5만원")["conditions"]
    assert c.sigungu == ["고양시 일산동구", "고양시 일산서구"] and c.max_price == 50000


def test_available_days_and_pm_time():
    c = P("화목만 가능하고 7시 이후면 좋겠어요. 영등포")["conditions"]
    assert c.days == "화목" and c.start_after == "19:00"


def test_ambiguous_sigungu_warns():
    r = P("북구에서 할 만한 거")
    assert r["conditions"].sigungu is None and r["warnings"]


def test_sport_word_is_not_region():
    assert P("수영 말고 다른 거")["conditions"].sigungu is None


def test_region_without_data_warns():
    r = P("광주에서 퇴근 후에")
    assert any("데이터가 없어요" in w for w in r["warnings"])


def test_child_detection():
    assert P("우리 아이 초등학생인데 강남구")["conditions"].adult is False


@pytest.mark.parametrize("raw,sido,sgg", [
    ({"sido": "서울", "sigungu": ["마포"]}, "서울특별시", ["마포구"]),
    ({"sigungu": ["고양시"]}, "경기도", ["고양시 덕양구", "고양시 일산동구", "고양시 일산서구"]),
    ({"sido": "서울특별시", "sigungu": ["없는구"]}, "서울특별시", None),
])
def test_validate_llm_style_output(raw, sido, sgg):
    c, _ = validate_conditions(raw)
    assert c.sido == sido and c.sigungu == sgg


# ---------------- 동 이름 / 근처 검색 ----------------
def test_neighborhood_name_maps_to_sigungu():
    c = P("신림에서 평일 저녁에 하고 싶어")["conditions"]
    assert c.sigungu == ["관악구"] and c.sido == "서울특별시"


def test_common_word_not_treated_as_neighborhood():
    assert P("대부분 평일이 좋아요")["conditions"].sigungu is None


def test_llm_place_with_data_keeps_sigungu_and_coords():
    c, w = validate_conditions({"sigungu": ["관악구"], "place": "신림", "place_lat": 37.4842, "place_lon": 126.9297})
    assert c.sigungu == ["관악구"] and c.near_lat and c.near_label == "신림" and not w


def test_llm_place_without_data_switches_to_near_search():
    c, w = validate_conditions({"sigungu": ["서대문구"], "place": "신촌", "place_lat": 37.5551, "place_lon": 126.9368})
    assert c.sigungu is None and c.sido is None and c.near_label == "신촌"
    assert any("가까운 곳" in x for x in w)


def test_invalid_coords_ignored():
    c, _ = validate_conditions({"sigungu": ["서대문구"], "place_lat": 0, "place_lon": 0})
    assert c.near_lat is None
