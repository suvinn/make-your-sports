"""
오행 기반 1·2단계 추천

1단계(종목 후보): 사주 부족 오행 → ohaeng_sports.json의 주/보조 오행으로 종목 점수화
2단계(실제 강좌): 자연어에서 뽑은 조건(Conditions)으로 programs.sqlite 필터링 → 종목당 강좌 1개

규칙 원문은 data/mapping/ohaeng_sports.json의 recommend_rules 참고.

CLI 예시:
    python -m core.recommend --birth 2000-06-24 --time 12:30 --birth-sido 서울특별시 \
        --sido 서울특별시 --sigungu 마포구 --days weekday --bands 저녁 --max-price 50000
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path

from core.saju import compute_saju, SajuResult

ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = ROOT / "data" / "mapping" / "ohaeng_sports.json"
DB_PATH = ROOT / "data" / "processed" / "programs.sqlite"

MAX_SPORTS = 5          # 최종 추천 종목 수 상한
MIN_SPORTS = 3          # 이보다 적으면 다음 순위 오행으로 확장
DAY_PRESETS = {"weekday": "월화수목금", "weekend": "토일"}
TIME_BANDS = ["새벽", "오전", "오후", "저녁"]


def load_mapping(path=MAPPING_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# 1단계: 종목 후보
# --------------------------------------------------------------------------- #
def score_sports(mapping, ranks, upto_rank):
    """
    ranks: SajuResult.deficiency_ranks (적은 순, 동률 묶음)
    upto_rank: 몇 순위 부족 오행까지 볼지 (1부터)

    우선순위 계층(tier, 작을수록 우선):
        1 = 1순위 부족 오행의 주 종목
        2 = 1순위 부족 오행의 보조 종목
        3 = 2순위 부족 오행의 주 종목
        4 = 2순위 부족 오행의 보조 종목 ... (n순위: 주=2n-1, 보조=2n)
    한 종목이 여러 계층에 걸리면 가장 우선인 계층만 사용.
    보조 종목을 허용하는 이유: 목적이 '부족 오행과 완전히 일치하는 종목만 보여주기'가 아니라
    '부족 오행을 기준으로 취미를 탐색'하게 하는 것이고, 보조 오행도 그 오행을 반영하는 후보이기 때문.
    """
    best = {}
    for r in range(1, min(upto_rank, len(ranks)) + 1):
        elems = set(ranks[r - 1])
        for sport, info in mapping["sports"].items():
            if info["primary"] in elems:
                tier, role, elem = 2 * r - 1, "주", info["primary"]
            elif info.get("secondary") in elems:
                tier, role, elem = 2 * r, "보조", info["secondary"]
            else:
                continue
            if sport not in best or tier < best[sport]["tier"]:
                best[sport] = {"sport": sport, "tier": tier, "rank": r, "role": role, "element": elem,
                               "via": f"{r}순위 부족 오행 {elem}({role})",
                               "primary": info["primary"], "secondary": info.get("secondary"),
                               "reason": info["reason"]}
    # 명시적 tie-break: 계층 → 종목명(재현성). 같은 계층 안에서는 2단계가 '강좌 있는 종목'을 먼저 채택
    return sorted(best.values(), key=lambda x: (x["tier"], x["sport"]))


def step1_candidates(mapping, saju: SajuResult):
    """1순위 부족 오행으로 풀을 만들고, 3개 미만이면 2순위까지 확장. 표시용 후보는 상위 5개."""
    upto = 1
    pool = score_sports(mapping, saju.deficiency_ranks, upto)
    while len(pool) < MIN_SPORTS and upto < len(saju.deficiency_ranks):
        upto += 1
        pool = score_sports(mapping, saju.deficiency_ranks, upto)
    return {"upto_rank": upto, "pool": pool, "candidates": pool[:MAX_SPORTS]}


# --------------------------------------------------------------------------- #
# 2단계: 실제 강좌
# --------------------------------------------------------------------------- #
@dataclass
class Conditions:
    """자연어 파싱 결과가 들어오는 자리. 모든 필드는 선택(None이면 필터 안 함)."""
    sido: str | None = None                 # "서울특별시"
    sigungu: list[str] | None = None        # ["마포구", "서대문구"]
    days: str | None = None                 # 사용자가 '참석 가능한' 요일 집합(희망 요일 아님)
                                            # "weekday"(평일 가능) | "weekend"(주말 가능) | "화목"(화·목만 가능)
                                            # 강좌의 모든 요일이 이 집합 안에 있어야 매칭됨
    time_bands: list[str] | None = None     # ["저녁"] (새벽/오전/오후/저녁)
    start_after: str | None = None          # "19:00" 이후 시작
    start_before: str | None = None         # "21:00" 이전 시작
    max_price: int | None = None            # 원
    adult: bool = True                      # 성인 사용자면 아동 전용 강좌 제외
    near_lat: float | None = None           # 사용자가 말한 장소의 대략 좌표(LLM) — 거리 표시·근처 검색용
    near_lon: float | None = None
    near_label: str | None = None           # "신림", "서대문구" 등 원래 이름
    radius_km: float | None = None          # 근처 검색 반경(시군구 조건이 없을 때만 필터로 사용)

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        if isinstance(known.get("sigungu"), str):
            known["sigungu"] = [known["sigungu"]]
        return cls(**known)


NEAR_RADII = [4, 8, 15]  # 근처 검색 반경 단계(km)


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _available_days(days):
    if not days:
        return None
    return DAY_PRESETS.get(days, "".join(c for c in "월화수목금토일" if c in days)) or None


def query_classes(con, sports, cond: Conditions):
    """조건에 맞는 강좌 전부(종목 제한). 불확실 필드는 제외하지 않고 uncertain 목록에 표시."""
    if not sports:
        return []
    sql = ["SELECT * FROM programs WHERE sport IN (%s)" % ",".join("?" * len(sports))]
    args = list(sports)
    if cond.sido:
        sql.append("AND sido = ?")
        args.append(cond.sido)
    if cond.sigungu:
        sql.append("AND sigungu IN (%s)" % ",".join("?" * len(cond.sigungu)))
        args += cond.sigungu
    if cond.max_price is not None:
        # 0원은 무료가 아니라 시설 단위 가격 미입력으로 확인됨(0원 강좌 시설의 93%가 가격 정보 자체가 없음)
        # → NULL과 마찬가지로 예산 조건을 확인할 수 없으므로 제외
        sql.append("AND price IS NOT NULL AND price > 0 AND price <= ?")
        args.append(cond.max_price)
    if cond.adult:
        sql.append("AND (adult_ok IS NULL OR adult_ok = 1)")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(" ".join(sql), args)]

    avail = _available_days(cond.days)
    has_near = cond.near_lat is not None and cond.near_lon is not None
    out = []
    for r in rows:
        uncertain = []
        r["distance_km"] = None
        if has_near and r["lat"] and r["lon"]:
            r["distance_km"] = round(haversine_km(cond.near_lat, cond.near_lon, r["lat"], r["lon"]), 1)
        if has_near and cond.radius_km and not cond.sigungu:
            if r["distance_km"] is None or r["distance_km"] > cond.radius_km:
                continue
        # 요일: 강좌 요일이 전부 가능 요일 안에 있어야 함(월수금 강좌는 화목만 되는 사람에게 불가)
        if avail:
            if not r["weekdays"]:
                continue  # 요일 없는 강좌는 60건뿐이라 제외
            if any(c not in avail for c in r["weekdays"]):
                continue
        # 시간대
        if cond.time_bands:
            if r["time_band"] is None:
                uncertain.append("시간")
            elif r["time_band"] not in cond.time_bands:
                continue
        if cond.start_after or cond.start_before:
            if r["start_time"] is None:
                if "시간" not in uncertain:
                    uncertain.append("시간")
            else:
                if cond.start_after and r["start_time"] < cond.start_after:
                    continue
                if cond.start_before and r["start_time"] > cond.start_before:
                    continue
        if cond.adult and r["adult_ok"] is None:
            uncertain.append("대상")
        if not r["price"]:
            uncertain.append("가격")  # 0원/NULL = 가격 미입력(예산 조건이 없을 때만 여기까지 옴)
        r["uncertain"] = uncertain
        out.append(r)
    return out


def _class_sort_key(r):
    # 불확실 필드 적은 순 → (근처 검색이면) 가까운 순 → 가격 낮은 순 → 도보 시간 짧은 순
    dist = r.get("distance_km")
    return (len(r["uncertain"]),
            dist if dist is not None else 10**6,
            r["price"] if r["price"] else 10**9,  # 0원/NULL은 가장 비싼 것처럼 뒤로
            r["walk_min"] if r["walk_min"] is not None else 10**6)


def _public_class(r):
    keys = ["id", "facility_name", "sido", "sigungu", "address", "lat", "lon", "nearest_stop", "walk_min",
            "program_name", "target_raw", "weekdays", "start_time", "end_time", "time_band",
            "begin_date", "end_date", "capacity", "price", "homepage", "tel", "uncertain", "distance_km"]
    return {k: r.get(k) for k in keys}


def step2_classes(mapping, saju: SajuResult, step1, cond: Conditions, con):
    """
    1단계 풀을 우선순위(계층)순으로 훑으며 조건에 맞는 강좌가 있는 종목만 채택(최대 5개, 종목당 1강좌).
    채택 종목이 3개 미만이면 다음 순위 부족 오행으로 풀을 넓혀 다시 훑는다.
    """
    upto = step1["upto_rank"]
    pool = step1["pool"]
    notes = []
    while True:
        classes = query_classes(con, [p["sport"] for p in pool], cond)
        by_sport = {}
        for c in classes:
            by_sport.setdefault(c["sport"], []).append(c)
        picks = []
        for p in pool:  # 계층순 → 같은 계층이면 강좌 있는 종목이 자연스럽게 먼저 채택됨
            cs = by_sport.get(p["sport"])
            if not cs:
                continue
            cs.sort(key=_class_sort_key)
            picks.append({**p, "class": _public_class(cs[0]), "more_count": len(cs) - 1,
                          "alternatives": [_public_class(x) for x in cs[1:21]]})  # 같은 조건의 다른 강좌(최대 20)
            if len(picks) >= MAX_SPORTS:
                break
        if len(picks) >= MIN_SPORTS or upto >= len(saju.deficiency_ranks):
            break
        upto += 1
        pool = score_sports(mapping, saju.deficiency_ranks, upto)

    if upto > step1["upto_rank"]:
        added = "·".join(e for r in saju.deficiency_ranks[step1["upto_rank"]:upto] for e in r)
        notes.append(f"조건에 맞는 강좌가 부족해 {added} 기운의 종목까지 넓혀서 찾았어요.")
    if not picks:
        notes.append("조건에 맞는 강좌를 찾지 못했어요. 지역·요일·시간·예산 중 하나를 넓혀보세요.")
    elif len(picks) < MIN_SPORTS:
        notes.append("조건에 맞는 강좌가 적어요. 지역·요일·시간·예산 중 하나를 넓혀보세요.")
    if not cond.sido and not cond.sigungu:
        notes.append("지역 조건이 없어 전국 강좌에서 골랐어요.")
    return {"upto_rank": upto, "picks": picks, "notes": notes}


# --------------------------------------------------------------------------- #
# 통합
# --------------------------------------------------------------------------- #
def recommend(birth: dict, conditions: dict | Conditions, db_path=DB_PATH, mapping=None):
    """
    birth: {"year": 2000, "month": 6, "day": 24, "hour": 12, "minute": 30, "birth_sido": "서울특별시"}
           hour를 모르면 None
    conditions: Conditions 또는 dict(LLM 파싱 결과)
    """
    mapping = mapping or load_mapping()
    cond = conditions if isinstance(conditions, Conditions) else Conditions.from_dict(conditions)
    saju = compute_saju(birth["year"], birth["month"], birth["day"], birth.get("hour"),
                        birth.get("minute", 0), birth.get("birth_sido"))
    s1 = step1_candidates(mapping, saju)
    con = sqlite3.connect(db_path)
    try:
        if cond.near_lat is not None and not cond.sigungu:
            # 데이터에 없는 지역 → 좌표 근처에서 찾기. 반경을 먼저 넓히고(4→8→15km), 그래도 부족하면 오행 확장
            for i, radius in enumerate(NEAR_RADII):
                cond.radius_km = radius
                s2 = step2_classes(mapping, saju, s1, cond, con)
                if len(s2["picks"]) >= MIN_SPORTS or i == len(NEAR_RADII) - 1:
                    break
            s2["notes"].insert(0, f"‘{cond.near_label}’ 근처 반경 {cond.radius_km:g}km 안의 강좌를 가까운 순으로 골랐어요.")
            s2["notes"] = [n for n in s2["notes"] if not n.startswith("지역 조건이 없어")]
        else:
            s2 = step2_classes(mapping, saju, s1, cond, con)
    finally:
        con.close()
    first = saju.deficiency_ranks[0]
    return {
        "saju": saju.to_dict(),
        "message": [mapping["elements"][e]["boost_message"] for e in first],
        "disclaimer": mapping["disclaimer"],
        "step1": {"upto_rank": s1["upto_rank"],
                  "candidates": [{k: c[k] for k in ("sport", "tier", "role", "element", "via", "reason")}
                                 for c in s1["candidates"]]},
        "step2": s2,
        "conditions": asdict(cond),
    }


def list_regions(db_path=DB_PATH):
    """데이터에 있는 시도→시군구 목록(자연어 파싱 결과 검증·LLM 도구용)"""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT sido, sigungu, COUNT(*) FROM programs GROUP BY sido, sigungu").fetchall()
    finally:
        con.close()
    out = {}
    for sido, sgg, n in rows:
        out.setdefault(sido, {})[sgg] = n
    return out


def _cli():
    ap = argparse.ArgumentParser(description="사주 기반 체육 강좌 추천 (1·2단계)")
    ap.add_argument("--birth", required=True, help="양력 생년월일 YYYY-MM-DD")
    ap.add_argument("--time", help="태어난 시각 HH:MM (모르면 생략)")
    ap.add_argument("--birth-sido", default="서울특별시")
    ap.add_argument("--sido")
    ap.add_argument("--sigungu", nargs="*")
    ap.add_argument("--days", help="weekday | weekend | 월수금 등")
    ap.add_argument("--bands", nargs="*", choices=TIME_BANDS)
    ap.add_argument("--start-after")
    ap.add_argument("--start-before")
    ap.add_argument("--max-price", type=int)
    ap.add_argument("--child", action="store_true", help="아동 사용자(성인 필터 해제)")
    a = ap.parse_args()

    y, m, d = map(int, a.birth.split("-"))
    hh, mm = (map(int, a.time.split(":")) if a.time else (None, 0))
    birth = {"year": y, "month": m, "day": d, "hour": hh, "minute": mm, "birth_sido": a.birth_sido}
    cond = Conditions(sido=a.sido, sigungu=a.sigungu, days=a.days, time_bands=a.bands,
                      start_after=a.start_after, start_before=a.start_before,
                      max_price=a.max_price, adult=not a.child)
    res = recommend(birth, cond)

    s = res["saju"]
    print("사주:", " ".join(f"{k}:{v}" for k, v in s["pillars"].items() if v), "| 오행:", s["counts"])
    print("부족 순위:", " > ".join("·".join(r) for r in s["deficiency_ranks"]))
    for msg in res["message"]:
        print("풀이:", msg)
    print("\n[1단계 종목 후보]")
    for c in res["step1"]["candidates"]:
        print(f"  {c['sport']} (계층 {c['tier']}, {c['via']})")
    print("\n[2단계 추천 강좌]")
    for p in res["step2"]["picks"]:
        c = p["class"]
        price = f"{c['price']:,}원" if c["price"] else "가격 정보 없음"
        when = f"{c['weekdays'] or '?'} {c['start_time'] or c['time_band'] or '시간 미상'}"
        unc = f" ⚠ {','.join(c['uncertain'])} 확인 필요" if c["uncertain"] else ""
        print(f"  [{p['sport']}] {c['facility_name']} · {c['program_name']} · {when} · {price}"
              f" (+{p['more_count']}개 더){unc}")
    for n in res["step2"]["notes"]:
        print("  ※", n)
    print("\n" + res["disclaimer"])


if __name__ == "__main__":
    _cli()
