# Agent 설계 문서 · MoviePlanner Agent

- 작성: 최진욱
- 작성일: 2026-11-20
- 대상: 명세 6장 "2) Agent 설계 문서"
- 기준: 최종 구현(`backend/app/agent/`, `backend/app/events/`). 구현 전 설계는 `docs/기획서.md` 4·5절에 있다.

---

## 1. 전체 구조

```
화면(index.html) ──POST /api/chat──▶ api/chat.py ──▶ graph.run_turn(session_id, message, run_id)
                                                        │  ① memory.get / get_cache  (이전 맥락)
                                                        │  ② LangGraph 그래프 실행
                                                        │  ③ memory.update / set_cache
                                                        ▼
          START → analyze ─┬─(chat · 되묻기)───────────────────────────────▶ respond → END
                  (LLM+코드) └─(plan · fact)→ plan → execute → judge ─┬─(맞는 영화 있음 · 멈춤)─▶ respond
                                           (코드)  (코드)    (코드)   │                    (LLM+코드)
                                             ▲                       │
                                             └──(재계획: 0편, 최대 3회)┘

모든 노드 ── emit(event, payload) ──▶ EventBus ──┬─▶ 로깅 Agent → SQLite event_logs
                                                ├─▶ /api/chat의 임시 수집기 → 응답 events[] → 실행 과정 패널
                                                └─▶ (시험용) 터미널 출력
```

LangGraph 구조 출력은 `docs/산출물/2_Agent설계문서/graph.mmd`에 있다(`make_graph().get_graph().draw_mermaid()`).

### 1.1 LLM과 코드의 역할 분리
- **LLM**은 두 곳에서만 쓴다.
  - analyze: 문장을 의도와 조건으로 바꾼다.
  - respond: 판단 결과를 문장으로 만든다.
- **나머지는 모두 코드**다. 이유는 둘이다.
  - 규칙으로 정할 수 있는 일은 코드가 더 정확하고 테스트할 수 있다.
  - LLM은 계산을 틀리거나 값을 지어낼 수 있다.

| 일 | 수행자 | 이유 |
|---|---|---|
| 의도 분류, 조건 추출, Tool 후보 고르기 | LLM | 자연어 이해 |
| LLM 출력 검사(형식, 오전·오후 보정), 이전 조건 병합, 되묻기 판정 | 코드 | 규칙. LLM이 가끔 어긴다 |
| Tool 순서와 인자, 실행, cache | 코드 | 의존 관계가 정해져 있다 |
| 시간 계산, 맞음·안 맞음·제외 | 코드 | 계산은 틀리면 안 된다 |
| 재계획 여부와 전략, 상한 | 코드 | 전략 3개, 순서 고정 |
| 답변 문장 | LLM | 자연어 생성 |
| 답변에 꼭 넣을 문장, 질문 여부, 영어 키 이름 제거 | 코드 | 검증에서 LLM이 빠뜨리거나 어긴 사례를 확인함 |

**LLM을 호출하는 조건**
- analyze: 매 요청 1회.
- respond: plan·fact·chat일 때 1회.
- 되묻기일 때는 respond가 LLM을 부르지 않는다. 코드가 만든 문장을 그대로 답한다.

**LLM 호출이 실패하면**
- analyze가 실패하면 chat으로 처리하고 "LLM 연결 실패"를 안내한다.
- respond가 실패하면 judge 결과로 코드가 대체 답변을 만든다(`_fallback_answer`).
- 서비스는 멈추지 않는다(NF3003).

## 2. 실행 단계와 단계별 입출력

노드는 모두 `(state, emit) → 바뀐 키만 담은 dict`이다. LangGraph가 돌려준 dict를 State에 합친다.

### 2.1 State (`nodes.State`)

| 키 | 채우는 곳 | 내용 |
|---|---|---|
| session_id, run_id, user | run_turn | 세션, 요청 1건의 ID, 이번 메시지 |
| memory | run_turn | 이전 조건, 추천 요약, 최근 3턴 |
| intent, intent_reason | analyze | plan / fact / chat, LLM이 말한 이유 |
| conditions | analyze | watch_date, start_after, end_before, region, genre (+ movie_query) |
| llm_tools | analyze | LLM이 고른 Tool 이름 (그대로 믿지 않는다) |
| ask | analyze | 되묻기 문구. 있으면 Tool 없이 respond로 |
| options | plan, judge | top_n(5→10), use_tmdb, relax_genre — 재계획이 바꾼다 |
| plan | plan | `[{tool, args}]` 순서 목록 |
| cache | run_turn, execute | `"Tool이름\|json(인자)"` → Tool 결과. 재계획과 다음 턴에서 재사용 |
| results | execute | Tool 이름 → 결과(상세는 리스트) |
| candidates | execute | judge에 넣을 영화 `{movie_nm, show_tm, genres, source, error}` |
| judged | judge | fit[], unfit[], excluded[], window_min, shortest_need, notes[] |
| next, replan_count, replan_history | judge | 다음 노드("plan"·"respond"), 재계획 횟수와 기록 |
| answer, recommendations, theaters, notices | respond | 답변과 화면에 보낼 값 |

### 2.2 노드별 입력 → 처리 → 출력

**① analyze (LLM + 코드)** · `nodes.analyze`
- **입력**: `user`, `memory`
- **처리**
  1. **LLM 프롬프트**에 넣는 것: 오늘 날짜와 요일, 앞으로 7일의 날짜표, "말하지 않은 값은 null" 규칙, 오후 규칙과 예시, 이전 조건과 최근 대화, `catalog()`.
  2. LLM이 `with_structured_output(Analysis)`로 답한다: intent, reason, 조건 5개, tools, movie_query.
  3. **코드 검사**
     - `_valid_date`, `_valid_time`: 형식이 틀리거나 25:00처럼 범위를 벗어나면 None.
     - `_fix_afternoon`: 끝 시각이 시작보다 이른 오전이면 오후로 고친다.
  4. **병합**(plan·fact이고 이전 조건이 있을 때): `memory.merge_conditions`로 null이 아닌 값만 덮어쓴다.
  5. **되묻기 판정**: end_before만 있음, 또는 start_after ≥ end_before.
- **출력**: intent, intent_reason, conditions, llm_tools, ask
- **이벤트**: `llm.decision{intent, reason, conditions, tools, code_fixes?}`, `memory.merge{changed}`, `agent.ask{question}`, (실패 시) `llm.error`
- **다음**: `route_after_analyze`가 ask가 있거나 chat이면 respond로, 아니면 plan으로 보낸다.

**② plan (코드)** · `nodes.plan`
- **입력**: conditions, llm_tools, options
- **처리**
  - LLM이 고른 Tool 중 레지스트리에 없는 이름은 버린다(`dropped`).
  - 순서는 의존 관계로 **고정**한다.
    1. `get_box_office(target_date=관람일 또는 오늘, top_n)`
    2. `get_movie_detail(movie_cd=<1의 출력>)`
    3. `search_new_releases`: 관람일 ≥ 오늘이고, (처음 계획에서 LLM이 골랐거나 재계획이 켰을 때)
    4. `search_theaters(region)`: 지역을 말했을 때만
- **출력**: plan, options
- **이벤트**: `plan.created{steps, dropped, options, replan_count}`

**③ execute (코드)** · `nodes.execute`
- **입력**: plan, cache
- **처리**
  - Tool마다 `call()`을 부른다. cache 키가 있으면 재사용(`tool.cache_hit`)하고, 없으면 호출해 소요 시간을 잰다.
  - 박스오피스의 `movies[].movie_cd`를 하나씩 상세 조회에 넘긴다(F3007).
  - 상세가 실패해도 그 영화에 `error`를 달아 후보에 남기고 계속한다.
  - TMDB `from_date`는 박스오피스 `basis_date + 1일`로 정한다.
  - KOBIS와 TMDB 후보는 **공백을 뺀 제목**이 같으면 하나로 합친다(KOBIS 우선).
- **출력**: results, candidates, cache
- **이벤트**: `tool.call{tool, args}`, `tool.result{tool, ok, code, elapsed_ms}`, `tool.cache_hit{tool, args}`

**④ judge (코드)** · `nodes.judge` → `judge.evaluate`, `replan`
- **입력**: candidates, conditions, options(relax_genre면 genre를 None으로)
- **처리**: 영화마다 `judge_one`을 적용한다(F3008).
  1. **제외**: error가 있거나 show_tm이 None이면 사유와 함께 제외한다(NO_RUNTIME, NOT_FOUND). 값을 지어내지 않는다.
  2. **장르**: genre가 있고 그 영화의 장르가 알려져 있는데 다르면 안 맞음. 장르를 모르면 통과시키고 notes에 적는다.
  3. **시간**
     - `need = show_tm + AD_BUFFER(20분)`
     - end_before가 있으면 `latest_start = end_before − need`이고, `start_after ≤ latest_start`이면 맞음이다. 아니면 "필요 N분 > 가능 M분"으로 안 맞음이다.
     - end_before가 없으면 맞음이고 `expected_end = start_after + need`를 준다.
  - 그다음 재계획을 결정한다(3절).
- **출력**: judged, next, replan_count, replan_history, options
- **이벤트**: `judge.result{fit, unfit, excluded, window_min, shortest_need}`, `replan{count, strategy, reason}`, `replan.stop{reason, replan_count}`
- **다음**: `route_after_judge`는 judge가 정한 `next`를 **읽기만** 한다. LangGraph의 분기 함수가 바꾼 State는 버려지기 때문에, 결정은 노드에서 한다.

**⑤ respond (LLM + 코드)** · `nodes.respond`
- **입력**: ask, intent, judged, results, conditions, replan_history
- **처리**
  - **되묻기**: ask를 그대로 답한다. LLM을 부르지 않는다.
  - **chat**: 할 수 있는 일 4가지와 할 수 없는 일(시간표·좌석·가격·예매·평점)을 코드가 적어 LLM에 넘긴다. 없는 기능을 소개하지 않게 하려는 것이다.
  - **plan·fact**
    1. 코드가 근거를 정리한다(`_summarize`). Tool 실패는 쉬운 말 안내(`notices`)로 바꾼다.
    2. 코드가 꼭 넣을 문장을 만든다: 집계 기준일("11월 19일 집계 기준이라 추정"), 재계획 경로.
    3. 코드가 마지막 문단의 방향을 정한다: 0편이면 조정 질문, 1편 이상이면 질문 금지, 후보 Tool 실패면 다시 시도 안내.
    4. LLM에는 **한국어 키로 바꾼 근거**만 넘긴다. 규칙: 숫자를 그대로 쓴다, 상영 시각을 지어내지 않는다, 영화와 영화관을 묶지 않는다.
    5. 코드가 마지막에 보장한다
       - 영어 키 이름과 지시 문구 줄을 지운다(`_no_keys`).
       - 빠진 꼭 넣을 문장을 붙인다.
       - 0편인데 조정 질문이 없으면 붙인다.
- **출력**: answer, recommendations(fit + unfit 요약), theaters, notices

## 3. 계획 수립과 재계획의 판단 조건 (F3006, F3009, NF3004)

### 3.1 계획을 건너뛰는 경우
- intent가 chat일 때 → Tool 없이 respond로 간다.
- ask(되묻기)가 있을 때 → Tool을 부르기 전에 respond로 간다. 시작 시각 없이 Tool을 부르면 결국 시각을 지어내게 되기 때문이다.

### 3.2 재계획 결정 (`replan.py`, judge 노드에서 위에서부터 차례로)

| 순서 | 조건 | 결과 |
|---|---|---|
| 멈춤 1 | 맞는 영화 ≥ 1편 | respond (재계획 없음) |
| 멈춤 2 | 박스오피스가 **INVALID_KEY / UPSTREAM_ERROR** | respond. 다시 해도 같으므로 재계획하지 않는다. "잠시 후 다시 시도"를 안내 |
| 멈춤 3 | `replan_count ≥ MAX_REPLAN(3)` | respond. "재계획 상한 3회 도달" |
| 전략 1 | `top_n < 10` | **expand_top_n**: 후보 5편 → 10편 |
| 전략 2 | TMDB를 아직 안 썼고, 관람일 ≥ 오늘 | **add_tmdb**: 신작 보충 |
| 전략 3 | genre가 있고 아직 안 풀었음 | **relax_genre**: 선호 조건 완화. 제약 조건(끝 시각)은 풀지 않는다 |
| 멈춤 4 | 적용할 전략 없음 | respond. "더 시도할 재계획 전략 없음" |

**결과 재사용**
- 재계획할 때 이미 받은 결과는 cache에서 꺼낸다.
- 예(R2): 10편으로 넓힐 때 앞 5편의 상세는 `tool.cache_hit`이고, 새 5편만 호출한다.

**0편으로 멈추면**
- `notes`에 근거 숫자를 남긴다(가장 짧은 필요 시간, 가능한 시간).
- respond가 이 숫자를 대며 "시작을 앞당기거나 끝 시각을 늦출 수 있는지" 되묻는다.

**이중 안전장치**: `MAX_REPLAN=3`과 별개로, LangGraph `recursion_limit=25`가 무한 루프를 막는다.

## 4. 메인 Agent와 로깅 Agent의 관계, 이벤트 흐름 (F3012, F3013)

### 4.1 분리 원칙
- 메인 Agent(노드들)는 `emit("이벤트", **payload)`만 부른다. **누가 듣는지 모른다.**
  - `app/agent/`의 어떤 파일도 `logger_agent`를 import하지 않는다(자가 확인 12번이 검사).
- 연결은 `main.py`의 시작 훅에서 `logger_agent.start()` 한 줄로만 한다. 이 줄을 지워도 대화는 정상이다.
- `EventBus.publish`는 구독자에서 난 예외를 삼키고 다음 구독자로 넘어간다.

### 4.2 메시지 형식
```json
{"event": "tool.result", "session_id": "d79c8d4bf1bb", "run_id": "6f2a691edebb",
 "seq": 10, "ts": "2026-11-20T15:40:53.112", "payload": {"tool": "get_box_office", "ok": true, "code": null, "elapsed_ms": 785}}
```
- 노드는 `event`와 `payload`만 준다.
- `bus.emitter(session_id, run_id)`가 만든 emit 함수가 나머지(session_id, run_id, seq, ts)를 붙인다. 그래서 노드 코드는 로깅을 몰라도 된다.

### 4.3 이벤트 목록

| 이벤트 | 발행 위치 | payload |
|---|---|---|
| session.start / session.end | run_turn | message / ok, intent, replan_count, answer_preview, elapsed_ms |
| step.enter / step.exit | 모든 노드(`_traced` 래퍼) | step / step, elapsed_ms |
| llm.decision | analyze | intent, **reason**, conditions, tools, code_fixes? — LLM의 판단과 이유 |
| llm.error | analyze, respond | step, error(예외 이름만) |
| memory.merge | analyze | prev, new, merged, changed |
| agent.ask | analyze | reason, question |
| plan.created | plan | steps, dropped, options, replan_count |
| tool.call / tool.cache_hit / tool.result | execute | tool, args / tool, ok, code, elapsed_ms |
| judge.result | judge | fit[], unfit, excluded, window_min, shortest_need |
| replan / replan.stop | judge | count, strategy, reason / reason, replan_count |
| error | run_turn | where, code (상세 메시지 없음) |

### 4.4 구독자

| 구독자 | 하는 일 | 실패하면 |
|---|---|---|
| **로깅 Agent** (`logger_agent.handle`) | **LLM 없이** 받는 즉시 `event_logs`에 INSERT. 세션 단위로 발생 순서대로, payload 원본 JSON 그대로 저장 | handle 안에서 삼킨다(이중으로 bus도 삼킴). `LOG_FAIL=1`로 시험했을 때 대화와 화면은 정상 |
| **/api/chat 임시 수집기** | 요청 전에 run_id를 정하고 같은 run_id만 모아 응답 `events[]`로 보낸다. 요청이 끝나면 구독을 뗀다 | DB를 거치지 않으므로 로그 저장이 실패해도 화면의 실행 과정 패널은 정상 |

로그는 목록·상세·삭제만 있다(`/api/logs`). 로그는 실행의 증거라서 수정 API는 두지 않는다(F3014).

## 5. 대화 맥락(Memory) (F3010)

### 5.1 보관 범위 (`memory.py`)

| 보관 | 내용 | 이유 |
|---|---|---|
| `conditions` | 마지막 조건 5개 | "같은 조건으로…"를 해석 |
| `recommendations` | 추천 **요약** `[{movie_nm, show_tm, fit, latest_start}]` | "아까 그 영화"를 해석 |
| `turns` | 최근 3턴 `[{user, answer}]` (`deque(maxlen=3)`) | 흐름 파악. 프롬프트가 끝없이 길어지지 않게 |
| (별도) `_CACHE` | 세션별 Tool 결과 cache | 같은 조회를 다시 하지 않음. API 원본이 아니라 Tool이 고른 값 |

- **보관하지 않는 것**: Tool·API 원본 응답, API 키, 이벤트.
- **저장 위치**: 서버 메모리의 dict(session_id 기준). 서버를 재시작하면 사라진다.

### 5.2 갱신 방식
1. `run_turn`이 매 턴 `memory.get(session_id)`로 **복사본**을 꺼내 State에 넣는다. 그래프 안에서 실수로 고쳐도 저장소는 안전하다.
2. analyze에서 LLM이 이번 메시지의 값만 채운다(나머지는 null).
3. 코드 `merge_conditions(prev, new)`가 **null이 아닌 값만 덮어쓴다**.
   - 예: "같은 조건으로 홍대에서" → `changed=["region"]`.
   - 날짜와 시각은 유지된다. chat 턴은 병합하지 않는다.
4. 그래프가 끝난 뒤 `run_turn`이 `memory.update` 한 번으로 저장한다.
   - 조건이 비어 있는 턴(인사)은 이전 조건을 지우지 않는다.
5. **cache 재사용 효과(R7)**: 날짜가 같으면 박스오피스와 상세 6건은 cache_hit이고, 바뀐 지역의 영화관만 새로 호출한다.

**알려진 한계**
- "지역은 상관없어"처럼 값을 **지우는** 요청은 표현할 수 없다. null을 "말하지 않음"으로 읽기 때문이다.
- cache에는 만료 시간이 없다.

## 6. 심화 구조
적용하지 않았다(F3020~F3023 미선택). Planner/Worker 분리 대신 위 2·3절처럼 **판단 규칙을 코드로 고정한 단일 그래프**를 쓴다.
