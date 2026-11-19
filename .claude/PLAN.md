# 작업 계획

- 설계 근거: `docs/기획서.md`
- 단계별 구현 명세, 힌트, 예시: `docs/구현가이드.md` (M0~M10, 학습용이며 제출물 아님)
- 마감: **2026-11-20 (목) 12:00** — 필수 기능 F3001~F3019 + NF만. 심화 없음.
- 진행 방식: 튜터 모드. **모든 코드는 사용자가 작성한다.** Claude는 단계마다 구현 명세, 힌트, 예시, 완료 기준을 먼저 주고 리뷰한다. 사용자가 요청할 때만 Claude가 코드를 쓴다.
  - 🧑 = 학습 핵심 (꼭 직접 작성)
  - 🤖 = 보일러플레이트 (사용자가 "코드 작성해줘"라고 하면 Claude가 대신 써도 되는 후보)

## 현재 진행 상황 (새 세션은 여기부터 읽는다)
- 2026-11-19: 기획 완료(`docs/기획서.md`, `docs/구현가이드.md`). 
- 준비 완료: 키 4종 발급 → 루트 `.env`(GMS_API_KEY, KOBIS_API_KEY, TMDB_API_KEY, KAKAO_REST_API_KEY), `.env.example`, `.venv`(Python 3.12, requests, python-dotenv), `.vscode/settings.json`.
- M0-1 완료: `scripts/check_apis.py` KOBIS 부분(사용자 요청으로 Claude가 작성). 확인 결과: 어제 조회 HTTP 200·10건, 상세 `showTm`은 문자열('88'), 오늘 조회는 HTTP 200·**빈 리스트**(오류 아님).
- M0-2 완료: TMDB 부분(사용자 요청으로 Claude가 작성). discover(오늘~+7일, KR, type 2|3) HTTP 200·22건, 상세 `runtime`은 정수(106 등). 0/None 사례는 아직 못 봄 → M0-5에서 먼 미래 날짜로 재확인.
- M0-3 완료: 카카오 부분(사용자 요청으로 Claude가 작성). "강남 영화관" CT1 HTTP 200, total_count=22, 5건 모두 영화관(CGV·메가박스). 외부 API 3종 호출 확인 = 1일차 16시 조건 충족.
- M0-4 완료: GMS(사용자 요청으로 Claude가 작성). langchain-openai 1.4.3 설치(의존성으로 langchain-core 1.8.3, openai 2.54.0 — 명세는 core 1.6.0 → M1 requirements에서 고정). 인사 정상. "어제 박스오피스 1위" 질문엔 지어내지 않고 **조회 불가라며 거절** → 실제 1위 '폭설 88분'을 Tool 없이는 못 답함.
- M0-5 완료: 실패 응답 확인(`python scripts/check_apis.py fail`, Claude 작성). 결과와 설계 변경 3건은 기획서 7.1절. 핵심: KOBIS faultInfo는 errorCode로 구분, 없는 movieCd는 모든 값 None, 카카오 401 메시지에 키 노출, TMDB 미래작 runtime=0.
- M0 남은 일(나중에): 기획서 1.2절 호출 한도·약관(사용자가 공식 문서 확인), 7절 catalog Tool 선택 확인·R1 따라가기(M2 후), R2/R3 시각 확정(M3 때).
- M1 완료(Claude 작성): 기획서 6절 폴더·빈 파일, `backend/requirements.txt`(명세 버전 고정, 설치 확인), `config.py`(루트 .env 로딩, 상수), `db.py`(get_conn 컨텍스트, init_db → `backend/data/app.db` 테이블 2개+인덱스). `.env.example`은 루트에 둔다(기획서는 backend/).
- **지금 할 일: M2 Tool 4종** — base.py부터.
- 2026-11-19: 원격 저장소가 공개라서 `.env`, `ref/`를 커밋 기록에서 빼고 `.gitignore` 처리. 커밋을 2개로 다시 만들어 push.

## 일정

### 11/19 (오늘) — 기획 검증 + Tool
- [ ] M0 기획 검증 (기획서 7절) — 키 4종 발급, API별 1회 호출, 응답 구조 대조, 이용 조건 기록
- [x] M1 프로젝트 뼈대 🤖 — 폴더, requirements, .env.example, 루트 .gitignore, config, db
- [ ] M2 Tool 4종 (F3001~F3004)
  - [ ] base.py (ok / fail / Tool / scrub 보강) 🤖
  - [ ] kobis.py: get_box_office, get_movie_detail 🧑 (샘플 버그 수정 포함)
  - [ ] kakao.py: search_theaters 🧑
  - [ ] tmdb.py: search_new_releases 🧑
  - [ ] registry + 정상·실패 시험 스크립트 → 체크리스트 1~4 통과

### 11/19 저녁 — Agent 핵심
- [ ] M3 judge.py 계산식 + test_judge.py 🧑 (F3008)
- [ ] M4 graph / nodes: analyze → plan → execute → judge → respond 🧑 (F3005~F3007, F3011)
- [ ] M5 재계획 루프와 상한, cache 재사용 🧑 (F3009, NF3004)
- [ ] M6 memory.py, 조건 병합 🧑 (F3010)

### 11/20 오전 — 로깅·API·화면·검증
- [ ] M7 EventBus + logger_agent → SQLite 🧑 (F3012, F3013)
- [ ] M8 FastAPI: chat / plans(겹침 검사) / logs / health 🤖+🧑 (F3014~F3017)
- [ ] M9 화면 3개 🤖 (F3018, F3019)
- [ ] M10 대표 요청 R1~R8 검증 기록, 실행 로그 샘플, README, 셀프 체크리스트 (~12:00)

## 시간이 모자라면 줄이는 순서
1. TMDB Tool은 R8 하나만 위해 쓰인다 → 마지막에 구현한다. 단, 기획 검증에서 호출 확인(3종 조건)은 반드시 한다.
2. `logs.html`은 별도 화면으로 만들지 않고 대화 화면의 실행 과정 패널과 `/docs`로 대신한다.
3. `fact` 의도 → `chat`으로 합친다.

## 결정 기록
- 2026-11-19: 외부 API = KOBIS, TMDB, 카카오 로컬. KOBIS 두 엔드포인트는 1종으로 센다.
- 2026-11-19: 의도는 3종(plan / fact / chat). 계획 저장은 버튼 + API로 처리하고 Tool로 만들지 않는다.
- 2026-11-19: 재계획은 규칙 기반 전략표(확장 → TMDB → 장르 완화), 상한 3회.
- 2026-11-19: AD_BUFFER 20분, 이벤트 이름은 `영역.동작` 형식.
