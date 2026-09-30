"""
Streamlit 웹 화면
실행: streamlit run app.py
API 키: .streamlit/secrets.toml 의 ANTHROPIC_API_KEY (없으면 기본 분석기로 동작)
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date

import pandas as pd
import pydeck as pdk
import streamlit as st
from urllib.parse import quote

from core.parse import parse_conditions
from core.recommend import DB_PATH, load_mapping, recommend
from core.saju import SIDO_LONGITUDE

st.set_page_config(page_title="운동운 — 사주로 찾는 우리 동네 운동", page_icon="🏃", layout="centered")

ELEMENT_COLOR = {"목": "#3f9b5a", "화": "#d9534f", "토": "#c79a3b", "금": "#8a8f98", "수": "#2f6fb0"}
HANJA = {"목": "木", "화": "火", "토": "土", "금": "金", "수": "水"}
EXAMPLES = [
    "마포구에서 평일 저녁에, 한 달 5만원 이하로 해보고 싶어요",
    "고양 일산 쪽 주말 오전, 3~5만원 정도",
    "화목만 가능하고 7시 이후면 좋겠어요. 영등포",
]

st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 760px;}
.tag {display:inline-block; padding:2px 10px; margin:2px 4px 2px 0; border-radius:999px;
      background:#f1f3f5; font-size:0.85rem;}
.warn {display:inline-block; padding:1px 8px; margin-left:4px; border-radius:6px;
       background:#fff4e5; color:#8a5a00; font-size:0.78rem;}
.card {border:1px solid #e9ecef; border-radius:14px; padding:16px 18px; margin-bottom:12px;}
.card h4 {margin:0 0 4px 0;}
.muted {color:#6c757d; font-size:0.88rem;}
.pillar {text-align:center; border:1px solid #e9ecef; border-radius:10px; padding:8px 0;}
.pillar .gz {font-size:1.6rem; letter-spacing:2px;}
.bar {height:12px; border-radius:6px;}
</style>
""", unsafe_allow_html=True)


def fit_view(df, width_px=700, height_px=500):
    """점 전체가 들어오도록 줌 계산(pydeck compute_view는 좁은 범위에서 줌을 과하게 잡음)"""
    import math
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


# ----------------------------------------------------------------------------- 입력
st.title("운동운 運動運")
st.caption("사주로 부족한 기운을 찾고, 우리 동네 공공체육시설 강좌로 연결해 드려요.")

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
                         placeholder=EXAMPLES[0], height=80)
    submitted = st.form_submit_button("추천 받기", type="primary", width="stretch")

def _use_example(text):
    st.session_state.query = text  # 콜백은 다음 실행 전에 돌아서 위젯 값 변경이 허용됨


st.caption("예시 문장")
ex_cols = st.columns(len(EXAMPLES))
for col, ex in zip(ex_cols, EXAMPLES):
    col.button(ex[:14] + "…", help=ex, width="stretch", on_click=_use_example, args=(ex,))

if not submitted:
    st.markdown(f"<p class='muted'>{mapping()['disclaimer']}</p>", unsafe_allow_html=True)
    st.stop()

# ----------------------------------------------------------------------------- 계산
with st.spinner("사주를 계산하고 강좌를 찾는 중…"):
    parsed = cached_parse(query.strip(), bool(api_key()))
    birth = {"year": birth_date.year, "month": birth_date.month, "day": birth_date.day,
             "hour": None if time_unknown else birth_hour,
             "minute": 0 if time_unknown else birth_min, "birth_sido": birth_sido}
    res = recommend(birth, parsed["conditions"])

# ----------------------------------------------------------------------------- 조건
cond = res["conditions"]
tags = []
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
src = "AI 분석" if parsed["source"] == "llm" else "기본 분석"
st.markdown("**검색 조건** <span class='muted'>(" + src + ")</span><br>" +
            ("".join(f"<span class='tag'>{t}</span>" for t in tags) or "<span class='muted'>조건 없음 — 전체에서 찾았어요</span>"),
            unsafe_allow_html=True)
for w in parsed["warnings"]:
    st.info(w)

# ----------------------------------------------------------------------------- 사주
saju = res["saju"]
st.subheader("나의 사주와 오행")
order = [("hour", "시주"), ("day", "일주"), ("month", "월주"), ("year", "연주")]
cols = st.columns(4)
for col, (k, label) in zip(cols, order):
    gz = saju["pillars"][k] or "??"
    col.markdown(f"<div class='pillar'><div class='muted'>{label}</div><div class='gz'>{gz}</div></div>",
                 unsafe_allow_html=True)

total = sum(saju["counts"].values())
bars = ""
for e in ["목", "화", "토", "금", "수"]:
    n = saju["counts"][e]
    w = max(4, int(100 * n / total)) if n else 2
    bars += (f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0'>"
             f"<div style='width:52px'>{e}({HANJA[e]})</div>"
             f"<div class='bar' style='width:{w}%;background:{ELEMENT_COLOR[e]}'></div>"
             f"<div class='muted'>{n}</div></div>")
st.markdown(bars, unsafe_allow_html=True)
for msg in res["message"]:
    st.success(msg)
for n in saju["notes"]:
    st.caption(n)

# ----------------------------------------------------------------------------- 추천
st.subheader("추천 강좌")
picks = res["step2"]["picks"]
m = mapping()
for p in picks:
    c = p["class"]
    elem = p["element"]
    if p["role"] == "주":
        label = f"{elem}({HANJA[elem]}) 종목"
    else:
        label = f"{elem}({HANJA[elem]}) 기운을 곁들인 {p['primary']}({HANJA[p['primary']]}) 종목"
    price = f"{c['price']:,}원" if c["price"] else "가격 정보 없음"
    when = f"{c['weekdays'] or '요일 미상'} · {c['start_time'] or c['time_band'] or '시간 미상'}"
    stop = f" · {c['nearest_stop']} 도보 {c['walk_min']}분" if c["nearest_stop"] and c["walk_min"] else ""
    warn = "".join(f"<span class='warn'>{u} 확인 필요</span>" for u in c["uncertain"])
    color = ELEMENT_COLOR.get(elem, "#495057")
    st.markdown(f"""
<div class='card'>
  <h4>{p['sport']} <span style='color:{color};font-size:0.85rem'>● {label}</span></h4>
  <div class='muted'>{p['reason']}</div>
  <div style='margin-top:10px'><b>{c['facility_name']}</b> — {c['program_name']}</div>
  <div class='muted'>{when} · {price} · 대상 {c['target_raw'] or '미상'}{warn}</div>
  <div class='muted'>{c['address']}{stop}</div>
</div>""", unsafe_allow_html=True)
    # 원천 데이터에는 강좌별 신청 링크가 없고 시설(운영기관) 예약 사이트 주소만 있음(62개 사이트/198개 시설)
    b1, b2 = st.columns(2)
    if c["homepage"]:
        b1.link_button("예약 사이트 열기", c["homepage"], width="stretch")
    b2.link_button("지도에서 보기", f"https://map.naver.com/p/search/{quote(c['facility_name'])}",
                   width="stretch")
    tel = f" · 문의 {fmt_tel(c['tel'])}" if c.get("tel") else ""
    st.caption(f"예약 사이트에서 ‘{c['program_name']}’ 강좌를 찾아 신청하세요{tel}")
    if p["alternatives"]:
        with st.expander(f"같은 조건의 다른 {p['sport']} 강좌 {p['more_count']}개"):
            rows = [{"시설": a["facility_name"], "강좌": a["program_name"], "요일": a["weekdays"],
                     "시간": a["start_time"] or a["time_band"] or "미상",
                     "가격": f"{a['price']:,}원" if a["price"] else "정보 없음"} for a in p["alternatives"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

for n in res["step2"]["notes"]:
    st.warning(n)

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
if len(fac) >= 2:
    df = pd.DataFrame([{**f, "sports": "·".join(sorted(f["sports"])),
                        "color": [47, 111, 176, 230] if f["top"] else [134, 142, 150, 170],
                        "radius": 120 + 40 * min(f["n"], 10)} for f in fac.values()])
    st.subheader("조건에 맞는 강좌가 있는 곳")
    st.caption(f"추천 종목의 강좌가 열리는 시설 {len(df)}곳 · 파란 점은 위 카드에 추천된 시설, 점이 클수록 강좌가 많아요 · 점에 마우스를 올리면 시설 이름이 보여요")
    st.pydeck_chart(pdk.Deck(
        initial_view_state=fit_view(df),
        layers=[
            pdk.Layer("ScatterplotLayer", df, get_position="[lon, lat]", get_fill_color="color",
                      get_radius="radius", radius_min_pixels=6, radius_max_pixels=22, pickable=True,
                      stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1),
        ],
        tooltip={"text": "{name}\n{sports} · 강좌 {n}개"},
    ))

# ----------------------------------------------------------------------------- 출처
meta = data_meta()
st.divider()
st.caption(f"강좌 데이터: {meta.get('source')} ({meta.get('source_file')}, {meta.get('as_of')} 기준 진행 강좌) · "
           "문화빅데이터플랫폼 제공")
st.caption(m["disclaimer"])
