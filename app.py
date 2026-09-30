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
import streamlit as st

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


def api_key():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")


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
    submitted = st.form_submit_button("추천 받기", type="primary", use_container_width=True)

def _use_example(text):
    st.session_state.query = text  # 콜백은 다음 실행 전에 돌아서 위젯 값 변경이 허용됨


st.caption("예시 문장")
ex_cols = st.columns(len(EXAMPLES))
for col, ex in zip(ex_cols, EXAMPLES):
    col.button(ex[:14] + "…", help=ex, use_container_width=True, on_click=_use_example, args=(ex,))

if not submitted:
    st.markdown(f"<p class='muted'>{mapping()['disclaimer']}</p>", unsafe_allow_html=True)
    st.stop()

# ----------------------------------------------------------------------------- 계산
with st.spinner("사주를 계산하고 강좌를 찾는 중…"):
    parsed = parse_conditions(query, api_key=api_key())
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
    elem = p["primary"] if p["via"] and "(주)" in p["via"] else p["secondary"]
    role = "주" if p["via"] and "(주)" in p["via"] else "보조"
    price = f"{c['price']:,}원" if c["price"] else "가격 정보 없음"
    when = f"{c['weekdays'] or '요일 미상'} · {c['start_time'] or c['time_band'] or '시간 미상'}"
    stop = f" · {c['nearest_stop']} 도보 {c['walk_min']}분" if c["nearest_stop"] and c["walk_min"] else ""
    warn = "".join(f"<span class='warn'>{u} 확인 필요</span>" for u in c["uncertain"])
    color = ELEMENT_COLOR.get(elem, "#495057")
    st.markdown(f"""
<div class='card'>
  <h4>{p['sport']} <span style='color:{color};font-size:0.85rem'>● {elem}({HANJA.get(elem, '')}) {role} 종목</span></h4>
  <div class='muted'>{p['reason']}</div>
  <div style='margin-top:10px'><b>{c['facility_name']}</b> — {c['program_name']}</div>
  <div class='muted'>{when} · {price} · 대상 {c['target_raw'] or '미상'}{warn}</div>
  <div class='muted'>{c['address']}{stop}</div>
  <div class='muted'>같은 조건의 {p['sport']} 강좌 {p['more_count']}개 더</div>
</div>""", unsafe_allow_html=True)
    if c["homepage"]:
        st.link_button("신청 페이지 열기", c["homepage"])

for n in res["step2"]["notes"]:
    st.warning(n)

pts = [{"lat": p["class"]["lat"], "lon": p["class"]["lon"]} for p in picks
       if p["class"]["lat"] and p["class"]["lon"]]
if pts:
    st.map(pd.DataFrame(pts), size=60, zoom=11)

# ----------------------------------------------------------------------------- 출처
meta = data_meta()
st.divider()
st.caption(f"강좌 데이터: {meta.get('source')} ({meta.get('source_file')}, {meta.get('as_of')} 기준 진행 강좌) · "
           "문화빅데이터플랫폼 제공")
st.caption(m["disclaimer"])
