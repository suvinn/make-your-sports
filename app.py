"""
Streamlit 웹 화면
실행: streamlit run app.py
API 키: .streamlit/secrets.toml 의 ANTHROPIC_API_KEY (없으면 기본 분석기로 동작)
"""
from __future__ import annotations

import html
import math
import os
import sqlite3
from datetime import date
from urllib.parse import quote

import pandas as pd
import pydeck as pdk
import streamlit as st

from core.parse import parse_conditions
from core.recommend import DB_PATH, load_mapping, recommend
from core.saju import ELEMENT_OF, SIDO_LONGITUDE

st.set_page_config(page_title="운동운 — 사주로 찾는 우리 동네 운동", page_icon="🏃", layout="centered")

ELEMENTS = ["목", "화", "토", "금", "수"]
ELEMENT_COLOR = {"목": "#4E8B5F", "화": "#C4553D", "토": "#B8893A", "금": "#7F848C", "수": "#35618F"}
ELEMENT_TINT = {"목": "#E8F1EA", "화": "#F7E6E2", "토": "#F5EEDF", "금": "#EEEFF1", "수": "#E3EAF3"}
HANJA = {"목": "木", "화": "火", "토": "土", "금": "金", "수": "水"}
EXAMPLES = [
    "마포구에서 평일 저녁에, 한 달 5만원 이하로 해보고 싶어요",
    "고양 일산 쪽 주말 오전, 3~5만원 정도",
    "화목만 가능하고 7시 이후면 좋겠어요. 영등포",
]
E = html.escape  # 원천 데이터 문자열을 HTML에 넣을 때 이스케이프


def H(markup):
    """HTML 출력. 마크다운 처리기는 빈 줄에서 HTML 블록을 끊으므로 빈 줄을 제거하고 넣는다."""
    st.markdown("\n".join(line for line in markup.splitlines() if line.strip()), unsafe_allow_html=True)


# ============================================================================ 스타일
H("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");
@import url("https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@500;700&display=swap");
:root{
  --ink:#1E2430; --muted:#6B6F76; --line:#E7E1D6; --paper:#F7F3EC; --card:#FFFFFF; --seal:#B4432F;
}
html, body, .stApp, .stMarkdown, p, label, button, input, textarea, select, [data-baseweb] {
  font-family: Pretendard, -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
}
[data-testid="stIconMaterial"], .material-symbols-rounded, [class*="material-symbols"] {
  font-family: "Material Symbols Rounded" !important;
}
.stApp {background: var(--paper);}
.block-container {padding-top: 2.2rem; padding-bottom: 4rem; max-width: 780px;}
header[data-testid="stHeader"] {background: transparent;}
[data-testid="stToolbar"], #MainMenu, footer {display: none !important;}

/* 입력 폼 */
[data-testid="stForm"] {background: var(--card); border: 1px solid var(--line); border-radius: 20px;
  padding: 1.4rem 1.4rem 1.1rem; box-shadow: 0 1px 2px rgba(30,36,48,.04), 0 8px 24px rgba(30,36,48,.05);}
[data-testid="stForm"] label p {font-weight: 600; color: var(--ink); font-size: .9rem;}
textarea {font-size: .98rem !important;}
[data-testid="stFormSubmitButton"] button {height: 3rem; border-radius: 12px; font-weight: 700; font-size: 1.02rem;
  letter-spacing: .02em;}
.stButton button, [data-testid="stLinkButton"] a {border-radius: 999px; border-color: var(--line);
  background: var(--card); color: var(--ink); font-size: .86rem;}
.stButton button:hover, [data-testid="stLinkButton"] a:hover {border-color: var(--seal); color: var(--seal);}
[data-testid="stExpander"] details {border-color: var(--line); border-radius: 12px; background: var(--card);}

/* 공통 */
.muted {color: var(--muted); font-size: .88rem;}
.eyebrow {font-size: .78rem; letter-spacing: .14em; color: var(--seal); font-weight: 700;}
.sec {margin: 2.4rem 0 .9rem; display:flex; align-items:baseline; gap:.6rem;}
.sec h2 {font-family: "Noto Serif KR", serif; font-size: 1.45rem; font-weight: 700; color: var(--ink); margin: 0; padding: 0;}
.sec .sub {color: var(--muted); font-size: .85rem;}

/* 히어로 */
.hero {padding: .4rem 0 1.4rem;}
.hero h1 {font-family: "Noto Serif KR", serif; font-size: 2.7rem; font-weight: 700; color: var(--ink);
  margin: .35rem 0 .3rem; padding: 0; letter-spacing: -.01em;}
.hero h1 .hj {font-size: 1.35rem; color: var(--seal); margin-left: .5rem; letter-spacing: .12em; font-weight: 500;}
.hero p {color: var(--muted); font-size: 1rem; margin: 0 0 1.1rem;}
.steps {display: grid; grid-template-columns: repeat(3, 1fr); gap: .6rem;}
.step {background: rgba(255,255,255,.6); border: 1px solid var(--line); border-radius: 14px; padding: .7rem .85rem;
  font-size: .86rem; color: var(--ink); line-height: 1.35;}
.step b {display:block; font-family: "Noto Serif KR", serif; color: var(--seal); font-size: .95rem; margin-bottom: .15rem;}

/* 조건 태그 */
.cond {background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: .9rem 1.1rem; margin-top: 1.6rem;}
.cond .head {display:flex; justify-content:space-between; align-items:center; margin-bottom:.45rem;}
.cond .head b {font-size: .92rem; color: var(--ink);}
.badge {font-size: .72rem; padding: 2px 8px; border-radius: 999px; background: #EFE9DE; color: #7A6A4F; font-weight: 600;}
.tag {display:inline-block; padding: 4px 12px; margin: 3px 6px 3px 0; border-radius: 999px; background: var(--paper);
  border: 1px solid var(--line); font-size: .86rem; color: var(--ink);}

/* 사주 기둥 */
.pillars {display: grid; grid-template-columns: repeat(4, 1fr); gap: .7rem;}
.pillar {background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: .75rem .5rem .8rem; text-align: center;}
.pillar .lb {font-size: .78rem; color: var(--muted); margin-bottom: .45rem;}
.gz {font-family: "Noto Serif KR", serif; font-size: 1.9rem; line-height: 1; width: 3.1rem; height: 3.1rem;
  display:flex; align-items:center; justify-content:center; margin: .28rem auto 0; border-radius: 12px;}
.gz small {display:none;}
.el {font-size: .7rem; margin-top: .2rem;}
.pillar.empty .gz {background: #F1EEE8 !important; color: #B9B3A8 !important;}

/* 오행 막대 */
.oheng {background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 1rem 1.2rem; margin-top: .8rem;}
.orow {display:grid; grid-template-columns: 6.2rem 1fr 2.4rem; align-items:center; gap:.7rem; margin: .38rem 0;}
.orow .nm {font-size: .9rem; color: var(--ink); white-space: nowrap;}
.orow .nm .hj {font-family: "Noto Serif KR", serif; color: var(--muted); margin-left: .2rem;}
.track {height: 10px; background: #F1EEE8; border-radius: 999px; overflow: hidden;}
.fill {height: 100%; border-radius: 999px;}
.orow .ct {font-size: .88rem; color: var(--muted); text-align:right;}
.orow.low .nm {font-weight: 700;}
.low-badge {font-size: .68rem; background: var(--seal); color: #fff; border-radius: 999px; padding: 1px 7px; margin-left: .35rem;}
.reading {margin-top: .9rem; padding: .95rem 1.1rem; border-radius: 14px; background: #FBF6EE; border: 1px dashed #E2D6C1;
  color: var(--ink); font-size: .96rem; line-height: 1.55;}

/* 추천 카드 */
.rec {background: var(--card); border: 1px solid var(--line); border-left: 6px solid var(--c); border-radius: 16px;
  padding: 1.1rem 1.25rem 1rem; margin: 1.1rem 0 .55rem; box-shadow: 0 6px 18px rgba(30,36,48,.04);}
.rec-head {display:flex; align-items:center; gap:.6rem; flex-wrap: wrap;}
.rank {font-family: "Noto Serif KR", serif; color: var(--c); font-size: 1.05rem; font-weight: 700;}
.sport {font-family: "Noto Serif KR", serif; font-size: 1.4rem; font-weight: 700; color: var(--ink);}
.pill {font-size: .76rem; padding: 3px 10px; border-radius: 999px; background: var(--t); color: var(--c); font-weight: 700;}
.reason {color: var(--muted); font-size: .92rem; margin: .35rem 0 .85rem;}
.cls {background: #FBFAF7; border: 1px solid #EFEAE0; border-radius: 12px; padding: .8rem .95rem;}
.fac {font-weight: 700; color: var(--ink); font-size: 1rem;}
.prog {color: var(--ink); font-size: .92rem; margin-top: .1rem;}
.meta {display:grid; grid-template-columns: repeat(4, 1fr); gap: .4rem; margin: .7rem 0 .55rem;}
.meta div {font-size: .86rem; color: var(--ink);}
.meta span {display:block; font-size: .7rem; color: var(--muted); margin-bottom: .05rem;}
.addr {font-size: .82rem; color: var(--muted);}
.warn {display:inline-block; padding: 1px 8px; margin: .45rem .3rem 0 0; border-radius: 6px; background: #FFF3E0;
  color: #8A5A00; font-size: .74rem;}
.howto {font-size: .8rem; color: var(--muted); margin: .1rem 0 .2rem;}

.foot {margin-top: 2.6rem; padding-top: 1rem; border-top: 1px solid var(--line); font-size: .78rem; color: var(--muted); line-height: 1.6;}

@media (max-width: 640px) {
  .steps {grid-template-columns: 1fr;}
  .hero h1 {font-size: 2.1rem;}
  .meta {grid-template-columns: repeat(2, 1fr);}
  .gz {font-size: 1.5rem; width: 2.5rem; height: 2.5rem;}
}
</style>
""")


# ============================================================================ 유틸
def fit_view(df, width_px=700, height_px=500):
    """점 전체가 들어오도록 줌 계산(pydeck compute_view는 좁은 범위에서 줌을 과하게 잡음)"""
    span_lon = max(df.lon.max() - df.lon.min(), 0.01)
    span_lat = max(df.lat.max() - df.lat.min(), 0.01) / math.cos(math.radians(df.lat.mean()))
    z = min(math.log2(360 * width_px / (256 * span_lon)), math.log2(360 * height_px / (256 * span_lat)))
    return pdk.ViewState(latitude=(df.lat.max() + df.lat.min()) / 2, longitude=(df.lon.max() + df.lon.min()) / 2,
                         zoom=max(6, min(15, z - 1.4)))


def fmt_tel(t):
    d = "".join(ch for ch in str(t) if ch.isdigit())
    if d.startswith("02") and len(d) in (9, 10):
        return f"02-{d[2:-4]}-{d[-4:]}"
    if len(d) in (10, 11):
        return f"{d[:3]}-{d[3:-4]}-{d[-4:]}"
    return t


def api_key():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")


@st.cache_data(show_spinner=False, max_entries=500)
def cached_parse(text, use_key):
    """같은 문장이면 Claude API를 다시 부르지 않는다(버튼 재클릭·새로고침 비용 방지)"""
    return parse_conditions(text, api_key=api_key() if use_key else None, use_llm=use_key)


@st.cache_data
def mapping():
    return load_mapping()


@st.cache_data
def data_meta():
    con = sqlite3.connect(DB_PATH)
    try:
        return dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()


def section(title, sub=""):
    H(f"<div class='sec'><h2>{title}</h2><span class='sub'>{sub}</span></div>")


def rgba(hex_color, a):
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [a]


# ============================================================================ 입력
H("""
<div class="hero">
  <div class="eyebrow">사주 명리 × 공공체육시설 데이터</div>
  <h1>운동운<span class="hj">運動運</span></h1>
  <p>사주에서 부족한 기운을 찾고, 그 기운을 채울 운동을 우리 동네 공공체육시설 강좌로 이어 드려요.</p>
  <div class="steps">
    <div class="step"><b>01</b>생년월일시로 오행 계산</div>
    <div class="step"><b>02</b>지역·요일·예산을 한 문장으로</div>
    <div class="step"><b>03</b>지금 들을 수 있는 공공 강좌</div>
  </div>
</div>
""")

if "query" not in st.session_state:
    st.session_state.query = ""

with st.form("input"):
    c1, c2 = st.columns(2)
    birth_date = c1.date_input("생년월일 (양력)", value=date(2000, 1, 1),
                               min_value=date(1930, 1, 1), max_value=date.today())
    birth_sido = c2.selectbox("태어난 곳", list(SIDO_LONGITUDE.keys()), index=0)
    # time_input은 일부 브라우저에서 로케일 오류(RangeError)가 나서 선택 상자로 대체
    c3, c4, c5 = st.columns([1, 1, 1.3])
    birth_hour = c3.selectbox("태어난 시", list(range(24)), index=12, format_func=lambda h: f"{h}시")
    birth_min = c4.selectbox("분", list(range(60)), index=0, format_func=lambda m: f"{m}분")
    c5.write("")
    time_unknown = c5.checkbox("태어난 시간을 몰라요", value=False)
    query = st.text_area("어디서, 언제, 얼마로 운동하고 싶나요?", key="query",
                         placeholder=f"예) {EXAMPLES[0]}", height=84)
    st.caption("비워 두면 지역·요일·예산 제한 없이 전체 강좌에서 찾아요.")
    submitted = st.form_submit_button("내 운동운 보기", type="primary", width="stretch")


def _use_example(text):
    st.session_state.query = text  # 콜백은 다음 실행 전에 돌아서 위젯 값 변경이 허용됨


H("<div class='muted' style='margin:.9rem 0 .35rem'>예시 문장으로 채우기</div>")
ex_cols = st.columns(len(EXAMPLES))
for col, ex in zip(ex_cols, EXAMPLES):
    col.button(ex[:14] + "…", help=ex, width="stretch", on_click=_use_example, args=(ex,))

if not submitted:
    H(f"<div class='foot'>{mapping()['disclaimer']}</div>")
    st.stop()

# ============================================================================ 계산
with st.spinner("사주를 계산하고 강좌를 찾는 중…"):
    q = query.strip()
    parsed = cached_parse(q, bool(api_key()))
    birth = {"year": birth_date.year, "month": birth_date.month, "day": birth_date.day,
             "hour": None if time_unknown else birth_hour,
             "minute": 0 if time_unknown else birth_min, "birth_sido": birth_sido}
    res = recommend(birth, parsed["conditions"])

# ============================================================================ 검색 조건
cond = res["conditions"]
tags = []
if cond["near_label"] and not cond["sigungu"]:
    tags.append(f"‘{cond['near_label']}’ 근처 {cond['radius_km']:g}km")
elif cond["near_label"] and cond["near_label"] not in " ".join(cond["sigungu"] or []):
    tags.append(f"{cond['near_label']} 기준 거리순")
if cond["sido"]:
    tags.append(cond["sido"] + (" " + ", ".join(cond["sigungu"]) if cond["sigungu"] else ""))
if cond["days"]:
    tags.append({"weekday": "평일 가능", "weekend": "주말 가능"}.get(cond["days"], f"{cond['days']} 가능"))
if cond["time_bands"]:
    tags.append("·".join(cond["time_bands"]))
if cond["start_after"]:
    tags.append(f"{cond['start_after']} 이후 시작")
if cond["start_before"]:
    tags.append(f"{cond['start_before']} 이전 시작")
if cond["max_price"]:
    tags.append(f"월 {cond['max_price']:,}원 이하")
if not cond["adult"]:
    tags.append("어린이·청소년")

if not q:
    body = "<span class='muted'>입력한 조건이 없어 전체 강좌에서 찾았어요. 지역이나 요일을 적으면 더 가까운 강좌를 찾을 수 있어요.</span>"
    badge = ""
else:
    body = "".join(f"<span class='tag'>{E(t)}</span>" for t in tags) or \
        "<span class='muted'>문장에서 조건을 찾지 못해 전체 강좌에서 찾았어요.</span>"
    badge = f"<span class='badge'>{'AI 분석' if parsed['source'] == 'llm' else '기본 분석'}</span>"
H(f"<div class='cond'><div class='head'><b>검색 조건</b>{badge}</div>{body}</div>")
for w in parsed["warnings"]:
    st.info(w)

# ============================================================================ 사주
saju = res["saju"]
low = set(saju["deficiency_ranks"][0])
section("나의 사주", "만세력 기준 · 진태양시 보정")


def gz_tile(ch):
    if not ch:
        return "<div class='gz'>?</div><div class='el'>&nbsp;</div>"
    e = ELEMENT_OF[ch]
    return (f"<div class='gz' style='background:{ELEMENT_TINT[e]};color:{ELEMENT_COLOR[e]}'>{ch}</div>"
            f"<div class='el' style='color:{ELEMENT_COLOR[e]}'>{e}({HANJA[e]})</div>")


tiles = ""
for k, label in [("hour", "시주"), ("day", "일주"), ("month", "월주"), ("year", "연주")]:
    gz = saju["pillars"][k]
    cls = "pillar" if gz else "pillar empty"
    sub = "태어난 시간 모름" if not gz else ("나 (일간)" if k == "day" else "")
    tiles += (f"<div class='{cls}'><div class='lb'>{label}{' · ' + sub if sub else ''}</div>"
              f"{gz_tile(gz[0] if gz else None)}{gz_tile(gz[1] if gz else None)}</div>")
H(f"<div class='pillars'>{tiles}</div>")

total = sum(saju["counts"].values())
rows = ""
for e in ELEMENTS:
    n = saju["counts"][e]
    w = 100 * n / max(saju["counts"].values())
    badge_low = "<span class='low-badge'>부족</span>" if e in low else ""
    rows += (f"<div class='orow{' low' if e in low else ''}'>"
             f"<div class='nm'>{e}<span class='hj'>{HANJA[e]}</span>{badge_low}</div>"
             f"<div class='track'><div class='fill' style='width:{max(w, 3 if n else 0)}%;background:{ELEMENT_COLOR[e]}'></div></div>"
             f"<div class='ct'>{n}/{total}</div></div>")
reading = "<br>".join(E(msg) for msg in res["message"])
H(f"<div class='oheng'>{rows}<div class='reading'>{reading}</div></div>")
for n in saju["notes"]:
    st.caption(n)

# ============================================================================ 추천
picks = res["step2"]["picks"]
section("추천 강좌", f"{len(picks)}개 종목 · 종목마다 조건에 가장 잘 맞는 강좌 하나씩")
m = mapping()
for i, p in enumerate(picks, 1):
    c = p["class"]
    elem = p["element"]
    color, tint = ELEMENT_COLOR[elem], ELEMENT_TINT[elem]
    if p["role"] == "주":
        label = f"{elem}({HANJA[elem]}) 종목"
    else:
        label = f"{elem}({HANJA[elem]}) 기운을 곁들인 {p['primary']}({HANJA[p['primary']]}) 종목"
    price = f"{c['price']:,}원" if c["price"] else "정보 없음"
    time_txt = c["start_time"] or c["time_band"] or "미상"
    stop = f" · {E(c['nearest_stop'])} 도보 {c['walk_min']}분" if c["nearest_stop"] and c["walk_min"] else ""
    if c.get("distance_km") is not None:
        stop += f" · {E(cond['near_label'] or '입력한 곳')}에서 약 {c['distance_km']}km"
    warn = "".join(f"<span class='warn'>{u} 확인 필요</span>" for u in c["uncertain"])
    H(f"""
<div class='rec' style='--c:{color};--t:{tint}'>
  <div class='rec-head'><span class='rank'>{i:02d}</span><span class='sport'>{E(p['sport'])}</span><span class='pill'>{label}</span></div>
  <div class='reason'>{E(p['reason'])}</div>
  <div class='cls'>
    <div class='fac'>{E(c['facility_name'])}</div>
    <div class='prog'>{E(c['program_name'])}</div>
    <div class='meta'>
      <div><span>요일</span>{E(c['weekdays'] or '미상')}</div>
      <div><span>시작</span>{E(time_txt)}</div>
      <div><span>월 수강료</span>{price}</div>
      <div><span>대상</span>{E(c['target_raw'] or '미상')}</div>
    </div>
    <div class='addr'>{E(c['address'] or '')}{stop}</div>
    {warn}
  </div>
</div>""")
    # 원천 데이터에는 강좌별 신청 링크가 없고 시설(운영기관) 예약 사이트 주소만 있음(62개 사이트/198개 시설)
    b1, b2 = st.columns(2)
    if c["homepage"]:
        b1.link_button("예약 사이트 열기", c["homepage"], width="stretch")
    b2.link_button("지도에서 보기", f"https://map.naver.com/p/search/{quote(c['facility_name'])}", width="stretch")
    tel = f" · 문의 {fmt_tel(c['tel'])}" if c.get("tel") else ""
    st.markdown(f"<div class='howto'>예약 사이트에서 ‘{E(c['program_name'])}’ 강좌를 찾아 신청하세요{tel}</div>",
                unsafe_allow_html=True)
    if p["alternatives"]:
        with st.expander(f"같은 조건의 다른 {p['sport']} 강좌 {p['more_count']}개"):
            alt = [{"시설": a["facility_name"], "강좌": a["program_name"], "요일": a["weekdays"],
                    "시간": a["start_time"] or a["time_band"] or "미상",
                    "가격": f"{a['price']:,}원" if a["price"] else "정보 없음"} for a in p["alternatives"]]
            st.dataframe(pd.DataFrame(alt), hide_index=True, width="stretch")

for n in res["step2"]["notes"]:
    st.warning(n)

# ============================================================================ 지도
# 추천 강좌 + 같은 조건의 다른 강좌가 열리는 시설을 모아 보여줌. 시설이 1곳이면 지도가 의미 없으므로 생략
fac = {}
for p in picks:
    for cl in [p["class"]] + p["alternatives"]:
        if not (cl["lat"] and cl["lon"]):
            continue
        f = fac.setdefault(cl["facility_name"], {"name": cl["facility_name"], "lat": cl["lat"], "lon": cl["lon"],
                                                  "sports": set(), "n": 0, "top": False})
        f["sports"].add(p["sport"])
        f["n"] += 1
        f["top"] |= cl is p["class"]
has_near = cond["near_lat"] is not None
if len(fac) >= 2 or (has_near and fac):
    df = pd.DataFrame([{**f, "sports": "·".join(sorted(f["sports"])),
                        "color": rgba("#B4432F", 235) if f["top"] else rgba("#7F848C", 170),
                        "radius": 120 + 40 * min(f["n"], 10)} for f in fac.values()])
    section("조건에 맞는 강좌가 있는 곳", f"시설 {len(df)}곳")
    st.markdown("<div class='muted' style='margin-bottom:.5rem'>붉은 점은 위에 추천된 시설, 회색 점은 같은 조건의 다른 강좌가 있는 시설이에요. "
                "점이 클수록 강좌가 많고, 마우스를 올리면 시설 이름이 보여요."
      + (" 검은 점은 입력한 위치예요." if has_near else "") + "</div>", unsafe_allow_html=True)
    layers = [pdk.Layer("ScatterplotLayer", df, get_position="[lon, lat]", get_fill_color="color",
                        get_radius="radius", radius_min_pixels=6, radius_max_pixels=22, pickable=True,
                        stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1)]
    view_df = df[["lat", "lon"]]
    if has_near:
        me = pd.DataFrame([{"name": f"{cond['near_label']} (입력한 곳)", "lat": cond["near_lat"], "lon": cond["near_lon"],
                            "sports": "기준 위치", "n": 0}])
        layers.append(pdk.Layer("ScatterplotLayer", me, get_position="[lon, lat]", get_fill_color=[30, 36, 48, 255],
                                get_radius=60, radius_min_pixels=7, radius_max_pixels=9, pickable=True,
                                stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=3))
        view_df = pd.concat([view_df, me[["lat", "lon"]]])
    st.pydeck_chart(pdk.Deck(
        initial_view_state=fit_view(view_df),
        layers=layers,
        tooltip={"text": "{name}\n{sports} · 강좌 {n}개"},
    ))

# ============================================================================ 출처
meta = data_meta()
H(f"""<div class='foot'>
강좌 데이터: {E(meta.get('source', ''))} ({E(meta.get('source_file', ''))}, {E(meta.get('as_of', ''))} 기준 진행 강좌) · 문화빅데이터플랫폼 제공<br>
{E(m['disclaimer'])}
</div>""")
