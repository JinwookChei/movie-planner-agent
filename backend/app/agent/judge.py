"""제약 판단 순수 함수: need = show_tm + AD_BUFFER, latest_start 역산. (M3, F3008)

Agent 관점에서 이 파일의 핵심
  "이 영화를 사용자 시간 안에 볼 수 있나?"를 LLM이 아니라 코드가 판단한다.
  LLM은 산수를 틀리고, 상영 시각을 지어낸다. 그래서 숫자는 전부 여기서 만들고,
  respond 노드의 LLM은 여기서 나온 숫자를 '그대로 옮겨 적기'만 한다 (기획서 4.3, F3011).

순수 함수다: LLM, 네트워크, 전역 상태를 쓰지 않는다. 같은 입력 → 항상 같은 출력 → 테스트가 쉽다.

입력 형태
  movie (execute 노드가 Tool 결과를 정리해 만든다):
    {"movie_nm": "폭설", "show_tm": 88 | None, "genres": ["드라마"], "source": "KOBIS" | "TMDB",
     "error": None | {"code": "NOT_FOUND", "message": "..."}}   # 상세 조회 실패 시 error가 채워진다
  conditions (analyze 노드가 사용자 말에서 뽑는다. 못 뽑은 값은 None):
    {"start_after": "15:00" | None, "end_before": "19:00" | None, "genre": "드라마" | None, ...}

TODO 표시가 있는 곳을 직접 채운다. 나머지는 완성된 뼈대다.
확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.agent.judge
      루트에서           .venv\\Scripts\\python -m pytest tests/test_judge.py -v
"""
from typing import Any

from app.config import AD_BUFFER
from app.tools.base import NO_RUNTIME, NOT_FOUND

FIT, UNFIT, EXCLUDED = "fit", "unfit", "excluded"

# 제외 사유 문구 (화면에 그대로 나간다). 없는 code는 기본 문구를 쓴다.
EXCLUDE_REASONS = {
    NO_RUNTIME: "러닝타임 정보가 없어 판단하지 않았다.",
    NOT_FOUND: "영화 상세 정보를 찾지 못해 판단하지 않았다.",
}


# ── 시각 ↔ 분 변환 (완성) ────────────────────────────────
def to_min(hhmm: str) -> int:
    """'14:30' → 870. 자정부터 몇 분인지. 형식이 틀리면 ValueError."""
    h, m = hhmm.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"시각 범위를 벗어났다: {hhmm!r}")
    return h * 60 + m


def to_hhmm(minutes: int) -> str:
    """870 → '14:30'. 24시를 넘으면 다음 날로 넘기지 않고 그대로 둔다(자정 넘김은 범위 밖)."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ── TODO 1: 영화 한 편 판단 ─────────────────────────────
def judge_one(movie: dict, start: int | None, end: int | None, genre: str | None,
              ad_buffer: int = AD_BUFFER) -> tuple[str, dict]:
    """영화 한 편을 (분류, 항목) 으로 돌려준다. 분류는 FIT / UNFIT / EXCLUDED 중 하나.
    start, end는 이미 분으로 바뀐 값이다(없으면 None).

    판단 순서 (위에서부터 먼저 걸리는 것으로 정한다)
      ① 제외(EXCLUDED): 판단할 근거가 없는 영화. 판단하지 않고 사유만 남긴다 (F3003).
           - movie["error"]가 있으면       → code = error["code"]
           - show_tm이 None이면            → code = NO_RUNTIME
         항목: {"movie_nm", "code", "reason", "source"}
         
      ② 장르(UNFIT): genre 조건이 있는데 그 영화 genres에 없으면 → 사유 "장르: ..."
           함정: TMDB 영화는 genres가 []로 온다(장르를 모름). '모름'과 '다름'은 다르다.
                 모르는 걸 다르다고 빼 버리면 신작 보충(재계획 2)이 장르 조건에서 전부 탈락한다.
                 어떻게 처리할지 직접 정하고, 정한 이유를 주석으로 남긴다.

      ③ 시간: need = show_tm + ad_buffer
           - end가 None   → FIT. 끝 제약이 없으니 무엇이든 된다.
                            start가 있으면 expected_end = start + need 를 함께 준다.
           - end가 있으면 → latest_start = end - need  (늦어도 이때 시작해야 end 전에 끝난다)
               · start가 있으면: start ≤ latest_start 이면 FIT, 아니면 UNFIT
               · start가 없으면: latest_start ≥ 0 이면 FIT (analyze가 되묻기로 막지만 방어용)
         FIT 항목:   {"movie_nm", "show_tm", "need_min", "latest_start": "HH:MM"|None,
                      "expected_end": "HH:MM"|None, "source"}
         UNFIT 항목: {"movie_nm", "show_tm", "need_min", "reason", "source"}

    규칙
      - UNFIT 사유에는 반드시 숫자를 넣는다. 예: "필요 161분 > 가능 150분"
        이 숫자가 답변의 근거가 된다. "시간이 안 맞음"만 쓰면 사용자가 조건을 어떻게 바꿀지 모른다.
      - 출력 시각은 to_hhmm으로 바꿔 문자열로 준다. 계산은 끝까지 분(int)으로 한다.
      - 경계값: start == latest_start 는 맞음인가? (딱 맞게 시작하면 딱 맞게 끝난다)
    힌트: movie.get("error"), `genre in movie.get("genres", [])`, 조기 return으로 ①→②→③ 순서 표현.
    """
    name =  movie.get("movie_nm")
    show_tm = movie.get("show_tm")
    genres = movie.get("genres")
    source = movie.get("source")
    error = movie.get("error")
    code = None
    
    # ① 제외: 실패 사유(error)를 먼저 본다. show_tm부터 보면 NOT_FOUND도 NO_RUNTIME으로 뭉개진다.
    if error is not None:
        code = error.get("code")
    elif show_tm is None:
        code = NO_RUNTIME
    if code:
        # 화면용 사유는 code로 정한다. error["message"]는 개발자용 상세라 화면에 내보내지 않는다 (NF3002).
        reason = EXCLUDE_REASONS.get(code, "상세 조회에 실패해 판단하지 않았다.")
        return EXCLUDED, {"movie_nm": name, "code": code, "reason": reason, "source": source}

    need = show_tm + ad_buffer  # 여기까지 왔으면 show_tm은 반드시 int다 (①에서 걸렀다)

    def unfit(reason: str) -> tuple[str, dict]:
        return UNFIT, {"movie_nm": name, "show_tm": show_tm, "need_min": need,
                       "reason": reason, "source": source}

    # ② 장르: 조건이 있고, 장르를 '알고 있는데' 다를 때만 뺀다.
    #   genres가 []인 영화(TMDB 신작)는 장르를 '모르는' 것이지 '다른' 것이 아니다.
    #   빼 버리면 재계획 2(TMDB 보충)가 장르 조건이 있을 때 항상 0편이 된다.
    #   그래서 통과시키고, evaluate가 notes에 "장르를 확인하지 못한 영화가 있다"고 밝힌다.
    if genre and genres and genre not in genres:
        return unfit(f"장르: '{genre}'이(가) 아니다 ({', '.join(genres)})")

    # ③ 시간
    if end is None:
        # 끝 제약이 없다 → 무엇이든 볼 수 있다. 시작 시각을 알면 끝나는 시각을 알려 준다.
        expected_end = to_hhmm(start + need) if start is not None else None
        return FIT, {"movie_nm": name, "show_tm": show_tm, "need_min": need,
                     "latest_start": None, "expected_end": expected_end, "source": source}

    latest_start = end - need  # 늦어도 이 시각에는 시작해야 end 전에 끝난다
    if start is not None:
        # 경계: start == latest_start 는 맞음. 딱 맞게 시작하면 딱 end에 끝난다("전에 끝나야"를 '늦지 않게'로 본다).
        if start > latest_start:
            return unfit(f"필요 {need}분 > 가능 {end - start}분")
    elif latest_start < 0:
        return unfit(f"필요 {need}분 > 끝 시각({to_hhmm(end)})까지 {end}분")

    return FIT, {"movie_nm": name, "show_tm": show_tm, "need_min": need,
                 "latest_start": to_hhmm(latest_start), "expected_end": None, "source": source}


# ── 전체 판단 ─────────────────────────────────────────
def evaluate(movies: list[dict], conditions: dict, ad_buffer: int = AD_BUFFER) -> dict:
    """후보 전체를 판단해 judge 노드가 State["judged"]에 넣을 dict를 만든다.

    반환: {"fit": [...], "unfit": [...], "excluded": [...],
           "window_min": int | None, "shortest_need": int | None, "notes": [str]}
      - window_min    : 사용자가 쓸 수 있는 시간(분). 상한 도달 시 "X분 범위에는..."의 X (R3)
      - shortest_need : 판단한 영화 중 가장 짧은 필요 시간. "가장 짧은 영화가 N분이라..."의 N (R3)
    """
    start_s, end_s = conditions.get("start_after"), conditions.get("end_before")
    start = to_min(start_s) if start_s else None
    end = to_min(end_s) if end_s else None
    genre = conditions.get("genre") or None

    judged: dict[str, Any] = {FIT: [], UNFIT: [], EXCLUDED: [], "notes": []}
    for movie in movies:
        kind, item = judge_one(movie, start, end, genre, ad_buffer)
        judged[kind].append(item)

    # 판단 요약값: 상한 도달 시 "가장 짧은 영화가 N분이라 X분 범위에는 맞는 영화가 없다"의 N과 X (R3)
    judged["window_min"] = end - start if start is not None and end is not None else None
    # excluded에는 need_min이 없으므로 fit + unfit만 본다. 판단한 영화가 0편이면 None.
    judged["shortest_need"] = min((m["need_min"] for m in judged[FIT] + judged[UNFIT]), default=None)

    if genre and any(not m.get("genres") for m in movies if m.get("show_tm") is not None):
        judged["notes"].append(f"장르를 확인할 수 없는 영화(신작 등)는 '{genre}' 조건 없이 시간만으로 판단했다.")

    # 안내 문구 (완성). respond가 그대로 전한다.
    if start is None:
        judged["notes"].append("시작 가능 시각을 말하지 않아 끝 시각 기준으로만 판단했다.")
    if end is None:
        judged["notes"].append("끝나야 하는 시각이 없어 모든 영화를 볼 수 있는 것으로 보고 예상 종료 시각을 함께 적었다.")
    if judged[FIT]:
        judged["notes"].append("상영 시각표는 확인할 수 없다. 각 영화의 latest_start 이전에 시작하는 회차를 고른다.")
    return judged


# ── 스스로 확인 ────────────────────────────────────────
if __name__ == "__main__":
    def mv(nm: str, show_tm: int | None, genres: list[str] | None = None, source: str = "KOBIS",
           error: dict | None = None) -> dict:
        return {"movie_nm": nm, "show_tm": show_tm, "genres": genres or [], "source": source, "error": error}

    def one(movie: dict, start: str | None, end: str | None, genre: str | None = None) -> tuple[str, dict]:
        return judge_one(movie, to_min(start) if start else None, to_min(end) if end else None, genre, 20)

    checks = [
        ("변환 to_min / to_hhmm",          lambda: to_min("14:30") == 870 and to_hhmm(870) == "14:30"),
        ("TODO1 14~19시, 132분 → fit 16:28", lambda: one(mv("A", 132), "14:00", "19:00") == (FIT, {
            "movie_nm": "A", "show_tm": 132, "need_min": 152, "latest_start": "16:28",
            "expected_end": None, "source": "KOBIS"})),
        ("TODO1 15~17:30, 141분 → unfit",   lambda: one(mv("B", 141), "15:00", "17:30")[0] == UNFIT),
        ("TODO1 unfit 사유에 숫자",          lambda: "161" in one(mv("B", 141), "15:00", "17:30")[1]["reason"]),
        ("TODO1 끝 없음 → fit, 종료 17:20",  lambda: one(mv("C", 120), "15:00", None)[1]["expected_end"] == "17:20"),
        ("TODO1 경계 start == latest → fit", lambda: one(mv("D", 100), "15:00", "17:00")[0] == FIT),
        ("TODO1 경계 1분 모자람 → unfit",     lambda: one(mv("D", 101), "15:00", "17:00")[0] == UNFIT),
        ("TODO1 러닝타임 없음 → excluded",   lambda: one(mv("E", None), "15:00", "19:00")[1]["code"] == NO_RUNTIME),
        ("TODO1 상세 실패 → excluded",       lambda: one(mv("F", None, error={"code": "NOT_FOUND", "message": "x"}),
                                                     "15:00", "19:00")[1]["code"] == "NOT_FOUND"),
        ("TODO1 장르 다름 → unfit",          lambda: one(mv("G", 90, ["코미디"]), "15:00", "19:00", "드라마")[0] == UNFIT),
        ("TODO2 window / shortest",         lambda: (lambda j: j["window_min"] == 150 and j["shortest_need"] == 152)(
                                                     evaluate([mv("A", 132), mv("B", 141), mv("E", None)],
                                                              {"start_after": "15:00", "end_before": "17:30"}, 20))),
        ("TODO2 판단 대상 0편 → None",       lambda: evaluate([mv("E", None)], {"start_after": "15:00",
                                                     "end_before": "17:30"}, 20)["shortest_need"] is None),
    ]
    for label, check in checks:
        try:
            result = "PASS" if check() else "FAIL"
        except Exception as e:  # 아직 TODO면 여기로 온다
            result = f"FAIL ({type(e).__name__})"
        print(f"{result:<26} {label}")
