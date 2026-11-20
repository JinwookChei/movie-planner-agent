# Tool 정의서 · MoviePlanner Agent

- 작성: 최진욱
- 작성일: 2026-11-20
- 대상: 명세 6장 "3) Tool 정의서"
- 기준: 최종 구현 `backend/app/tools/`. 아래 이름·설명·입력 스키마는 `python -m app.tools.registry`가 출력하는 catalog와 같다.

---

## 1. 공통 규약 (F3001, NF3002, NF3003)

### 1.1 응답 형식
모든 Tool은 둘 중 하나를 돌려준다. **예외를 밖으로 던지지 않는다.**
```json
{"ok": true,  "data": { ... }}
{"ok": false, "error": {"code": "INVALID_KEY", "message": "KOBIS API 키가 올바르지 않다: …"}}
```

### 1.2 Tool 객체 (`base.Tool`)

| 필드 | 뜻 | LLM에 넘기나 |
|---|---|---|
| `name` | Tool 이름 | ○ |
| `description` | **언제 부르는지**를 쓴다(기능 설명이 아님) | ○ |
| `parameters` | 입력 JSON Schema | ○ |
| `func` | 실제 실행 함수 | ✕ (JSON이 될 수 없고, LLM은 실행하지 않는다) |
| `kind` | `"external"`(외부 API) / `"local"`(로컬 서버 API) | ✕ (내부 분류). 이 서비스의 Tool 4종은 모두 external |

- `catalog()`는 `name·description·parameters`만 모아 analyze 프롬프트에 넣는다.
- `TOOLS[name].run(**args)`는 execute 노드(코드)가 부른다.

### 1.3 `Tool.run()`의 예외 처리

| 예외 | 바뀌는 코드 | 메모 |
|---|---|---|
| `TypeError` (인자 이름이 틀리거나 빠짐) | `BAD_ARGUMENT` | 원인은 서버 콘솔(traceback)에만 남기고 응답에는 넣지 않는다 |
| 그 밖의 모든 `Exception` | `UPSTREAM_ERROR` | 네트워크 오류, 응답 구조 변화 등 |

### 1.4 실패 코드

| 코드 | 뜻 | Agent의 처리 |
|---|---|---|
| `BAD_ARGUMENT` | 입력이 규격과 다름 (형식, 범위, 빈 값) | 호출 **전에** 검사해 멈춘다. 재계획하지 않는다 |
| `INVALID_KEY` | 키가 없거나 틀림 | **재계획하지 않는다**(다시 해도 같음). 후보 Tool이면 "다시 시도" 안내, 보조 Tool이면 그 정보만 빼고 진행 |
| `UPSTREAM_ERROR` | 시간 초과(10초), 서버 오류, JSON 아님 | INVALID_KEY와 같게 처리 |
| `NOT_FOUND` | 없는 식별자 | 그 영화만 판단에서 제외(사유 표시) |
| `NO_DATA` | 조회는 됐지만 결과가 비어 있음 | 후보 Tool이면 재계획 전략으로 계속 시도. 보조 Tool이면 안내만 |
| `NO_RUNTIME` | 러닝타임이 비어 있음 (도메인 전용) | 그 영화만 판단하지 않고 제외한다. 값을 지어내지 않는다 |

- 화면에는 코드 대신 쉬운 안내만 나간다.
  - 예: "영화관 정보를 가져오지 못했습니다(API 키가 없거나 올바르지 않음)."
- 모든 `fail()` 메시지는 `scrub()`을 거친다. 키 값 자체와 `key=…`, `KakaoAK …`, `appKey(…)` 패턴을 `****`로 가린다.
  - 카카오 401 응답의 메시지에는 키 원문이 들어 있다. M0에서 실제로 확인했다.
- 외부 호출에는 `timeout=10`초를 둔다.

---

## 2. Tool 목록

### 2.1 `get_box_office` · KOBIS · F3002

- **설명 (LLM이 읽음)**: 볼 영화를 아직 정하지 않았을 때, 관람 후보 영화 목록(일일 박스오피스 순위)을 얻으려고 가장 먼저 부른다. 결과의 movie_cd를 get_movie_detail에 넘긴다. 미래 날짜는 최근 집계일 기준 추정치다.
- **입력**

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `target_date` | string `YYYY-MM-DD` | 아니요 | 관람 날짜. 없으면 오늘 |
| `top_n` | integer 1~10 | 아니요 (기본 5) | 후보 수. 재계획 1단계에서 10 |

- **출력**
```json
{"requested_date": "2026-11-21", "basis_date": "2026-11-19", "is_estimated": true,
 "movies": [{"movie_cd": "20247221", "movie_nm": "폭설", "rank": 1, "open_dt": "2026-11-12"}]}
```
- **기준일 규칙**: `basis_date = min(target_date, 어제)`.
  - KOBIS는 오늘·미래 날짜에 빈 리스트를 준다(M0 확인).
  - 요청일과 다르면 `is_estimated=true`이고, 답변에 "○월 ○일 집계 기준이라 추정"을 밝힌다.
- **실패**

| 상황 | 코드 |
|---|---|
| 날짜 형식 오류, top_n 범위 밖 | BAD_ARGUMENT (호출 전) |
| 키 없음, `faultInfo.errorCode = 320010` (HTTP 200이어도) | INVALID_KEY |
| 그 밖의 `faultInfo` | BAD_ARGUMENT |
| 결과 목록이 비어 있음 | NO_DATA |
| 시간 초과, HTTP 오류, JSON 아님 | UPSTREAM_ERROR |

### 2.2 `get_movie_detail` · KOBIS · F3003

- **설명**: 영화코드(movie_cd)로 러닝타임과 관람등급을 확인할 때 부른다. 시간 안에 볼 수 있는지 판단하기 전에 후보마다 필요하다.
- **입력**: `movie_cd` string, 필수 — `get_box_office` 결과의 movie_cd
- **출력**
```json
{"movie_cd": "20247221", "movie_nm": "폭설", "show_tm": 88, "watch_grade": "12세이상관람가", "genres": ["드라마"]}
```
  - KOBIS의 `showTm`은 문자열("88")이라 정수로 바꾼다.
- **실패**

| 상황 | 코드 |
|---|---|
| movie_cd가 빈 값 | BAD_ARGUMENT (호출 전) |
| 응답의 movieCd가 None (없는 코드도 HTTP 200에 모든 값 None) | NOT_FOUND |
| showTm이 비었거나 숫자가 아님 | NO_RUNTIME |
| 키 오류, 통신 오류 | INVALID_KEY, UPSTREAM_ERROR (2.1과 같음) |

### 2.3 `search_new_releases` · TMDB · F3002 보충

- **설명**: 관람 날짜가 오늘 이후라서 박스오피스(어제 기준)에 아직 없는 신작을 보충할 때 부른다. from_date는 박스오피스 basis_date 다음 날, to_date는 관람 날짜다. 러닝타임(show_tm)을 함께 주므로 상세 조회는 따로 하지 않는다. 지난 날짜에는 부르지 않는다.
- **입력**

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `from_date` | string `YYYY-MM-DD` | 예 | 박스오피스 basis_date + 1일 (execute가 채움) |
| `to_date` | string `YYYY-MM-DD` | 예 | 관람 날짜 |

- **내부 동작**
  1. `discover/movie`(region=KR, release_date 범위, `with_release_type=2|3`, 인기순)에서 앞 **3편**을 고른다.
  2. 각 영화를 `movie/{id}`로 조회해 runtime을 얻는다.
  - 호출 수는 1 + 최대 3이다.
- **출력**
```json
{"from_date": "2026-11-20", "to_date": "2026-11-21",
 "movies": [{"tmdb_id": 101, "movie_nm": "신작 X", "release_date": "2026-11-21", "show_tm": 104},
            {"tmdb_id": 102, "movie_nm": "신작 Y", "release_date": "2026-11-21", "show_tm": null}]}
```
  - runtime이 0이거나 없으면 `show_tm: null`이다. 미래 개봉작은 0으로 온다(M0 확인). judge가 NO_RUNTIME으로 제외한다.
  - 한 영화의 상세가 실패해도 목록은 살린다. 단 INVALID_KEY면 바로 멈춘다.
- **실패**

| 상황 | 코드 |
|---|---|
| 형식 오류, from > to, 기간 31일 초과 | BAD_ARGUMENT (호출 전) |
| 키 없음, HTTP 401·403 | INVALID_KEY |
| HTTP 404 | NOT_FOUND |
| HTTP 400·422 | BAD_ARGUMENT |
| 기간 안 개봉작 0편 | NO_DATA |
| 그 밖의 HTTP 오류, 시간 초과 | UPSTREAM_ERROR |

### 2.4 `search_theaters` · 카카오 로컬 · F3004

- **설명**: 사용자가 지역(예: 강남, 홍대)을 말했을 때, 그 지역의 영화관 이름·주소·링크를 함께 안내하려고 부른다. 어느 영화를 상영하는지는 알 수 없다. 지역을 말하지 않았으면 부르지 않는다.
- **입력**

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `region` | string | 예 | 사용자가 말한 지역 |
| `size` | integer 1~15 | 아니요 (기본 5) | 결과 수 |

- **내부 동작**: `query="{region} 영화관"`, `category_group_code=CT1`(영화관), 헤더 `Authorization: KakaoAK {키}`
- **출력**
```json
{"theaters": [{"name": "CGV 강남", "address": "서울 강남구 …", "url": "http://place.map.kakao.com/…", "phone": "…"}]}
```
  - address는 도로명 주소가 있으면 그것을, 없으면 지번 주소를 쓴다. phone이 비면 null이다.
- **결과 없음 형식**: 빈 목록 대신 `NO_DATA`로 통일한다("'○○' 지역에서 영화관을 찾지 못했다").
- **실패**

| 상황 | 코드 |
|---|---|
| region이 빈 값, size 범위 밖 | BAD_ARGUMENT (호출 전) |
| 키 없음, HTTP 401·403 | INVALID_KEY (메시지의 appKey는 scrub) |
| HTTP 400 | BAD_ARGUMENT |
| 결과 0건 | NO_DATA |
| 429·5xx, 시간 초과 | UPSTREAM_ERROR |

- **주의**: 영화와 영화관을 임의로 묶지 않는다. 답변 규칙과 화면 안내("상영 여부는 각 영화관에서 확인")로 지킨다.

---

## 3. Tool 사이의 데이터 연결 (F3007)

```
conditions.watch_date ─┬─▶ get_box_office ──movies[].movie_cd──▶ get_movie_detail (영화마다)
                       └─▶ (미래 날짜·전략 2) basis_date+1 ~ watch_date ─▶ search_new_releases
conditions.region ──────▶ search_theaters
```

- 순서와 인자는 plan 노드(코드)가 의존 관계로 정한다. LLM이 고른 Tool 목록은 후보로만 쓴다.
- 결과는 `results[Tool 이름]`에 모은다.
- 같은 Tool·같은 인자는 `"Tool|json(인자)"` cache로 재사용한다(재계획, 다음 턴).

### Tool로 만들지 않은 것

| 일 | 맡은 곳 | 이유 |
|---|---|---|
| 시간 계산, 제약 판단 | `agent/judge.py` | 규칙이라 LLM 판단이 필요 없다 |
| 같은 영화 합치기(공백을 뺀 제목) | execute 노드 | 〃 |
| 계획 저장, 겹침 검사 | `/api/plans` | 사용자가 버튼으로 하는 일. 명세 2.3 "모델의 판단이 필요 없는 작업은 Tool로 만들지 않는다" |

---

## 4. Tool 등록과 확장 방법

새 Tool(예: 상영 시간표 API)을 더하는 순서다. 기존 Agent 코드는 거의 바꾸지 않는다.

1. **함수 작성**: `backend/app/tools/새파일.py`
   - 입력을 먼저 검사한다(실패하면 `fail(BAD_ARGUMENT, …)`).
   - 외부 호출은 `timeout`을 두고, HTTP 상태와 본문을 공통 실패 코드로 바꾼다(`classify_error` 패턴).
   - 판단에 쓸 값만 골라 `ok({...})`로 돌려준다. 빈 값을 `""`나 0으로 채우지 않는다.
2. **Tool 객체 만들기**
```python
SHOWTIMES_TOOL = Tool(
    name="search_showtimes",
    description="사용자가 특정 영화관의 상영 시각을 알고 싶을 때 부른다. …",   # '언제 부르는지'
    parameters={"type": "object", "properties": {"theater_id": {"type": "string"}}, "required": ["theater_id"]},
    func=search_showtimes,
    kind="external",            # 로컬 서버 API면 "local"
)
```
3. **등록**: `registry.py`의 `TOOLS` 튜플에 한 줄을 추가한다. `catalog()`에 자동으로 들어가 LLM이 볼 수 있다.
4. **계획에 넣기**: `nodes.plan`의 고정 순서 목록에 언제 넣을지(조건)를 추가한다.
   - 결과를 judge에서 쓰려면 `nodes.execute`에서 candidates로 옮기는 부분을 추가한다.
5. **자가 확인**: 파일 아래 `if __name__ == "__main__":`에 정상 1회, 잘못된 입력, 틀린 키를 시험하는 블록을 둔다(기존 Tool처럼).
   - 실행: `python -m app.tools.새파일`
6. **키가 필요하면**: `.env.example`, `config.py`, `base._SECRETS`(scrub 대상), `/api/health` 응답에 함께 추가한다.

## 5. 시험 방법

```powershell
cd backend
..\.venv\Scripts\python -m app.tools.registry   # catalog 출력 (LLM에 넘기는 명세)
..\.venv\Scripts\python -m app.tools.kobis      # 14/14  (정상 · 형식 오류 · 틀린 키 · 없는 코드 · 실제 호출)
..\.venv\Scripts\python -m app.tools.kakao      # 14/14
..\.venv\Scripts\python -m app.tools.tmdb       # 15/15
```
