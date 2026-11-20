"""재계획 규칙: 맞는 영화가 0편일 때 '다음에 무엇을 바꿔 다시 할지' 고른다. (M5, F3009, NF3004)

재계획을 LLM에게 맡기지 않는 이유: 전략이 3개뿐이고 순서가 정해져 있다(기획서 4.4).
규칙으로 정할 수 있는 것은 코드가 한다 → 무한 반복이 없고, 테스트할 수 있다.

전략표 (순서대로, 적용할 수 없는 전략은 건너뛴다)
  1 expand_top_n  : 박스오피스 후보 5편 → 10편      (이미 10편이면 건너뜀)
  2 add_tmdb      : TMDB 신작 보충                  (이미 썼거나, 관람일이 지난 날이면 건너뜀)
  3 relax_genre   : 선호 조건(장르) 풀기            (장르 조건이 없거나 이미 풀었으면 건너뜀)

재계획하지 않는 경우
  - 맞는 영화가 1편 이상
  - 후보 Tool(박스오피스)이 INVALID_KEY / UPSTREAM_ERROR → 다시 해도 똑같이 실패한다
  - 상한(MAX_REPLAN) 도달, 또는 남은 전략 없음
"""
from datetime import date

from app import config

FATAL = {"INVALID_KEY", "UPSTREAM_ERROR"}  # 재시도해도 결과가 같은 실패


def stop_reason(state: dict) -> str | None:
    """재계획하면 안 되는 이유. 재계획해도 되면 None."""
    judged = state.get("judged", {})
    if judged.get("fit"):
        return "맞는 영화 있음"
    box = state.get("results", {}).get("get_box_office", {})
    if box and not box.get("ok") and box["error"]["code"] in FATAL:
        return f"후보 Tool 치명적 실패({box['error']['code']})"
    if state.get("replan_count", 0) >= config.MAX_REPLAN:
        return f"재계획 상한 {config.MAX_REPLAN}회 도달"
    return None


def next_strategy(state: dict) -> tuple[str, dict] | None:
    """적용할 다음 전략 (이름, options 패치). 남은 전략이 없으면 None."""
    opts = state.get("options", {})
    cond = state.get("conditions", {})
    watch = cond.get("watch_date") or date.today().isoformat()

    if opts.get("top_n", config.BOX_TOP_N) < 10:
        return "expand_top_n", {"top_n": 10}
    if not opts.get("use_tmdb") and date.fromisoformat(watch) >= date.today():
        return "add_tmdb", {"use_tmdb": True}
    if cond.get("genre") and not opts.get("relax_genre"):
        return "relax_genre", {"relax_genre": True}
    return None
