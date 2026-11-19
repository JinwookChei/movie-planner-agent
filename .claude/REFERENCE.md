# ref/ 자료 요약 (Claude 참고용)

원본: `ref/`. 이 요약과 원본이 다르면 원본이 우선이다.

## 1. 요구사항정의서 (`ref/AI코어프로젝트_3회차_요구사항정의서.pdf`)

**일정**: 3일 과정이다.
- 1일차: FastAPI 실습, 기획, 필수 Tool 구현. 16시까지 외부 API 3종의 호출을 확인한다.
- 2일차: 분석·계획·판단·재계획, 로깅 Agent, API와 화면을 구현한다.
- 3일차: 테스트하고 셀프 체크리스트로 점검한 뒤 제출한다.

**도메인 조건**
- 서로 다른 정보를 주는 외부 API를 3종 이상 쓰고, 출처와 이용 조건(호출 한도, 약관)을 기록한다.
- Tool 결과와 사용자 제약을 비교해야만 답할 수 있는 요청이 있어야 한다.
- 조건을 못 맞췄을 때 다시 계획하는 것(재계획)이 의미가 있어야 한다.
- 사용자가 저장·조회·수정·삭제할 결과물이 있어야 한다.

**영화 예시 구성**
- 외부 API: KOBIS(박스오피스, 영화 상세), 카카오 로컬(영화관 검색), TMDB(신작 보충)
- 대표 요청 예: "토요일 3시 이후 강남, 7시 전에 끝나야 해"
- 저장할 결과물: 관람 계획(영화, 날짜, 시작·종료 시각, 생성 일시)

**기획 단계 (2장)**
- 조건을 세 종류로 나눈다.
  - 기준 조건: 날짜, 시각
  - 선호 조건: 지역, 분류
  - 제약 조건: 이후 일정, 상한 금액
- 의도는 다섯 가지로 나눈다: 계획 수립 / 사실 조회 / 계획 확정 / 캘린더 등록 / 기타 대화.
- 희망 시각 없이 "7시 전에 끝나야 해"만 있으면 Tool을 부르기 전에 되묻는다.
- Tool 설명에는 기능이 아니라 "언제 쓰는지"를 적는다.
- 모델의 판단이 필요 없는 작업(예: 계획 저장)은 Tool로 만들지 않는다.
- 재계획: 충족하는 후보가 0개일 때만 하고, 상한은 3회다. API 키 누락처럼 다시 해도 결과가 같은 실패는 재계획하지 않는다.
- 이벤트에는 session_id를 공통으로 넣는다.
- 분리 기준: 로깅 Agent가 멈춰도 메인 Agent가 정상 응답해야 한다.
- 기획 검증
  - 대표 요청마다 "Tool 없이 LLM만으로 답하면 무엇이 틀리는지"를 한 줄로 적는다.
  - 실제 API 응답을 Tool 출력 구조와 대조한다.
  - 값이 비어 있는 응답, 결과가 없는 응답을 직접 확인한다.
  - LLM에 Tool 명세를 넘겼을 때 Tool을 고르는지 확인한다.
  - 대표 요청 하나를 설계대로 따라가 본다.

**필수 요구사항 핵심 (3장)**

| 번호 | 핵심 |
|---|---|
| F3001 | Tool 레지스트리. Agent에는 이름·설명·입력 스키마만 넘긴다. 예외를 던지지 않고 ok=false + 실패 코드로 돌려준다. 외부 API 툴과 로컬 서버 API 툴을 구분한다. |
| F3002 | 후보 목록. 날짜를 주지 않으면 기본값을 쓴다. 미래 날짜는 최근 집계일로 대신하고 추정임을 밝힌다. 다음 Tool이 쓸 식별자를 반환한다. 신작은 TMDB로 보충할 수 있다. |
| F3003 | 상세정보. 러닝타임과 등급을 가져온다. 러닝타임이 비어 있으면 NO_RUNTIME으로 돌려주고, 그 영화는 판단하지 않고 제외한다. |
| F3004 | 보조 정보(카카오 영화관). 이름·주소·링크를 돌려준다. 결과가 없을 때의 형식을 정한다. 영화와 영화관을 임의로 묶지 않는다. |
| F3005 | 기준·선호·제약 조건을 추출한다. 뽑지 못한 값은 비워 둔다. |
| F3006 | Tool과 호출 순서를 계획으로 만들고 보관한다. 인사처럼 Tool이 필요 없는 요청은 계획을 건너뛴다. |
| F3007 | 앞선 Tool의 출력을 다음 Tool의 입력으로 넘긴다. 결과는 Tool 이름을 키로 모은다. 하나가 실패해도 사유를 남기고 계속 진행한다. |
| F3008 | 필요 시간 = 러닝타임 + 광고 시간. 희망 시각부터 제약 시각 사이에 들어가는지 본다. 상영 시각은 지어내지 않고 "늦어도 몇 시에 시작해야 하는지"를 역산해 제시한다. |
| F3009 | 재계획할 때 이미 받은 Tool 결과는 다시 부르지 않는다. 상한(3회)에 도달하면 사유를 밝히고 조건 조정을 되묻는다. |
| F3010 | 대화 맥락을 기억한다. 바뀐 조건만 갱신한다. Tool 원본 응답은 보관하지 않고 요약만 남긴다. |
| F3011 | 추천 항목, 보조 정보, 판단 기준, 근거가 된 Tool 결과, 제외 사유를 함께 보여 준다. |
| F3012 | 단계 진입·종료, Tool 요청·응답·소요 시간, LLM이 내린 판단과 이유를 이벤트로 발행한다. 발행하는 쪽은 구독자를 모른다. |
| F3013 | 로깅 Agent는 LLM 없이 세션 단위로 발생 순서대로 저장한다. 원본을 그대로 남긴다. 저장에 실패해도 대화는 정상 동작해야 한다. |
| F3014 | 로그는 목록·상세·삭제만 제공한다. 수정 API는 두지 않는다. |
| F3015 | 계획을 저장한다(날짜, 생성 일시 포함). 같은 날 시간이 겹치는 계획이 있으면 저장은 하되 겹친다는 사실을 함께 알린다. |
| F3016 | 계획을 수정·삭제하고 화면에 바로 반영한다. |
| F3017 | FastAPI 경로를 대화, 계획, 로그로 나누고 Pydantic으로 요청·응답을 정의한다. |
| F3018 | 대화 화면과 계획 관리 화면을 HTML/JS 또는 Vue로 만든다. |
| F3019 | 실행 단계와 Tool 이력을 순서대로 보여 주고, 재계획 지점을 구분한다. 처리 중 상태, Tool 실패, API 키 누락 안내를 표시한다. |

**심화 (선택)**
- F3020: 로그로 이상 징후를 판정한다(같은 입력 반복 호출, 과도한 재계획, 계획에 없던 호출). 판정은 규칙으로 하고, 근거가 없으면 "판정 불가"로 남긴다. 이상이 나온 실행에만 LLM으로 원인을 3문장 이내로 진단하고, 진단은 판정과 분리해 저장한다.
- F3021: Planner와 Worker로 분리한다. 고정 인자를 둔다. Worker는 NO_RESULT, NOT_FOUND, BAD_ARGUMENT일 때만 LLM에게 재시도·건너뜀·계획 변경 중 하나를 묻는다. 치명적 실패와 AMBIGUOUS는 LLM에 넘기지 않는다. 구조 변경 전후를 비교한다.
- F3022: RAG Tool. 2회차의 Chroma를 쓰고, 색인이 없으면 NO_INDEX로 돌려준다.
- F3023: 캘린더 Tool. 로컬 구현을 먼저 만들고 Google Calendar로 교체한다. 등록 Tool은 계획 ID만 받는다.

**비기능**
- NF3001: API 키는 환경변수로만 관리한다.
- NF3002: 화면에 키와 오류 상세를 노출하지 않는다.
- NF3003: Tool이 실패해도 서비스가 중단되지 않는다.
- NF3004: 재계획 상한을 둔다.
- NF3005: 응답 시간이 사용에 지장이 없어야 한다.
- NF3006: 프론트·백엔드를 분리하고 Agent·Tool을 파일로 나눈다.
- NF3007: API 출처와 이용 조건을 기록한다.

**검증 (4장)**
- 대표 요청을 5개 이상 정하고 아래 다섯 경우를 모두 포함한다: 한 번에 충족 / 재계획 / 상한 도달 / Tool 하나 실패 / Tool 불필요.
- LangGraph는 분석 → 계획 → 실행 → 판단 → 응답 노드로 구성되어야 한다.

**제출물**
- 요구사항 정의서(기획 검증 결과 포함), Agent 설계 문서, Tool 정의서, API 명세서
- 검증 결과, 셀프 체크리스트, 소스코드, 실행 로그 샘플(대표 1건 + 재계획 사례), README.md
- lab.ssafy.com에 제출한다.

**라이브러리 버전**
- langgraph 1.2.10, langchain 1.3.14, langchain-openai 1.4.3, langchain-core 1.6.0
- openai 2.54.0, fastapi 0.141.1, uvicorn 0.52.4, pydantic 2.13.5
- chromadb 1.5.9, langsmith 0.10.17, google-api-python-client 2.201.0 등

## 2. 셀프 체크리스트 (`ref/셀프체크리스트.xlsx`)
- 32개 항목이다: Tool 1~4, 메인 Agent 5~13, 로깅 14~18, 계획 19~20, 화면 21~22, 시나리오 23~24, 비기능 25~26, 제출 27, 심화 28~32.
- 결과는 통과 / 실패 / 미확인 / 해당없음 중 하나로 적는다. 확인하지 않은 항목을 통과로 적지 않는다.
- 주목할 확인 방법
  - 로그 저장을 일부러 실패시킨 상태에서도 대화가 되는지 본다 (17번).
  - 세션 2개 이상의 로그가 섞이지 않는지 본다 (14번).
  - 브라우저 네트워크 탭에서 API 호출을 확인한다 (21번).
  - 저장소 전체에서 키 문자열을 검색한다 (25번).

## 3. 1일차 FastAPI (`ref/1일차_FastAPI/`)

**단계별 실습 파일**
- step1: 첫 서버와 `/docs`
- step2: 경로 파라미터와 쿼리 파라미터. 타입이 틀리면 422.
- step3: Pydantic으로 요청 본문 받기
- step4: `response_model`로 응답 거르기, 상태 코드 201
- step5: CRUD 빈칸 실습(본문이 `pass`). 정답은 `app/api/plans.py`에 있다.

**`app/` 구조**
- `main.py`: CORS(localhost:5500) 설정, 라우터 등록
- `schemas.py`: PlanIn(movie_nm, watch_date: date, start_time), PlanOut(PlanIn + plan_id)
- `store.py`: 메모리 dict `PLANS`. 나중에 SQLite로 바꿔도 라우터는 그대로 두는 구조.
- `api/plans.py`: `APIRouter(prefix="/api/plans")`로 CRUD 구현. 없는 ID는 404.
- `index.html`: `fetch`로 `/api/plans` 목록을 표시한다. `python -m http.server 5500`으로 띄운다.

**`tools_start.py` (내 도메인 Tool 템플릿)**
- 구성은 ① ok/fail, ② Tool 데이터클래스, ③ search_items, ④ TOOLS와 `catalog()`.
- 공통 실패 코드: BAD_ARGUMENT, INVALID_KEY, NOT_FOUND, NO_DATA, UPSTREAM_ERROR.
- 지킬 약속
  - 원본 응답을 통째로 넘기지 않고 판단에 쓸 값만 뽑는다.
  - 빈 값을 `""`나 0으로 채우지 않는다.
  - Tool은 LLM 없이 혼자 실행된다.

## 4. 2일차 Tool·Agent (`ref/2일차_Tool_에이전트/`)

**환경**
- `.env`: `GMS_API_KEY`, `KOBIS_API_KEY`
- 모델: `ChatOpenAI(model="gpt-5.4-nano", base_url="https://gms.ssafy.io/gmsapi/api.openai.com/v1")`

**파일별 내용**
- step1_ask_twice: 같은 질문을 Tool 없이 두 번 보내 LLM이 지어내는 것을 보여 준다.
- step2_tools
  - `call_kobis`는 (본문, 실패) 튜플을 돌려준다. `faultInfo`가 오면 INVALID_KEY.
  - `get_movie_detail(movie_cd)`는 NO_RUNTIME과 NOT_FOUND를 구분한다.
  - `get_box_office(target_date)`는 기준일을 min(목표일, 어제)로 잡고, `is_estimated`와 `basis_date`를 함께 돌려준다.
  - `scrub()`은 오류 문구에서 키를 가린다.
  - `TOOLS`와 `catalog()`로 등록한다.
- fake_kobis: `--fake`를 붙이면 `requests.get`을 가로채 영화 A·B·C를 돌려준다(C는 러닝타임이 비어 있음).
- step3_agent
  - `make_graph(llm_plan, llm_answer, emit)`로 그래프를 만든다: analyze →(조건 분기) dispatch → judge → respond.
  - `traced()`가 단계 진입·종료 이벤트를 보낸다.
  - `latest_start = deadline − (show_tm + 30분)`
  - `fake_plan`과 `fake_answer`가 진짜 LLM 자리를 대신한다.
- step4_agent_gms: `with_structured_output(Plan)`으로 진짜 LLM을 끼운다. 그래프는 바꾸지 않는다.
- step5_events
  - `EventBus`의 `subscribe`/`publish`. 구독자에서 난 예외는 삼킨다.
  - 메시지 형식: `{event, session_id, ts, payload}`
  - `run()`이 `session.start`와 `session.end`를 보낸다.

**샘플에 아직 없는 것 (본 구현에서 직접 만들어야 함)**
- 희망 시작 시각을 반영한 판단
- 재계획 루프와 상한
- Memory
- 카카오 영화관 Tool
- 로깅 Agent의 저장소(SQLite)
- 대화·로그 API, 대화 화면
- Tool 소요 시간 이벤트, LLM 판단과 이유 이벤트

**샘플 코드에 있는 버그 (튜터링 퀴즈로 활용)**
- `step3_agent.py`: `datetime.striptime`은 오타다 → `strptime`.
- `step3_agent.py`: 이벤트 이름 표기가 섞여 있다(`tool_call`, `tool.result`, `step.enter`).
- `step3_agent.py` dispatch: 박스오피스 이외의 Tool은 인자 없이 호출한다.
- `step4_agent_gms.py`: Plan 필드 이름이 `deadlin`(오타)이다 → `plan.get("deadline")`이 항상 None.
- `step4_response.py`: PlanOut은 `movie_name`인데 저장된 키는 `movie_nm`이다 → 응답 검증에서 500 오류.
- `step2_tools.py`
  - `requests.get`에 timeout이 없다.
  - 날짜 형식이 틀리면 `fromisoformat`의 ValueError가 UPSTREAM_ERROR로 잡힌다(BAD_ARGUMENT여야 맞다).
  - 메시지에 오타가 있다(`movid_cd`).
