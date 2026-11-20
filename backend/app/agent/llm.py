"""LLM 연결과 LLM이 채울 양식. (M4, F3005, F3011)

이 Agent에서 LLM이 하는 일은 딱 두 가지다 (기획서 4.1).
  1) analyze: 사용자 말 → 의도 + 조건 + Tool 후보   (구조화 출력: Analysis 양식을 채운다)
  2) respond: judge가 계산한 결과 → 사람이 읽을 문장  (자유 문장)
시간 계산, Tool 순서, 재계획 판단은 LLM에게 맡기지 않는다. 그건 코드(nodes.py, judge.py)가 한다.
"""
from functools import lru_cache
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app import config


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """GMS 경유 ChatOpenAI. 처음 부를 때 한 번만 만든다. 키가 없으면 여기서 예외 → 호출한 노드가 잡는다."""
    if not config.GMS_API_KEY:
        raise RuntimeError("GMS_API_KEY가 설정되지 않았다.")
    return ChatOpenAI(
        model=config.MODEL,
        api_key=config.GMS_API_KEY,
        base_url=config.GMS_BASE_URL,
        timeout=config.LLM_TIMEOUT,
    )


# ── analyze가 LLM에게 받는 양식 (구조화 출력) ─────────────────
# Field(description=...)는 LLM이 읽는 '작성 요령'이다. 프롬프트의 일부라고 생각하면 된다.
class Analysis(BaseModel):
    intent: Literal["plan", "fact", "chat"] = Field(
        description="plan=시간·장소 조건으로 볼 영화를 고르는 요청, fact=영화 정보(순위·러닝타임 등) 질문, "
                    "chat=인사·잡담 등 Tool이 필요 없는 말")
    reason: str = Field(description="그 의도로 판단한 이유 한 문장")
    watch_date: str | None = Field(None, description="관람 날짜 YYYY-MM-DD. '내일', '토요일'은 오늘 날짜 기준으로 바꾼다. 말하지 않았으면 null")
    start_after: str | None = Field(None, description="이 시각 이후에 볼 수 있다 HH:MM(24시간). '3시'는 문맥상 오후면 15:00. 말하지 않았으면 null")
    end_before: str | None = Field(None, description="이 시각 전에 끝나야 한다 HH:MM(24시간). 말하지 않았으면 null")
    region: str | None = Field(None, description="지역 이름(예: 강남). 말하지 않았으면 null")
    genre: str | None = Field(None, description="원하는 장르(예: 드라마, 코미디). 말하지 않았으면 null")
    tools: list[str] = Field(default_factory=list, description="쓸 Tool 이름 목록. Tool이 필요 없으면 []")
    movie_query: str | None = Field(None, description="fact 의도에서 묻는 영화 제목. 없으면 null")


def analyze_with_llm(prompt: str) -> Analysis:
    return get_llm().with_structured_output(Analysis).invoke(prompt)


def write_with_llm(prompt: str) -> str:
    return get_llm().invoke(prompt).content
