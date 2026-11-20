"""대화 맥락: 세션별로 '이전 조건 + 추천 요약 + 최근 3턴'을 기억한다. (M6, F3010)

왜 필요한가
  그래프는 요청 한 번마다 빈 State로 새로 시작한다. 그래서 그냥 두면
  "같은 조건으로 홍대에서"를 받았을 때 날짜·시각을 모른다. 이전 턴의 조건을 어딘가 남겨야 한다.

보관하는 것 (기획서 4.5)
  - conditions      : 마지막 조건 (watch_date, start_after, end_before, region, genre)
  - recommendations : 마지막 추천 요약 [{movie_nm, show_tm, fit, latest_start}]
  - turns           : 최근 3턴 [{user, answer}]
보관하지 않는 것: Tool 원본 응답, API 키, 이벤트
  - 단, Tool '정제된 출력'의 cache는 세션별로 따로 둔다(_CACHE). 같은 세션에서 같은 조회를 다시 하지 않으려는 것이고,
    API 원본이 아니라 Tool이 고른 값만 들어 있다. memory(맥락)와는 섞지 않는다.

저장 위치: 서버 메모리의 dict. 서버를 재시작하면 사라진다(알려진 한계).
"""
from collections import deque
from copy import deepcopy

MAX_TURNS = 3
COND_KEYS = ("watch_date", "start_after", "end_before", "region", "genre")

_STORE: dict[str, dict] = {}
_CACHE: dict[str, dict] = {}


def _empty() -> dict:
    return {"conditions": {}, "recommendations": [], "turns": deque(maxlen=MAX_TURNS)}


def get(session_id: str) -> dict:
    """세션 기억의 복사본. 그래프 안에서 실수로 고쳐도 저장소는 안전하다."""
    m = _STORE.get(session_id) or _empty()
    return {"conditions": dict(m["conditions"]), "recommendations": deepcopy(m["recommendations"]),
            "turns": list(m["turns"])}


def merge_conditions(prev: dict, new: dict) -> dict:
    """new에서 None이 아닌 값만 prev 위에 덮어쓴다. "같은 조건으로 홍대에서" → region만 바뀐다.

    병합을 LLM이 아니라 코드가 하는 이유: LLM에게 "이전 조건을 유지하라"고 맡기면 가끔 값을 빠뜨린다.
    알려진 한계: 사용자가 값을 '지우고' 싶을 때("지역은 상관없어")는 표현할 수 없다.
    """
    merged = {k: prev.get(k) for k in COND_KEYS}
    for k in COND_KEYS:
        if new.get(k) is not None:
            merged[k] = new[k]
    return merged


def update(session_id: str, conditions: dict, recommendations: list[dict], user: str, answer: str) -> None:
    m = _STORE.setdefault(session_id, _empty())
    if conditions:
        m["conditions"] = {k: conditions.get(k) for k in COND_KEYS}
    if recommendations:
        # 원본 판단 결과가 아니라 요약만 남긴다
        m["recommendations"] = [{"movie_nm": r.get("movie_nm"), "show_tm": r.get("show_tm"),
                                 "fit": r.get("fit"), "latest_start": r.get("latest_start")}
                                for r in recommendations]
    m["turns"].append({"user": user, "answer": answer})  # deque(maxlen=3): 넘치면 오래된 것부터 빠진다


def get_cache(session_id: str) -> dict:
    return dict(_CACHE.get(session_id, {}))


def set_cache(session_id: str, cache: dict) -> None:
    _CACHE[session_id] = dict(cache)


def clear(session_id: str) -> None:
    _STORE.pop(session_id, None)
    _CACHE.pop(session_id, None)
