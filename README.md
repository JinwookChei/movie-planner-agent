# MoviePlanner Agent

"내일 강남에서 2시 이후에 보고 7시 전에 끝나야 해"처럼 **시간 조건**을 말하면, 실제 박스오피스·러닝타임·영화관 정보를 API로 확인해 **그 시간 안에 볼 수 있는 영화**와 **늦어도 몇 시에 시작해야 하는지**를 알려 주는 Agent입니다. 맞는 영화가 없으면 스스로 계획을 바꿔 다시 찾고(최대 3회), 추천한 영화는 관람 계획으로 저장·수정·삭제할 수 있습니다.

SSAFY AI 코어 프로젝트 3회차 · LangGraph 1.2.10 · langchain-openai 1.4.3 (SSAFY GMS) · FastAPI · SQLite · HTML/CSS/JS

## 빠른 실행

```powershell
# 1) 가상환경과 의존성 (Python 3.11)
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt

# 2) 키 설정: .env.example을 .env로 복사한 뒤 값 4개를 채운다 (아래 "API 키")
copy .env.example .env

# 3) 백엔드 (터미널 1)
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000
#    → http://127.0.0.1:8000/docs  (API 명세, 직접 호출해 볼 수 있음)

# 4) 화면 (터미널 2)
cd frontend
python -m http.server 5500
#    → http://localhost:5500/index.html
```

화면은 3개입니다: **대화**(`index.html`, 오른쪽에 실행 과정 패널) · **계획 관리**(`plans.html`) · **실행 로그**(`logs.html`).

## API 키

키는 루트 `.env`에만 둡니다. 소스·커밋·화면에 나오지 않습니다(NF3001, NF3002). `.env`는 `.gitignore`에 들어 있습니다.

| 변수 | 발급처 | 없으면 |
|---|---|---|
| `GMS_API_KEY` | SSAFY GMS | 요청 분석·답변 불가 (화면에 빨간 배너) |
| `KOBIS_API_KEY` | [KOBIS 오픈API](https://www.kobis.or.kr/kobisopenapi/homepg/main/main.do) | 영화 목록·러닝타임 불가. 재계획하지 않고 다시 시도를 안내 |
| `TMDB_API_KEY` | [TMDB](https://www.themoviedb.org/settings/api) (v3 API Key) | 신작 보충만 빠짐 |
| `KAKAO_REST_API_KEY` | [Kakao Developers](https://developers.kakao.com/) REST API 키 | 영화관 안내만 빠짐 (영화 판단은 정상) |

## 외부 API 출처와 이용 조건 (NF3007)

| API | 쓰는 곳 | 이용 조건 |
|---|---|---|
| **KOBIS 오픈API** (영화진흥위원회) | 일별 박스오피스(후보 목록), 영화 상세(러닝타임·등급·장르) | 회원가입 후 키 발급. 일별 박스오피스는 1회 최대 10건. 호출 한도는 공식 문서에서 수치를 확정하지 못함(기획서 1.2절) |
| **TMDB API** | 박스오피스 집계에 없는 신작 보충 (재계획 전략 2) | 비상업적 사용 무료, 출처 표기 필수. 화면 하단에 "This product uses the TMDB API but is not endorsed or certified by TMDB."를 표시 |
| **카카오 로컬** (키워드로 장소 검색, CT1) | 지역 영화관의 이름·주소·링크 | 앱 단위 일간·월간 쿼터(콘솔 [통계 > 쿼터]). 검색 결과를 DB에 저장하지 않고 세션 메모리에만 둠 |
| **SSAFY GMS** | analyze(의도·조건), respond(답변 문장) | 교육 과정 정책 |

호출을 줄이는 장치: 같은 세션에서 같은 Tool·같은 인자는 다시 부르지 않습니다(`"Tool|인자"` cache, F3009). 상영 시각표는 어떤 API로도 확인할 수 없어서, 시각을 지어내지 않고 "늦어도 시작할 시각"을 역산해 제시합니다(F3008).

## 구조

```
backend/app/
  main.py            FastAPI 생성, CORS, 라우터, 시작 시 DB 초기화 + 로깅 Agent 구독
  config.py db.py schemas.py
  api/               chat · plans(겹침 검사) · logs(목록·상세·삭제, 수정 없음) · health
  agent/             graph(LangGraph) · nodes(5개 노드) · llm · judge(시간 계산) · replan(전략표) · memory(대화 맥락)
  tools/             base(ok/fail 공통 응답) · kobis · tmdb · kakao · registry(TOOLS / catalog)
  events/            bus(EventBus) · logger_agent(LLM 없이 SQLite 저장)
frontend/            index.html · plans.html · logs.html · app.js · style.css
tests/               test_judge.py · test_api.py   (pytest, LLM 호출 없음)
scripts/             check_apis.py(M0 API 확인) · run_scenarios.py(대표 요청 검증)
docs/                기획서.md · 설계지도.html · 검증/
```

```
START → analyze ─┬─(chat · 되묻기)──────────────────────────────▶ respond → END
       (LLM)     └─(plan)→ plan → execute → judge ─┬─(맞는 영화 있음 · 멈춤)─▶ respond (LLM)
                          (코드)  (코드)   (코드)  │
                            ▲                      │
                            └──(재계획: 0편, 최대 3회)┘
```

**LLM과 코드의 분리**: LLM은 말을 이해하고(의도·조건 추출) 문장을 만드는 일만 합니다. Tool 실행 순서, 시간 계산, 맞음/안 맞음 판단, 재계획 여부와 전략, 조건 병합, 되묻기 판정은 모두 코드가 합니다. 검증하면서 LLM이 가끔 어기는 규칙(키 이름 노출, 조정 질문 여부, 오전/오후 해석)도 코드로 옮겼습니다 → [`docs/검증/검증기록.md`](docs/검증/검증기록.md) 2절.

**이벤트 분리**: 메인 Agent는 `emit()`만 하고 구독자를 모릅니다. 로깅 Agent는 `main.py`에서만 연결되며, 저장에 실패해도 대화와 화면의 실행 과정은 정상입니다(`LOG_FAIL=1`로 시험).

## 검증

```powershell
.venv\Scripts\python -m pytest -q                # 27 passed · LLM·외부 API 없이 판단 로직과 API 전체
.venv\Scripts\python scripts\run_scenarios.py    # 대표 요청 10건을 실제 API로 실행 → docs/검증/
cd backend
..\.venv\Scripts\python -m app.tools.kobis       # Tool 자가 확인 (kakao · tmdb · registry 도 같은 방식)
..\.venv\Scripts\python -m app.events.logger_agent   # 로깅 Agent 자가 확인 12/12
```

| 경우 (명세 4장) | 대표 요청 | 결과 |
|---|---|---|
| 한 번에 충족 | R1 | ✅ |
| 재계획 | R2 | ✅ 5편 → 10편, 상세 5건 cache 재사용 |
| 상한 도달 | R3b | ✅ 3회에서 멈추고 조건 조정을 되물음 |
| Tool 하나 실패 | R4 (카카오 키 비움), R4b (KOBIS 키 비움) | ✅ 실패 사유를 남기고 계속, 재계획하지 않음 |
| Tool 불필요 | R5 | ✅ Tool 호출 0건 |

자세한 기록은 [`docs/검증/검증기록.md`](docs/검증/검증기록.md)(기대와 다른 점, 원인, 수정)와 [`docs/검증/결과.md`](docs/검증/결과.md)(자동 기록)에 있습니다.

## 제출물 위치

| 제출물 | 위치 |
|---|---|
| 요구사항 정의서 (기획 검증 결과 포함) | [`docs/기획서.md`](docs/기획서.md) 1~2절, 7절(기획 검증) |
| Agent 설계 문서 | [`docs/기획서.md`](docs/기획서.md) 4절, [`docs/검증/graph.mmd`](docs/검증/graph.mmd)(LangGraph 구조 출력), [`docs/설계지도.html`](docs/설계지도.html) |
| Tool 정의서 | [`docs/기획서.md`](docs/기획서.md) 3절, `python -m app.tools.registry`(catalog 출력) |
| API 명세서 | [`docs/기획서.md`](docs/기획서.md) 5.3절, [`docs/검증/openapi.json`](docs/검증/openapi.json), 실행 중 `/docs` |
| 실행 로그 샘플 | [`docs/검증/logs/R1.json`](docs/검증/logs/R1.json)(대표 1건), [`R2.json`](docs/검증/logs/R2.json)·[`R3b.json`](docs/검증/logs/R3b.json)(재계획) |
| 검증 결과 · 셀프 체크리스트 | [`docs/검증/검증기록.md`](docs/검증/검증기록.md), [`docs/검증/셀프체크리스트.md`](docs/검증/셀프체크리스트.md) |

## 알려진 한계

- 대화 기억과 Tool cache는 서버 메모리에 있어 재시작하면 사라집니다.
- "지역은 상관없어"처럼 조건을 **지우는** 요청은 표현하지 못합니다(병합 규칙이 null을 "말하지 않음"으로 읽음).
- 자정을 넘는 상영 계획은 다루지 않습니다.
- 박스오피스 순위와 러닝타임은 날마다 바뀌어, R2·R3의 시각은 실행일 데이터에 맞춰 조정이 필요할 수 있습니다.
