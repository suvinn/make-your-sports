"""
사주 계산 (결정적 계산만 담당 — LLM에 맡기지 않음)

- 사주 4주: sajupy(MIT) 사용, 출생 시도 경도로 진태양시 보정
- 오행 분포: 천간·지지 8글자(시간 모르면 6글자)의 오행 개수
- 부족 순위: 개수가 적은 오행부터 순위를 매김(동률은 같은 순위)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import sajupy

ELEMENTS = ["목", "화", "토", "금", "수"]

ELEMENT_OF = {
    # 천간
    "甲": "목", "乙": "목", "丙": "화", "丁": "화", "戊": "토",
    "己": "토", "庚": "금", "辛": "금", "壬": "수", "癸": "수",
    # 지지
    "寅": "목", "卯": "목", "巳": "화", "午": "화",
    "辰": "토", "戌": "토", "丑": "토", "未": "토",
    "申": "금", "酉": "금", "亥": "수", "子": "수",
}

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
GANZHI_60 = [STEMS[i % 10] + BRANCHES[i % 12] for i in range(60)]

# 출생 시도별 대표 경도(시청·도청 기준, 진태양시 보정용). 분 단위 오차는 재미용 서비스에서 허용
SIDO_LONGITUDE = {
    "서울특별시": 126.978, "부산광역시": 129.075, "대구광역시": 128.601, "인천광역시": 126.705,
    "광주광역시": 126.851, "대전광역시": 127.385, "울산광역시": 129.311, "세종특별자치시": 127.289,
    "경기도": 127.009, "강원특별자치도": 127.730, "충청북도": 127.489, "충청남도": 126.660,
    "전북특별자치도": 127.108, "전라남도": 126.463, "경상북도": 128.729, "경상남도": 128.681,
    "제주특별자치도": 126.531,
}
DEFAULT_LONGITUDE = SIDO_LONGITUDE["서울특별시"]


@dataclass
class SajuResult:
    pillars: dict                  # {"year": "庚辰", "month": "壬午", "day": "癸丑", "hour": "戊午" | None}
    counts: dict                   # {"목": 0, "화": 2, "토": 3, "금": 1, "수": 2}
    deficiency_ranks: list         # [["목"], ["금"], ["화", "수"], ["토"]]  적은 순, 동률은 같은 칸
    day_stem: str                  # 일간 (예: "癸")
    day_element: str               # 일간 오행 (예: "수")
    hour_known: bool
    solar_time: str | None = None  # 보정된 진태양시 "HH:MM"
    notes: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _prev_ganzhi(gz):
    return GANZHI_60[(GANZHI_60.index(gz) - 1) % 60]


def compute_saju(year, month, day, hour=None, minute=0, birth_sido=None):
    """생년월일(양력) + 시각(모르면 None) + 출생 시도 → SajuResult"""
    hour_known = hour is not None
    lon = SIDO_LONGITUDE.get(birth_sido, DEFAULT_LONGITUDE)
    notes = []
    if birth_sido and birth_sido not in SIDO_LONGITUDE:
        notes.append(f"출생 시도 '{birth_sido}'를 찾지 못해 서울 경도로 보정했어요.")

    r = sajupy.calculate_saju(year, month, day, hour if hour_known else 12, minute if hour_known else 0,
                              longitude=lon, use_solar_time=True)
    pillars = {"year": r["year_pillar"], "month": r["month_pillar"], "day": r["day_pillar"],
               "hour": r["hour_pillar"] if hour_known else None}

    # sajupy 0.2.0 보정: 입춘 당일 입춘 시각 이전 출생이면 월주(丑월)는 맞지만 연주가 다음 해로 넘어가 있음.
    # 양력 2월인데 월지가 丑이면 아직 입춘 전이므로 연주를 한 칸 되돌린다.
    if month == 2 and pillars["month"][1] == "丑":
        fixed = _prev_ganzhi(pillars["year"])
        if fixed != pillars["year"]:
            pillars["year"] = fixed

    chars = [c for p in pillars.values() if p for c in p]
    counts = {e: 0 for e in ELEMENTS}
    for c in chars:
        counts[ELEMENT_OF[c]] += 1

    levels = sorted(set(counts.values()))
    ranks = [[e for e in ELEMENTS if counts[e] == lv] for lv in levels]

    solar = r.get("solar_correction") or {}
    if not hour_known:
        notes.append("태어난 시간을 몰라 시주를 빼고 6글자로 계산했어요.")
    return SajuResult(
        pillars=pillars, counts=counts, deficiency_ranks=ranks,
        day_stem=pillars["day"][0], day_element=ELEMENT_OF[pillars["day"][0]],
        hour_known=hour_known, solar_time=solar.get("solar_time") if hour_known else None, notes=notes,
    )
