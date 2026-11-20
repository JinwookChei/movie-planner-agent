# MoviePlanner Agent

"내일 강남에서 2시 이후에 보고 7시 전에 끝나야 해"처럼 **시간 조건**을 말하면, 실제 박스오피스·러닝타임·영화관 정보를 API로 확인해 **그 시간 안에 볼 수 있는 영화**와 **늦어도 몇 시에 시작해야 하는지**를 알려 주는 Agent입니다. 맞는 영화가 없으면 스스로 계획을 바꿔 다시 찾고(최대 3회), 추천한 영화는 관람 계획으로 저장·수정·삭제할 수 있습니다.

SSAFY AI 코어 프로젝트 3회차 · LangGraph 1.2.10 · langchain-openai 1.4.3 (SSAFY GMS) · FastAPI · SQLite · HTML/CSS/JS

## 빠른 실행

```powershell
# 1) 가상환경과 의존성 (Python 3.11)
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt

# 2) 키 설정: .env.example을 .env로 복사한 뒤 값 4개를 채운다 (아래 "API 키 발급과 설정")
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

## 전체 기능

### 필수 기능 (F3001~F3019) — 모두 구현

| 영역 | 기능 | 요구사항 |
|---|---|---|
| **Tool** | 외부 API Tool 4종(박스오피스, 영화 상세, TMDB 신작, 카카오 영화관). LLM에는 이름·설명·입력 스키마만 넘기고, 실패는 예외 대신 공통 실패 코드로 돌려준다 | F3001~F3004 |
| **요청 분석** | 의도(plan/fact/chat)와 조건(날짜·시작·끝 시각·지역·장르) 추출. 말하지 않은 값은 비워 두고, 시작 시각 없이 끝 시각만 있으면 Tool을 부르기 전에 되묻는다 | F3005 |
| **계획·실행** | Tool 호출 순서를 계획하고, 앞 Tool의 출력(movie_cd)을 다음 Tool의 입력으로 넘긴다. 인사처럼 Tool이 필요 없으면 계획을 건너뛴다. 하나가 실패해도 사유를 남기고 계속한다 | F3006, F3007 |
| **시간 판단** | 러닝타임 + 광고 20분이 시간 안에 들어가는지 계산하고, 상영 시각을 지어내지 않고 "늦어도 몇 시에 시작"을 역산해 제시한다 | F3008 |
| **재계획** | 맞는 영화가 0편이면 후보 확장 → TMDB 신작 → 장르 완화 순으로 다시 찾는다. 이미 받은 결과는 재사용하고, 최대 3회 후 근거를 대며 조건 조정을 되묻는다 | F3009 |
| **대화 맥락** | "같은 조건으로 홍대에서"처럼 바뀐 조건만 갱신한다. 최근 3턴과 추천 요약을 기억한다 | F3010 |
| **답변** | 추천 영화, 영화관, 판단 기준, 안 맞는 이유, 제외 사유를 함께 보여 준다 | F3011 |
| **이벤트·로깅 Agent** | 메인 Agent는 이벤트를 발행만 하고, 로깅 Agent가 LLM 없이 세션별로 SQLite에 저장한다. 저장이 실패해도 대화는 정상 | F3012, F3013 |
| **로그 API** | 목록·상세·삭제 (수정 API 없음) | F3014 |
| **관람 계획** | 저장·조회·수정·삭제. 같은 날 시간이 겹쳐도 저장하고 겹침을 알린다 | F3015, F3016 |
| **API·화면** | FastAPI(대화·계획·로그·상태 경로 분리, Pydantic) + 화면 3개. 실행 과정 패널에 단계와 Tool 이력, 재계획 지점(빨강), 처리 중, Tool 실패, 키 누락 안내 | F3017~F3019 |

### 심화 기능 (F3020~F3023) — 적용하지 않음
이상 징후 판정(F3020), Planner/Worker 분리(F3021), RAG Tool(F3022), 캘린더 Tool(F3023)은 선택하지 않았습니다. 대신 검증 과정에서 LLM이 가끔 어기던 규칙을 코드로 옮겨 답변의 정확성을 보강했습니다([`docs/산출물/5_검증결과/검증기록.md`](docs/산출물/5_검증결과/검증기록.md) 2절).

## API 키 발급과 설정

키는 루트 `.env`에만 둡니다. 소스·커밋·화면에 나오지 않습니다(NF3001, NF3002). `.env`는 `.gitignore`에 들어 있습니다.

| 변수 | 발급처 | 없으면 |
|---|---|---|
| `GMS_API_KEY` | SSAFY GMS | 요청 분석·답변 불가 (화면에 빨간 배너) |
| `KOBIS_API_KEY` | [KOBIS 오픈API](https://www.kobis.or.kr/kobisopenapi/homepg/main/main.do) | 영화 목록·러닝타임 불가. 재계획하지 않고 다시 시도를 안내 |
| `TMDB_API_KEY` | [TMDB](https://www.themoviedb.org/settings/api) (v3 API Key) | 신작 보충만 빠짐 |
| `KAKAO_REST_API_KEY` | [Kakao Developers](https://developers.kakao.com/) REST API 키 | 영화관 안내만 빠짐 (영화 판단은 정상) |

**발급 절차 요약**
1. **SSAFY GMS**: 교육 과정에서 받은 GMS 키를 그대로 씁니다(OpenAI 호환 게이트웨이, 모델 `gpt-5.4-nano`).
2. **KOBIS**: KOBIS 오픈API 사이트에서 회원가입 → [키 발급/관리] → 키 발급 신청(심사 없이 바로 발급) → 발급된 키를 복사합니다.
3. **TMDB**: themoviedb.org 회원가입 → [설정 > API]에서 개발자 키 신청(용도: 교육·비상업) → **API Key (v3 auth)** 값을 복사합니다. "읽기 액세스 토큰"(v4)이 아닙니다.
4. **카카오**: Kakao Developers 로그인 → [내 애플리케이션 > 애플리케이션 추가] → 앱 키의 **REST API 키**를 복사합니다. 로컬 API(키워드로 장소 검색)는 별도 신청 없이 쓸 수 있습니다.
5. 루트에서 `copy .env.example .env` 후 네 값을 채우고 백엔드를 다시 시작합니다. 화면 상단에 키 누락 배너가 없으면 정상입니다(`GET /api/health`로도 확인).

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

**LLM과 코드의 분리**: LLM은 말을 이해하고(의도·조건 추출) 문장을 만드는 일만 합니다. Tool 실행 순서, 시간 계산, 맞음/안 맞음 판단, 재계획 여부와 전략, 조건 병합, 되묻기 판정은 모두 코드가 합니다. 검증하면서 LLM이 가끔 어기는 규칙(키 이름 노출, 조정 질문 여부, 오전/오후 해석)도 코드로 옮겼습니다 → [`docs/산출물/5_검증결과/검증기록.md`](docs/산출물/5_검증결과/검증기록.md) 2절.

**이벤트 분리**: 메인 Agent는 `emit()`만 하고 구독자를 모릅니다. 로깅 Agent는 `main.py`에서만 연결되며, 저장에 실패해도 대화와 화면의 실행 과정은 정상입니다(`LOG_FAIL=1`로 시험).

## 검증

```powershell
.venv\Scripts\python -m pytest -q                # 27 passed · LLM·외부 API 없이 판단 로직과 API 전체
.venv\Scripts\python scripts\run_scenarios.py    # 대표 요청 10건을 실제 API로 실행 → docs/산출물/5_검증결과/
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

자세한 기록은 [`docs/산출물/5_검증결과/검증기록.md`](docs/산출물/5_검증결과/검증기록.md)(기대와 다른 점, 원인, 수정)와 [`docs/산출물/5_검증결과/결과.md`](docs/산출물/5_검증결과/결과.md)(자동 기록)에 있습니다.

## 제출물 위치

명세서가 요구한 산출물은 모두 [`docs/산출물/`](docs/산출물/README.md)에 번호 순서대로 모았습니다(문서와 근거 자료를 함께). 구현 소스코드와 이 README는 저장소 루트에 있습니다.

| 제출물 | 위치 |
|---|---|
| 1) 요구사항 정의서 (기획 검증 결과 포함) | [`docs/산출물/1_요구사항정의서/요구사항정의서.md`](docs/산출물/1_요구사항정의서/요구사항정의서.md) |
| 2) Agent 설계 문서 | [`docs/산출물/2_Agent설계문서/Agent설계문서.md`](docs/산출물/2_Agent설계문서/Agent설계문서.md), [`docs/산출물/2_Agent설계문서/graph.mmd`](docs/산출물/2_Agent설계문서/graph.mmd)(LangGraph 구조 출력), [`docs/설계지도.html`](docs/설계지도.html)(동작 시각화) |
| 3) Tool 정의서 | [`docs/산출물/3_Tool정의서/Tool정의서.md`](docs/산출물/3_Tool정의서/Tool정의서.md) |
| 4) API 명세서 | [`docs/산출물/4_API명세서/API명세서.md`](docs/산출물/4_API명세서/API명세서.md), [`docs/산출물/4_API명세서/openapi.json`](docs/산출물/4_API명세서/openapi.json), 실행 중 `/docs` |
| (참고) 구현 전 설계 | [`docs/기획서.md`](docs/기획서.md) — 제출 문서는 이것을 최종 구현에 맞춰 다시 쓴 것이다 |
| 실행 로그 샘플 | [`docs/산출물/5_검증결과/logs/R1.json`](docs/산출물/5_검증결과/logs/R1.json)(대표 1건), [`R2.json`](docs/산출물/5_검증결과/logs/R2.json)·[`R3b.json`](docs/산출물/5_검증결과/logs/R3b.json)(재계획) |
| 검증 결과 · 셀프 체크리스트 | [`docs/산출물/5_검증결과/검증기록.md`](docs/산출물/5_검증결과/검증기록.md), [`docs/산출물/6_셀프체크리스트/셀프체크리스트.xlsx`](docs/산출물/6_셀프체크리스트/셀프체크리스트.xlsx)(제출 양식), [`셀프체크리스트.md`](docs/산출물/6_셀프체크리스트/셀프체크리스트.md)(같은 내용과 증빙 링크), [`docs/산출물/5_검증결과/캡처/`](docs/산출물/5_검증결과/캡처) |

## 알려진 한계

- 대화 기억과 Tool cache는 서버 메모리에 있어 재시작하면 사라집니다.
- "지역은 상관없어"처럼 조건을 **지우는** 요청은 표현하지 못합니다(병합 규칙이 null을 "말하지 않음"으로 읽음).
- 자정을 넘는 상영 계획은 다루지 않습니다.
- 박스오피스 순위와 러닝타임은 날마다 바뀌어, R2·R3의 시각은 실행일 데이터에 맞춰 조정이 필요할 수 있습니다.
