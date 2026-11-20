# API 명세서 · MoviePlanner Agent

- 작성: 최진욱
- 작성일: 2026-11-20
- 대상: 명세 6장 "4) API 명세서"
- 기준
  - 최종 구현: `backend/app/main.py`, `api/*.py`, `schemas.py`(Pydantic)
  - 기계가 읽는 명세: [`docs/산출물/4_API명세서/openapi.json`](openapi.json). 서버 실행 중에는 `http://127.0.0.1:8000/docs`

---

## 1. 공통

| 항목 | 내용 |
|---|---|
| Base URL | `http://127.0.0.1:8000` (실행: `backend`에서 `..\.venv\Scripts\python -m uvicorn app.main:app --port 8000`) |
| 형식 | JSON (UTF-8) |
| CORS 허용 출처 | `http://localhost:5500`, `http://127.0.0.1:5500` (프론트 정적 서버). 메서드 GET·POST·PUT·DELETE |
| 시각 형식 | `HH:MM` 24시간, 두 자리 (`^([01]\d\|2[0-3]):[0-5]\d$`) |
| 날짜 형식 | `YYYY-MM-DD` |
| 검증 실패 | **422**. FastAPI 기본 형식 `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`. 화면은 필드 이름만 골라 쉬운 말로 바꿔 보여 준다 |
| 오류 상세 | 키, 예외 메시지, 스택은 어떤 응답에도 넣지 않는다(NF3002) |

### 경로 요약

| 영역 | 메서드 | 경로 | 설명 | 상태 코드 |
|---|---|---|---|---|
| 대화 | POST | `/api/chat` | 메시지 1건 → Agent 그래프 1회 실행 | 200, 422 |
| 계획 | GET | `/api/plans` | 목록 (watch_date, start_time 순) | 200 |
| 계획 | POST | `/api/plans` | 저장 + 겹침 알림 | **201**, 422 |
| 계획 | GET | `/api/plans/{plan_id}` | 상세 | 200, 404 |
| 계획 | PUT | `/api/plans/{plan_id}` | 수정 + 겹침 알림(자기 자신 제외) | 200, 404, 422 |
| 계획 | DELETE | `/api/plans/{plan_id}` | 삭제 | **204**, 404 |
| 로그 | GET | `/api/logs` | 세션 목록 | 200 |
| 로그 | GET | `/api/logs/{session_id}` | 세션의 이벤트 (발생 순서) | 200, 404 |
| 로그 | DELETE | `/api/logs/{session_id}` | 세션 로그 삭제 | **204** |
| 상태 | GET | `/api/health` | 키 존재 여부 | 200 |

**로그 수정 API(PUT·PATCH)는 두지 않는다**(F3014). `/api/logs/{id}`에 PUT을 보내면 405다.

---

## 2. 대화 `POST /api/chat`

화면이 메시지를 보내면 `run_turn()`으로 그래프를 한 번 실행하고, 화면에 필요한 값만 돌려준다.
- 내부 State(cache, results 등)는 `response_model`이 걸러 내보내지 않는다.
- `def`(동기) 엔드포인트다. LLM·HTTP 대기 중에도 다른 요청은 스레드 풀에서 처리된다.

### 요청 `ChatIn`

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `session_id` | string ≤ 64 | 아니요 | 없으면 서버가 새로 발급한다. 화면은 받은 값을 다음 요청부터 보낸다(대화 맥락 유지) |
| `message` | string 1~500 | 예 | 사용자 메시지 |

```json
{"session_id": null, "message": "내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어"}
```

### 응답 `ChatOut` (200)

| 필드 | 형식 | 설명 |
|---|---|---|
| `session_id`, `run_id` | string | 세션, 이번 실행(턴)의 ID |
| `intent` | `plan` \| `fact` \| `chat` \| null | LLM이 분류한 의도. 처리 실패면 null |
| `answer` | string | 답변 문장 |
| `conditions` | object | 추출·병합된 조건(값이 있는 것만): watch_date, start_after, end_before, region, genre |
| `recommendations` | `Recommendation[]` | 판단한 영화. `fit=true`가 맞는 영화 |
| `excluded` | `Excluded[]` | 판단에서 뺀 영화와 사유 (NO_RUNTIME, NOT_FOUND) |
| `theaters` | `Theater[]` | 영화관 (영화와 묶지 않음) |
| `replan_count` | int | 재계획 횟수 (0~3) |
| `replan_history` | object[] | `[{count, reason, strategy}]` (expand_top_n / add_tmdb / relax_genre) |
| `notices` | string[] | 화면용 안내 (Tool 실패, LLM 연결 실패). 오류 상세 없음 |
| `events` | `Event[]` | 이번 실행의 이벤트(실행 과정 패널용). **임시 구독자가 모으므로 로그 저장이 실패해도 채워진다** |

**Recommendation**: `movie_nm`, `show_tm`(분), `need_min`(광고 20분 포함), `latest_start`(늦어도 시작할 시각, end_before가 있을 때), `expected_end`(end_before가 없을 때), `fit`, `reason`(안 맞는 이유), `source`(KOBIS/TMDB)
**Excluded**: `movie_nm`, `code`, `reason`
**Theater**: `name`, `address`, `url`, `phone`
**Event**: `seq`, `event`, `ts`, `payload`

```json
{
  "session_id": "d79c8d4bf1bb", "run_id": "6f2a691edebb", "intent": "plan",
  "answer": "내일(2026-11-21) 강남에서 14:00 이후에 시작해 19:00 전에 끝나는 영화는 5편입니다. … 박스오피스 순위는 11월 19일 집계 기준이라 관람일의 순위와 다를 수 있습니다(추정).",
  "conditions": {"watch_date": "2026-11-21", "start_after": "14:00", "end_before": "19:00", "region": "강남"},
  "recommendations": [
    {"movie_nm": "폭설", "show_tm": 88, "need_min": 108, "latest_start": "17:12", "expected_end": null, "fit": true, "reason": null, "source": "KOBIS"}
  ],
  "excluded": [],
  "theaters": [{"name": "CGV 강남", "address": "서울 강남구 …", "url": "http://place.map.kakao.com/…", "phone": "…"}],
  "replan_count": 0, "replan_history": [], "notices": [],
  "events": [
    {"seq": 1, "event": "session.start", "ts": "2026-11-20T15:40:51.001", "payload": {"message": "내일 강남에서 …"}},
    {"seq": 3, "event": "llm.decision", "ts": "…", "payload": {"step": "analyze", "intent": "plan", "reason": "…", "conditions": {"…": "…"}, "tools": ["search_theaters"]}},
    {"seq": 9, "event": "tool.call", "ts": "…", "payload": {"tool": "get_box_office", "args": {"target_date": "2026-11-21", "top_n": 5}}}
  ]
}
```

**경우별 응답**

| 경우 | 응답의 특징 |
|---|---|
| 되묻기 | `answer` = "19:00 전에 끝나야 한다고 하셨는데, 몇 시부터 볼 수 있으세요?", recommendations 비어 있음, Tool 이벤트 없음 |
| Tool 하나 실패 (영화관) | 추천은 정상. `notices: ["영화관 정보를 가져오지 못했습니다(API 키가 없거나 올바르지 않음)."]` |
| 재계획 | `replan_count ≥ 1`, `replan_history`, events에 `replan`·`tool.cache_hit` |
| 그래프 내부 오류 | **200**으로 `intent: null`, `answer: "지금은 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."`, `notices: ["서버에서 요청을 처리하는 중 문제가 생겼습니다."]`. 500 상세를 내보내지 않는다 |

이벤트 종류와 payload는 `docs/산출물/2_Agent설계문서/Agent설계문서.md` 4.3절에 있다.

---

## 3. 계획 `/api/plans` (F3015, F3016)

저장소는 SQLite `plans` 테이블(`backend/data/app.db`)이다.

### 요청 `PlanIn`

| 필드 | 형식 | 필수 | 검증 |
|---|---|---|---|
| `movie_nm` | string 1~200 | 예 | |
| `watch_date` | date `YYYY-MM-DD` | 예 | |
| `start_time` | `HH:MM` | 예 | |
| `end_time` | `HH:MM` | 예 | **start_time < end_time** (자정을 넘는 상영은 범위 밖) |
| `theater` | string ≤ 200 | 아니요 | |
| `memo` | string ≤ 500 | 아니요 | |

### 응답 `PlanOut` = PlanIn + `id`, `created_at`, `updated_at`

### 겹침 규칙
- 같은 `watch_date`에서 **`A.start < B.end AND B.start < A.end`**이면 겹침이다.
- 끝과 시작이 닿는 것(15:50 끝, 15:50 시작)은 겹침이 아니다.
- **겹쳐도 저장한다.** 응답의 `overlaps`로 알린다.
- 수정할 때는 자기 자신을 빼고 검사한다.

### `POST /api/plans` → 201 `PlanSaved`
```json
// 요청
{"movie_nm": "폭설", "watch_date": "2026-11-21", "start_time": "14:00", "end_time": "15:48", "theater": "CGV 강남"}
// 응답 (같은 날 14:00~15:48 계획이 이미 있을 때)
{"plan": {"id": 4, "movie_nm": "폭설", "watch_date": "2026-11-21", "start_time": "14:00", "end_time": "15:48",
          "theater": "CGV 강남", "memo": null, "created_at": "2026-11-20T15:41:08", "updated_at": "2026-11-20T15:41:08"},
 "overlaps": [{"id": 3, "movie_nm": "폭설", "watch_date": "2026-11-21", "start_time": "14:00", "end_time": "15:48", "…": "…"}]}
```

### `GET /api/plans` → 200 `PlanOut[]` (watch_date, start_time, id 순)
### `GET /api/plans/{plan_id}` → 200 `PlanOut` / 404 `{"detail": "계획 9번이 없습니다."}`
### `PUT /api/plans/{plan_id}` → 200 `PlanSaved` / 404 / 422
요청 본문은 `PlanIn` 전체다. `updated_at`이 갱신된다.
### `DELETE /api/plans/{plan_id}` → 204 (본문 없음) / 404

---

## 4. 실행 로그 `/api/logs` (F3014)

로깅 Agent가 SQLite `event_logs`(session_id, run_id, seq, event, ts, payload JSON)에 저장한 것을 **읽고 지우기만** 한다.

### `GET /api/logs` → 200 `LogSummary[]` (최근 세션 먼저)
```json
[{"session_id": "d79c8d4bf1bb", "started_at": "2026-11-20T15:40:51.001", "event_count": 77,
  "first_message": "내일 3시 이후에 보고 4시 45분 전에 끝나야 해"}]
```
`first_message`는 그 세션의 첫 `session.start` 이벤트 payload에서 꺼낸다.

### `GET /api/logs/{session_id}` → 200 `LogEvent[]` (id 순 = 발생 순서) / 404
```json
[{"id": 1201, "session_id": "d79c8d4bf1bb", "run_id": "6f2a691edebb", "seq": 1,
  "event": "session.start", "ts": "2026-11-20T15:40:51.001", "payload": {"message": "…"}}]
```
한 세션에 턴이 여러 개면 `run_id`로 구분된다. 다른 세션의 이벤트는 섞이지 않는다.

### `DELETE /api/logs/{session_id}` → 204

---

## 5. 상태 `GET /api/health`

화면이 키 누락 배너를 띄우는 데 쓴다. **키 값은 어떤 형태로도(앞 글자, 길이 포함) 보내지 않는다.**
```json
{"keys": {"gms": true, "kobis": true, "tmdb": true, "kakao": false}, "log_fail_test": false}
```
`log_fail_test`가 true면 `LOG_FAIL=1`(로그 저장 실패 시험)로 실행 중이라는 뜻이다.

---

## 6. 시험

```powershell
.venv\Scripts\python -m pytest -q tests/test_api.py   # 18개: CRUD·겹침 경계·422·로그 분리·PUT 405·LOG_FAIL·구독 해제·health·CORS
```
화면에서 실제로 호출한 기록은 `docs/산출물/5_검증결과/캡처/21_네트워크기록_*.png`에 있다.
