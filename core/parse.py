"""
자연어 → Conditions 파싱

원칙: LLM은 사주를 판단하지 않는다. 사용자의 문장을 Conditions라는 제한된 JSON으로만 바꾼다.
- 1순위: Claude API(tool use로 스키마 강제)
- 대체: 규칙 기반 파서(API 키가 없거나 호출 실패 시 — 시연이 멈추지 않게)
- 어느 쪽이든 마지막에 validate_conditions()로 실제 데이터의 지역명과 대조해 정규화한다.

필요 환경변수: ANTHROPIC_API_KEY (선택: ANTHROPIC_MODEL)
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

import sqlite3

from core.recommend import DB_PATH, Conditions, TIME_BANDS, list_regions

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SIDO_ALIASES = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주광역시": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도",
    "광주": "광주광역시",
}
EXTRA_ALIASES = {"일산": ["고양시 일산동구", "고양시 일산서구"]}
# 줄임말로 쓰면 다른 지역과 헷갈리는 이름(예: '광주'는 보통 광주광역시를 뜻하지만 데이터엔 경기 광주시만 있음)
NO_SHORT_FORM = {"광주시", "수영구", "연수구"}  # 수영·연수(반)는 종목 용어와 겹침
SKIP_SIGUNGU = {"인천광역시"}  # 원천 데이터 입력 오류(시군구 칸에 시도명)


@lru_cache(maxsize=1)
def regions():
    return list_regions()


# --------------------------------------------------------------------------- #
# 지역 정규화 (LLM/규칙 공통)
# --------------------------------------------------------------------------- #
def _sigungu_aliases():
    """별칭 → [(sido, sigungu)] 사전. '마포'→마포구, '고양'→고양시 3개 구, '일산동구'→고양시 일산동구"""
    table = {}

    def add(alias, pair):
        table.setdefault(alias, set()).add(pair)

    for sido, sggs in regions().items():
        for sgg in sggs:
            if sgg in SKIP_SIGUNGU:
                continue
            pair = (sido, sgg)
            add(sgg, pair)
            parts = sgg.split()
            for part in parts:
                add(part, pair)
                base = re.sub(r"(시|군|구)$", "", part)
                if len(base) >= 2 and part not in NO_SHORT_FORM:
                    add(base, pair)
    for alias, names in EXTRA_ALIASES.items():
        for sido, sggs in regions().items():
            for n in names:
                if n in sggs:
                    add(alias, (sido, n))
    return table


@lru_cache(maxsize=1)
def _emd_aliases():
    """읍면동 별칭 → {(sido, sigungu)}. 데이터에 있는 동만('신림동'·'신림' → 관악구)"""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("SELECT DISTINCT sido, sigungu, emd FROM programs WHERE emd IS NOT NULL").fetchall()
    finally:
        con.close()
    table = {}
    for sido, sgg, emd in rows:
        if sgg in SKIP_SIGUNGU:
            continue
        base = re.sub(r"(동|리)$", "", re.sub(r"\d*가$", "", emd))
        for alias in {emd, base}:
            if len(alias) >= 2:
                table.setdefault(alias, set()).add((sido, sgg))
    return table


def _lookup_region(name):
    """시군구 별칭 → 동 별칭 순으로 찾기"""
    name = (name or "").strip()
    if not name:
        return set()
    sgg = _sigungu_aliases()
    hit = sgg.get(name) or sgg.get(re.sub(r"(시|군|구)$", "", name))
    if hit:
        return set(hit)
    emd = _emd_aliases()
    return set(emd.get(name) or emd.get(re.sub(r"(동|역)$", "", name)) or set())


def _valid_coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if 33.0 <= lat <= 38.9 and 124.5 <= lon <= 131.9 else None


def normalize_sido(s):
    if not s:
        return None
    s = s.strip()
    if s in regions() or s in SIDO_ALIASES.values():
        return s
    for k, v in SIDO_ALIASES.items():
        if s.startswith(k):
            return v
    return None


def validate_conditions(raw: dict) -> tuple[Conditions, list[str]]:
    """LLM/규칙 결과(dict)를 실제 데이터 기준으로 정규화. (Conditions, 경고 목록)"""
    warnings = []
    raw = dict(raw or {})
    sido = normalize_sido(raw.get("sido"))
    if raw.get("sido") and not sido:
        warnings.append(f"'{raw.get('sido')}' 지역을 인식하지 못했어요.")

    pairs = set()
    unknown = []
    sggs = raw.get("sigungu") or []
    if isinstance(sggs, str):
        sggs = [sggs]
    place = (raw.get("place") or "").strip() or None
    names = list(sggs) or ([place] if place else [])
    for name in names:
        hit = _lookup_region(name)
        if hit:
            pairs |= hit
        else:
            unknown.append(name)
    if sido:
        pairs = {p for p in pairs if p[0] == sido}

    near = _valid_coord(raw.get("place_lat"), raw.get("place_lon"))
    near_label = place or (unknown[0] if unknown else None)

    sigungu = None
    if pairs:
        sidos = {p[0] for p in pairs}
        if len(sidos) == 1:
            sido = sido or sidos.pop()
            sigungu = sorted(p[1] for p in pairs)
        else:
            warnings.append(f"같은 이름의 지역이 여러 곳이에요({', '.join(sorted(sidos))}). 시·도를 함께 알려주세요.")
    elif unknown and near:
        # 데이터에 없는 지역이지만 좌표가 있으면 근처 강좌로 찾는다(시·도 경계도 넘어서)
        warnings.append(f"‘{near_label}’에는 강좌 데이터가 없어 가까운 곳의 강좌를 거리순으로 찾았어요.")
        sido = None
    elif unknown:
        warnings.append(f"{', '.join(unknown)}의 강좌 데이터가 없어 지역 조건에서 뺐어요.")
    if sido and sido not in regions():
        warnings.append(f"{sido}에는 아직 공공체육시설 강좌 데이터가 없어요.")

    days = raw.get("days")
    if days and days not in ("weekday", "weekend"):
        days = "".join(c for c in "월화수목금토일" if c in days) or None

    bands = [b for b in (raw.get("time_bands") or []) if b in TIME_BANDS] or None

    def hhmm(v):
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(v or ""))
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m and int(m.group(1)) < 24 else None

    price = raw.get("max_price")
    try:
        price = int(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    if price is not None and price <= 0:
        price = None

    cond = Conditions(sido=sido, sigungu=sigungu, days=days, time_bands=bands,
                      start_after=hhmm(raw.get("start_after")), start_before=hhmm(raw.get("start_before")),
                      max_price=price, adult=raw.get("adult", True) is not False,
                      near_lat=near[0] if near else None, near_lon=near[1] if near else None,
                      near_label=near_label if near else None)
    return cond, warnings


# --------------------------------------------------------------------------- #
# 규칙 기반 파서 (대체용)
# --------------------------------------------------------------------------- #
DAY_RUN = re.compile(r"(?<![가-힣])([월화수목금토일](?:\s*[,·/]?\s*[월화수목금토일])+)(?=요일|만|에|이나|$|[^가-힣])")
DAY_ONE = re.compile(r"([월화수목금토일])요일")
MANWON = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*원?")
WON = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원")
HOUR_AFTER = re.compile(r"(\d{1,2})\s*시\s*(반)?\s*(?:이후|넘어|부터|쯤부터|以後)")
HOUR_BEFORE = re.compile(r"(\d{1,2})\s*시\s*(반)?\s*(?:이전|전에|전까지|까지)")
PM_HINT = re.compile(r"저녁|오후|밤|퇴근|야간")
AM_HINT = re.compile(r"오전|아침|새벽")


def _to_24h(h, text):
    h = int(h)
    if h < 12 and (PM_HINT.search(text) or (1 <= h <= 8 and not AM_HINT.search(text))):
        h += 12
    return h


def rule_parse(text: str) -> dict:
    t = text or ""
    out = {}

    # 지역
    for k, v in SIDO_ALIASES.items():
        if k in t or v in t:
            out["sido"] = v
            break
    found = [alias for alias in _sigungu_aliases() if len(alias) >= 2 and alias in t]
    # 긴 별칭이 짧은 별칭을 포함하면 긴 것만(예: '일산동구' 안의 '일산동')
    found = [a for a in found if not any(a != b and a in b for b in found)]
    table = _sigungu_aliases()
    # '고양'(3개 구)과 '일산'(2개 구)이 함께 나오면 더 좁은 '일산'만 사용
    found = [a for a in found if not any(table[b] < table[a] for b in found if b != a)]
    if found:
        out["sigungu"] = found
    else:
        # 시군구가 없으면 동 이름으로(‘대부분’ 같은 일반어 오인을 막기 위해 앞뒤 경계 확인)
        for alias in sorted(_emd_aliases(), key=len, reverse=True):
            if re.search(rf"(?<![가-힣]){re.escape(alias)}(?:동|역)?(?=$|[^가-힣]|에서|에|쪽|근처|부근|인근|사는|살아)", t):
                out["sigungu"] = [alias]
                break

    # 요일(참석 가능한 요일)
    weekday, weekend = bool(re.search(r"평일|주중", t)), "주말" in t
    if weekday and not weekend:
        out["days"] = "weekday"
    elif weekend and not weekday:
        out["days"] = "weekend"
    else:
        chars = set(DAY_ONE.findall(t))
        for run in DAY_RUN.findall(t):
            chars |= set(c for c in run if c in "월화수목금토일")
        if chars:
            out["days"] = "".join(c for c in "월화수목금토일" if c in chars)

    # 시간대
    bands = []
    for word, band in [("새벽", "새벽"), ("아침", "오전"), ("오전", "오전"), ("점심", "오후"), ("낮", "오후"),
                       ("오후", "오후"), ("저녁", "저녁"), ("퇴근", "저녁"), ("밤", "저녁"), ("야간", "저녁")]:
        if word in t and band not in bands:
            bands.append(band)
    if bands:
        out["time_bands"] = bands
    m = HOUR_AFTER.search(t)
    if m:
        out["start_after"] = f"{_to_24h(m.group(1), t):02d}:{'30' if m.group(2) else '00'}"
    m = HOUR_BEFORE.search(t)
    if m:
        out["start_before"] = f"{_to_24h(m.group(1), t):02d}:{'30' if m.group(2) else '00'}"
    if out.get("start_after") and out.get("time_bands"):
        out.pop("time_bands")  # 구체 시각이 있으면 시각 조건만 사용

    # 예산(여러 값이면 가장 큰 값을 상한으로: '3~5만원' → 5만원)
    prices = [float(x) * 10000 for x in MANWON.findall(t)] + [int(x.replace(",", "")) for x in WON.findall(t)]
    if prices:
        out["max_price"] = int(max(prices))

    # 대상
    if re.search(r"초등|유아|어린이|중학생|고등학생|우리 아이|아이가|아들|딸", t):
        out["adult"] = False
    return out


# --------------------------------------------------------------------------- #
# LLM 파서
# --------------------------------------------------------------------------- #
def _tool_schema():
    return {
        "name": "set_conditions",
        "description": "사용자 문장에서 체육 강좌 검색 조건을 추출한다. 문장에 없는 조건은 넣지 않는다(null/생략).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sido": {"type": "string", "description": "시·도 정식 명칭. 아래 지역 목록의 이름만 사용."},
                "sigungu": {"type": "array", "items": {"type": "string"},
                            "description": "시·군·구. 아래 지역 목록에 있는 이름 그대로. '고양'처럼 여러 구를 뜻하면 해당 구를 모두."},
                "place": {"type": "string",
                          "description": "사용자가 말한 동네·역·랜드마크 이름 원문(예: '신림', '홍대입구역', '잠실'). 없으면 생략."},
                "place_lat": {"type": "number", "description": "place의 대략적인 위도(WGS84). 확실하지 않으면 생략."},
                "place_lon": {"type": "number", "description": "place의 대략적인 경도(WGS84). 확실하지 않으면 생략."},
                "days": {"type": "string",
                         "description": "사용자가 '참석 가능한' 요일 집합(희망 요일 아님). 평일 가능→'weekday', "
                                        "주말 가능→'weekend', 특정 요일만 가능→요일 글자 나열(예: 화·목만→'화목'). "
                                        "평일·주말 모두 괜찮거나 언급 없으면 생략."},
                "time_bands": {"type": "array", "items": {"type": "string", "enum": TIME_BANDS},
                               "description": "새벽(7시 전)/오전(7~12시)/오후(12~18시)/저녁(18시 이후). 퇴근 후→저녁."},
                "start_after": {"type": "string", "description": "'7시 이후' 같은 구체 시작 시각 하한, 24시간 HH:MM. 저녁 맥락이면 19:00."},
                "start_before": {"type": "string", "description": "시작 시각 상한, 24시간 HH:MM."},
                "max_price": {"type": "integer", "description": "월 예산 상한(원). '5만원 이하'→50000, '3~5만원'→50000."},
                "adult": {"type": "boolean", "description": "강좌를 들을 사람이 성인이면 true, 아이(초등 등)를 위한 검색이면 false. 기본 true."},
            },
        },
    }


def _system_prompt():
    region_lines = "\n".join(f"- {sido}: {', '.join(sorted(s for s in sggs if s not in SKIP_SIGUNGU))}"
                             for sido, sggs in regions().items())
    return (
        "너는 체육 강좌 검색 조건 추출기다. 사용자의 문장을 set_conditions 도구 호출 하나로만 변환한다.\n"
        "- 사주, 운세, 종목 추천은 하지 않는다. 종목 언급은 무시한다(종목은 사주로 정한다).\n"
        "- 문장에 없는 조건은 추측하지 말고 생략한다.\n"
        "- days는 '참석 가능한 요일'이다. '화요일이나 목요일 가능', '화목만 돼요' 모두 '화목'.\n"
        "- 지역은 아래 목록(강좌 데이터가 있는 지역)의 이름을 그대로 쓴다. 목록에 없는 지역이면 사용자가 말한 이름 그대로 넣는다.\n"
        "- 동네·역·랜드마크(예: 신림, 홍대입구, 잠실, 판교)를 말하면 그곳이 속한 시·군·구를 sigungu에 넣고, "
        "원래 이름은 place에, 그 장소의 대략적인 좌표는 place_lat/place_lon에 넣는다.\n"
        f"지역 목록:\n{region_lines}"
    )


def llm_parse(text: str, api_key: str | None = None, model: str | None = None) -> dict:
    import anthropic  # 필요할 때만 import

    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=400,
        system=_system_prompt(),
        tools=[_tool_schema()],
        tool_choice={"type": "tool", "name": "set_conditions"},
        messages=[{"role": "user", "content": text}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise ValueError("도구 호출 결과가 없습니다")


def parse_conditions(text: str, api_key: str | None = None, use_llm: bool = True) -> dict:
    """
    반환: {"conditions": Conditions, "source": "llm"|"rule", "raw": dict, "warnings": [...]}
    """
    raw, source, warnings = None, "rule", []
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if use_llm and key and (text or "").strip():
        try:
            raw, source = llm_parse(text, key), "llm"
        except Exception as e:  # 네트워크/키/쿼터 오류 → 규칙 기반으로 계속
            warnings.append(f"AI 조건 분석에 실패해 기본 분석기로 처리했어요({type(e).__name__}).")
    if raw is None:
        raw = rule_parse(text)
    cond, w = validate_conditions(raw)
    return {"conditions": cond, "source": source, "raw": raw, "warnings": warnings + w}


if __name__ == "__main__":
    import sys
    from dataclasses import asdict

    r = parse_conditions(" ".join(sys.argv[1:]))
    print(json.dumps({**r, "conditions": asdict(r["conditions"])}, ensure_ascii=False, indent=2))
