"""Pydantic 요청/응답 모델. (M8, F3017)

왜 모델을 따로 두는가
  - 요청 검증: 형식이 틀린 요청은 라우터 함수에 들어오기도 전에 FastAPI가 422로 돌려보낸다.
    (예: start_time="7시" → 422. 라우터 안에서 if 문으로 검사할 필요가 없다)
  - 응답 거르기: response_model에 없는 키는 응답에서 빠진다. 내부 State(cache, results …)가 새어 나가지 않는다.
  - /docs: 이 모델들이 그대로 API 명세서가 된다 (제출물 'API 명세서'의 근거).
"""
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# "HH:MM" 24시간. 두 자리로 맞춰 두면 문자열 비교("09:30" < "14:00")가 시각 비교와 같아진다 → 겹침 검사 SQL이 단순해진다
HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", examples=["14:30"])]


# ── 계획 (F3015, F3016) ─────────────────────────────
class PlanIn(BaseModel):
    movie_nm: str = Field(min_length=1, max_length=200, examples=["폭설"])
    watch_date: date = Field(examples=["2026-11-21"])
    start_time: HHMM
    end_time: HHMM
    theater: str | None = Field(None, max_length=200, examples=["CGV 강남"])
    memo: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _start_before_end(self) -> "PlanIn":
        # 자정을 넘는 상영은 다루지 않는다(범위 밖). 끝이 시작보다 빠르면 입력 실수로 본다 → 422
        if self.start_time >= self.end_time:
            raise ValueError("end_time은 start_time보다 늦어야 한다")
        return self


class PlanOut(PlanIn):
    id: int
    created_at: str
    updated_at: str


class PlanSaved(BaseModel):
    """저장·수정 결과. 겹쳐도 저장은 하고, 겹치는 계획을 함께 알린다 (F3015)."""
    plan: PlanOut
    overlaps: list[PlanOut]


# ── 대화 (F3011, F3019) ─────────────────────────────
class ChatIn(BaseModel):
    session_id: str | None = Field(None, max_length=64, description="없으면 서버가 새로 발급한다")
    message: str = Field(min_length=1, max_length=500, examples=["내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어"])


class Recommendation(BaseModel):
    """judge가 판단한 영화 한 편. 화면의 [저장] 버튼이 이 값으로 PlanIn을 만든다."""
    model_config = ConfigDict(extra="ignore")
    movie_nm: str
    show_tm: int | None = None
    need_min: int | None = None
    latest_start: str | None = None     # 늦어도 이때 시작 (end_before가 있을 때)
    expected_end: str | None = None     # 이때 끝남 (end_before가 없을 때)
    fit: bool
    reason: str | None = None           # 안 맞는 이유
    source: str | None = None           # KOBIS / TMDB


class Excluded(BaseModel):
    """판단에서 뺀 영화와 사유 (F3003, F3011)."""
    model_config = ConfigDict(extra="ignore")
    movie_nm: str | None = None
    code: str | None = None
    reason: str


class Theater(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    address: str | None = None
    url: str | None = None
    phone: str | None = None


class Event(BaseModel):
    """실행 과정 패널에 그릴 이벤트 한 건 (F3019). session_id·run_id는 ChatOut에 한 번만 둔다."""
    seq: int
    event: str
    ts: str
    payload: dict[str, Any]


class ChatOut(BaseModel):
    session_id: str
    run_id: str
    intent: str | None
    answer: str
    conditions: dict[str, Any] = {}
    recommendations: list[Recommendation] = []
    excluded: list[Excluded] = []
    theaters: list[Theater] = []
    replan_count: int = 0
    replan_history: list[dict[str, Any]] = []
    notices: list[str] = []             # Tool 실패·키 누락 안내. 오류 상세와 키는 넣지 않는다 (NF3002)
    events: list[Event] = []


# ── 로그 (F3014) ───────────────────────────────────
class LogSummary(BaseModel):
    session_id: str
    started_at: str
    event_count: int
    first_message: str | None


class LogEvent(BaseModel):
    id: int
    session_id: str
    run_id: str
    seq: int
    event: str
    ts: str
    payload: dict[str, Any]


# ── 상태 ───────────────────────────────────────────
class Health(BaseModel):
    keys: dict[str, bool]               # 키가 '있는지'만. 값은 절대 보내지 않는다
    log_fail_test: bool                 # LOG_FAIL=1 시험 중인지 (체크리스트 17 확인용)
