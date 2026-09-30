# 사주 기반 체육 취미 추천

2026 국민체육진흥공단 공공데이터 활용 경진대회 — 서비스(앱·웹) 개발 부문 출품작

생년월일시로 오행 분포를 계산하고, 자연어로 받은 조건(지역·요일·시간·예산)에 맞는
**실제 공공체육시설 강좌**를 추천합니다.

```
생년월일시 → [sajupy] 사주 4주·오행 분포 → [오행→종목 매핑표] 종목 후보
          → [LLM] 자연어 조건 파싱 → [programs.sqlite] 강좌 매칭 → 추천 + 재미용 풀이
```

## 폴더 구조

```
data/
  raw/          원천 CSV (git 제외 — 아래 '데이터 준비' 참고)
  processed/    programs.sqlite (전처리 결과, git 포함)
  mapping/
    sport_rules.json     강좌 → 표준 종목 분류 키워드 규칙
    ohaeng_sports.json   오행 → 종목 매핑표 (주·보조 오행, 추천 규칙, 문구 가이드 / 재미용 팀 자체 규칙)
scripts/
  preprocess.py  원천 CSV → programs.sqlite
core/
  saju.py        사주 4주·오행 분포·부족 순위 계산 (sajupy + 입춘 경계 보정)
  recommend.py   1단계 종목 후보 + 2단계 실제 강좌 추천, CLI
tests/
  test_core.py   사주 계산·추천 규칙 테스트
```

## 추천 실행 (CLI)

```bash
python -m core.recommend --birth 2000-06-24 --time 12:30 --birth-sido 서울특별시 \
    --sido 서울특별시 --sigungu 마포구 --days weekday --bands 저녁 --max-price 50000
python -m pytest -q
```

- `--time` 생략 시 시주 없이 6글자로 계산
- `--days`: `weekday` / `weekend` / `화목` 같은 가능 요일(강좌 요일이 전부 이 안에 있어야 매칭)
- `--bands`: 새벽 / 오전 / 오후 / 저녁

## 활용 공공데이터

| 데이터 | 제공 | 용도 |
|---|---|---|
| 공공체육시설 프로그램 정보 (`KS_PUBLIC_ALSFC_PROGRM_INFO_YYYYMM.csv`) | 체육종합빅데이터센터(국민체육진흥공단), 문화빅데이터플랫폼 | 강좌 매칭(핵심) |

## 데이터 준비

1. [문화빅데이터플랫폼 - 공공체육시설 프로그램 정보](https://www.bigdata-culture.kr/bigdata/user/data_market/detail.do?id=c3b8fb69-307d-4ae7-ab42-d0314c89ef47)에서 CSV 다운로드
2. `data/raw/`에 저장
3. 전처리 실행

```bash
pip install -r requirements.txt
python scripts/preprocess.py --input data/raw/KS_PUBLIC_ALSFC_PROGRM_INFO_202608.csv --as-of 2026-10-01
```

`--as-of` 날짜에 진행 중인 강좌(종료일 ≥ as-of)만 남깁니다. 매월 새 파일이 올라오면 파일명과 날짜만 바꿔 다시 실행하세요.

## programs.sqlite 스키마 (테이블 `programs`)

| 컬럼 | 설명 |
|---|---|
| facility_name, facility_type | 시설명(접두어 정리), 시설 업종 |
| sido, sigungu, emd, address, lat, lon | 위치 |
| nearest_stop, walk_min | 가장 가까운 정류장/역, 도보 분(원천 초 단위 → 분 올림, 단위는 추정) |
| sport | 표준 종목(sport_rules.json 기준) |
| program_type_raw, program_name, target_raw | 원천 종목유형·프로그램명·대상 |
| adult_ok | 성인 수강 가능 1 / 불가 0 / 판단불가 NULL |
| begin_date, end_date | 강좌 기간 |
| weekdays, has_weekday, has_weekend | 개설 요일(예: "월수금"), 평일/주말 포함 여부 |
| start_time, end_time, time_band, time_source | 시작·종료 시각, 시간대(새벽/오전/오후/저녁), 출처(field/name/word/none) |
| capacity, price, homepage | 모집인원, 가격(원, 1,000원 미만은 NULL), 신청 홈페이지 |

`meta` 테이블에 원천 파일명과 기준일이 기록됩니다.

## 알려진 한계

- 지역 커버리지: 서울·경기·부산이 강좌의 약 92%. 광주·대전·세종·전북·전남·제주는 0건
- 시간 정보: 원천 필드는 약 26%만 채워져 있고, 프로그램명에서 추출(약 42%), 없음(약 25%)
- 종목 쏠림: 수영이 약 44%
- 오행→종목 매핑은 과학적 근거가 아닌 재미용 규칙이며, 서비스에 그렇게 표기함(`disclaimer`, `copy_guide` 참고)
