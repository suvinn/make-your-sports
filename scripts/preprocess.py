"""
공공체육시설 프로그램 정보(체육종합빅데이터센터) → 추천용 SQLite 전처리

사용법:
    python scripts/preprocess.py \
        --input data/raw/KS_PUBLIC_ALSFC_PROGRM_INFO_202608.csv \
        --output data/processed/programs.sqlite \
        --as-of 2026-10-01

--as-of 날짜 이후에도 진행 중인 강좌(종료일 >= as-of)만 남긴다. 생략하면 오늘 날짜.
"""
import argparse
import json
import math
import re
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "data" / "mapping" / "sport_rules.json"

USECOLS = [
    "FCLTY_NM", "INDUTY_NM", "CTPRVN_NM", "SIGNGU_NM", "EMD_NM", "FCLTY_ADDR",
    "FCLTY_TEL_NO", "FCLTY_LA", "FCLTY_LO", "BSTP_SUBWAYST_1R_NM", "WLKG_MVMN_1R_TIME",
    "PROGRM_TY_NM", "PROGRM_NM", "PROGRM_TRGET_NM", "PROGRM_BEGIN_DE", "PROGRM_END_DE",
    "PROGRM_ESTBL_WKDAY_NM", "PROGRM_ESTBL_TIZN_VALUE", "PROGRM_RCRIT_NMPR_CO",
    "PROGRM_PRC", "HMPG_URL",
]

ADULT_PAT = re.compile(r"성인|일반|이상|전체|전연령|누구나|제한\s*없음|어르신|노인|시니어|남녀|여성|남성|"
                       r"직장인|대학생|주부|중장년|장년|엄마|아빠|부모")
KID_PAT = re.compile(r"유아|어린이|초등|아동|유치|키즈|영유아|청소년|중학|고등|중고|학생|\d{2}년생")
WEEKDAY_CHARS = "월화수목금토일"
TIME_RANGE_PAT = re.compile(r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})")
TIME_IN_NAME_PAT = re.compile(r"(?<!\d)(\d{1,2})\s*(?::|시)\s*(\d{2})?")
BAND_WORDS = [("새벽", "새벽"), ("아침", "오전"), ("오전", "오전"), ("오후", "오후"),
              ("저녁", "저녁"), ("야간", "저녁"), ("밤", "저녁")]


def _s(x):
    """NaN 등 문자열이 아닌 값을 빈 문자열로"""
    return x if isinstance(x, str) else ""


def load_rules():
    with open(RULES_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    rules = [(r["sport"], re.compile(r["pattern"], re.IGNORECASE)) for r in cfg["rules"]]
    return rules, cfg["facility_fallback"], re.compile(cfg["non_sport_pattern"])


def classify_sport(name, type_raw, facility_type, rules, fallback):
    """프로그램명 → 종목유형 → 시설 업종 순으로 표준 종목을 찾는다"""
    for text in (_s(name), _s(type_raw)):
        if not text:
            continue
        for sport, pat in rules:
            if pat.search(text):
                return sport
    return fallback.get(facility_type)


def adult_ok(target, name):
    """성인이 수강 가능한지: True / False / None(판단 불가)"""
    t = _s(target).replace("<br>", " ")
    name = _s(name)
    if t.strip():
        if ADULT_PAT.search(t):
            return True
        if KID_PAT.search(t):
            return False
        return None
    # 대상이 비어 있으면 프로그램명으로 추정
    if re.search(r"성인|어르신|시니어|직장인", name):
        return True
    if KID_PAT.search(name):
        return False
    return None


def band_of(hour):
    if hour < 7:
        return "새벽"
    if hour < 12:
        return "오전"
    if hour < 18:
        return "오후"
    return "저녁"


def parse_time(field, name):
    """(start 'HH:MM', end 'HH:MM', band, source)"""
    if isinstance(field, str):
        m = TIME_RANGE_PAT.search(field)
        if m:
            h1, m1, h2, m2 = map(int, m.groups())
            return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}", band_of(h1), "field"
    name = _s(name)
    for m in TIME_IN_NAME_PAT.finditer(name):
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        if 5 <= h <= 23 and mi < 60:
            return f"{h:02d}:{mi:02d}", None, band_of(h), "name"
    for word, band in BAND_WORDS:
        if word in name:
            return None, None, band, "word"
    return None, None, None, "none"


def parse_weekdays(field, name):
    src = field if isinstance(field, str) and any(c in field for c in WEEKDAY_CHARS) else _s(name)
    days = "".join(c for c in WEEKDAY_CHARS if c in src)
    if not days:
        if "평일" in src:
            days = "월화수목금"
        elif "주말" in src:
            days = "토일"
    return days or None


def clean_facility_name(s):
    return re.sub(r"^(\(취약시설\))+", "", _s(s)).strip()


def to_int(x):
    try:
        v = float(x)
        return None if math.isnan(v) else int(round(v))
    except (TypeError, ValueError):
        return None


def to_float(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(ROOT / "data/raw/KS_PUBLIC_ALSFC_PROGRM_INFO_202608.csv"))
    ap.add_argument("--output", default=str(ROOT / "data/processed/programs.sqlite"))
    ap.add_argument("--as-of", default=date.today().isoformat(), help="YYYY-MM-DD")
    args = ap.parse_args()

    as_of = args.as_of.replace("-", "")
    rules, fallback, non_sport = load_rules()

    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig", usecols=USECOLS)
    n_raw = len(df)
    df = df[df["PROGRM_END_DE"] >= as_of].drop_duplicates()
    n_active = len(df)

    text = (df["PROGRM_TY_NM"].fillna("") + " " + df["PROGRM_NM"].fillna(""))
    is_non_sport = text.str.contains(non_sport)
    df = df[~is_non_sport].copy()
    text = text[~is_non_sport]

    df["sport"] = [classify_sport(n, ty, ft, rules, fallback)
                   for n, ty, ft in zip(df["PROGRM_NM"], df["PROGRM_TY_NM"], df["INDUTY_NM"])]
    df["adult_ok"] = [adult_ok(t, n) for t, n in zip(df["PROGRM_TRGET_NM"], df["PROGRM_NM"])]
    times = [parse_time(f, n) for f, n in zip(df["PROGRM_ESTBL_TIZN_VALUE"], df["PROGRM_NM"])]
    df["start_time"], df["end_time"], df["time_band"], df["time_source"] = zip(*times)
    df["weekdays"] = [parse_weekdays(f, n) for f, n in zip(df["PROGRM_ESTBL_WKDAY_NM"], df["PROGRM_NM"])]

    out = pd.DataFrame({
        "facility_name": df["FCLTY_NM"].map(clean_facility_name),
        "facility_type": df["INDUTY_NM"],
        "sido": df["CTPRVN_NM"],
        "sigungu": df["SIGNGU_NM"],
        "emd": df["EMD_NM"],
        "address": df["FCLTY_ADDR"],
        "tel": df["FCLTY_TEL_NO"],
        "lat": df["FCLTY_LA"].map(to_float),
        "lon": df["FCLTY_LO"].map(to_float),
        "nearest_stop": df["BSTP_SUBWAYST_1R_NM"],
        # 원천값은 초 단위로 보임(예: 656 → 약 11분). 분으로 올림 변환
        "walk_min": df["WLKG_MVMN_1R_TIME"].map(lambda x: None if to_int(x) is None else math.ceil(to_int(x) / 60)),
        "sport": df["sport"],
        "program_type_raw": df["PROGRM_TY_NM"],
        "program_name": df["PROGRM_NM"],
        "target_raw": df["PROGRM_TRGET_NM"].str.replace("<br>", " ", regex=False),
        "adult_ok": df["adult_ok"].map({True: 1, False: 0}),
        "begin_date": pd.to_datetime(df["PROGRM_BEGIN_DE"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d"),
        "end_date": pd.to_datetime(df["PROGRM_END_DE"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d"),
        "weekdays": df["weekdays"],
        "has_weekday": df["weekdays"].map(lambda d: None if not isinstance(d, str) else int(any(c in d for c in "월화수목금"))),
        "has_weekend": df["weekdays"].map(lambda d: None if not isinstance(d, str) else int(any(c in d for c in "토일"))),
        "start_time": df["start_time"],
        "end_time": df["end_time"],
        "time_band": df["time_band"],
        "time_source": df["time_source"],
        "capacity": df["PROGRM_RCRIT_NMPR_CO"].map(to_int),
        "price": df["PROGRM_PRC"].map(to_int).map(lambda v: v if v is not None and (v == 0 or v >= 1000) else None),  # 1,000원 미만은 입력 오류로 보고 미상 처리
        "homepage": df["HMPG_URL"],
    })
    out = out[out["sport"].notna()].reset_index(drop=True)
    for col in ["walk_min", "adult_ok", "has_weekday", "has_weekend", "capacity", "price"]:
        out[col] = out[col].astype("Int64")  # NULL을 허용하는 정수형(35000.0 → 35000)
    out.index.name = "id"

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    if Path(args.output).exists():
        Path(args.output).unlink()
    con = sqlite3.connect(args.output)
    out.to_sql("programs", con, index=True)
    con.executescript("""
        CREATE INDEX idx_region ON programs(sido, sigungu);
        CREATE INDEX idx_sport ON programs(sport);
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    con.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("source", "체육종합빅데이터센터_공공체육시설 프로그램 정보"),
        ("source_file", Path(args.input).name),
        ("as_of", args.as_of),
        ("rows", str(len(out))),
    ])
    con.commit()
    con.close()

    # ---- 리포트 ----
    print(f"원본 {n_raw:,}행 → 진행중(종료일>={as_of}) {n_active:,}행 → 비체육 제외·종목 분류 후 {len(out):,}행")
    print(f"시설 {out['facility_name'].nunique():,}곳 / 시도 {out['sido'].nunique()}곳 / 시군구 {out['sigungu'].nunique()}곳")
    print("\n[시도별 강좌 수]")
    print(out["sido"].value_counts().to_string())
    print("\n[종목별 강좌 수]")
    print(out["sport"].value_counts().to_string())
    print("\n[성인 수강 가능] ", out["adult_ok"].value_counts(dropna=False).rename({1: "가능", 0: "불가"}).to_dict())
    print("[시간 정보 출처] ", out["time_source"].value_counts().to_dict())
    print("[요일 없음] ", int(out["weekdays"].isna().sum()))
    print(f"\n저장: {args.output}")


if __name__ == "__main__":
    main()
