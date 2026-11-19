"""KOBIS Tool: get_box_office, get_movie_detail. (M2-2, F3002, F3003)

Agent 관점에서 이 파일의 핵심은 두 가지다.
  1) API마다 제각각인 실패 신호를 '공통 실패 코드'로 바꾸는 판단 (TODO 1, 3)
  2) Agent가 다음 판단에 쓸 값만 골라, 믿을 수 있는 형태로 돌려주기 (TODO 2, 3)
나머지(HTTP 호출, 입력 검사, Tool 명함)는 완성된 뼈대다.

확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.tools.kobis
"""
from datetime import date, datetime, timedelta
from typing import Any

import requests

from app import config
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NO_DATA,
    NO_RUNTIME,
    NOT_FOUND,
    UPSTREAM_ERROR,
    Tool,
    ToolResult,
    fail,
    ok,
)

KOBIS_MAX_ITEMS = 10  # 일일 박스오피스는 최대 10위까지 온다


# ── 공통 호출 ──────────────────────────────────────────
def _call_kobis(path: str, params: dict) -> tuple[dict | None, ToolResult | None]:
    """KOBIS를 호출해 (본문, None) 또는 (None, 실패결과)를 돌려준다."""
    key = config.KOBIS_API_KEY
    if not key:
        return None, fail(INVALID_KEY, "KOBIS API 키가 설정되지 않았다.")

    try:
        resp = requests.get(
            f"{config.KOBIS_BASE_URL}/{path}",
            params={"key": key, **params},
            timeout=config.HTTP_TIMEOUT,
        )
    except requests.RequestException as e:  # 시간 초과, 연결 실패
        return None, fail(UPSTREAM_ERROR, f"KOBIS 호출 실패: {type(e).__name__}")

    if resp.status_code >= 400:
        return None, fail(UPSTREAM_ERROR, f"KOBIS 응답 오류: HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError:
        return None, fail(UPSTREAM_ERROR, "KOBIS 응답이 JSON이 아니다.")

    # ── TODO 1: KOBIS 고유의 실패 신호를 공통 실패 코드로 바꾸기 ──────────
    # M0에서 확인한 사실 (기획서 7.1절):
    #   KOBIS는 실패해도 HTTP 200을 주고, 본문에 "faultInfo"를 넣는다.
    #     키 오류        → {"faultInfo": {"errorCode": "320010", "message": "유효하지않은 키값입니다."}}
    #     날짜 형식 오류  → {"faultInfo": {"errorCode": "320103", "message": "날싸형식에 ..."}}
    # 할 일: body에 faultInfo가 있으면
    #   errorCode가 "320010" → (None, fail(INVALID_KEY, ...))
    #   그 밖의 errorCode     → (None, fail(BAD_ARGUMENT, ...))
    # 왜 나누나? INVALID_KEY는 '다시 해도 똑같이 실패' → Agent가 재계획하지 않는다 (기획서 3.2, 4.4).
    #            BAD_ARGUMENT는 '입력을 고치면 될 수도' → 원인이 다르면 Agent의 다음 행동도 달라야 한다.
    # 메시지에 faultInfo의 message를 넣어도 된다 (fail이 scrub한다).

    return body, None


# ── 입력 검사 (LLM이 만든 인자는 믿지 않는다) ────────────────
def _parse_date(value: str) -> date | None:
    """'YYYY-MM-DD' → date. 형식이 틀리면 None."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# ── TODO 2: 실제 조회 기준일 정하기 (F3002) ──────────────────
def basis_date(target: date, today: date) -> tuple[date, bool]:
    """사용자가 원한 날짜(target)로 실제 조회할 날짜와 '추정 여부'를 정한다.

    M0에서 확인한 사실: 오늘 날짜로 조회하면 오류가 아니라 '빈 리스트'가 온다(집계 전).
    그래서 박스오피스는 '어제'까지만 실제 데이터가 있다.

    할 일:
      - 어제 = today - 1일
      - 실제 기준일 = target과 어제 중 '더 이른 날'
      - is_estimated = 실제 기준일이 target과 다르면 True
        (예: 토요일 계획인데 어제 기준 순위를 쓰면 '추정'이라고 답변에 밝혀야 한다)
    반환: (실제 기준일, is_estimated)

    순수 함수(입력만 보고 결과를 냄, today도 인자로 받음)로 둔 이유: 날짜를 바꿔 가며 테스트하기 쉽다.
    """
    raise NotImplementedError("TODO 2")


# ── Tool 함수 1: 박스오피스 ─────────────────────────────
def get_box_office(target_date: str = "", top_n: int = config.BOX_TOP_N) -> ToolResult:
    today = date.today()
    if target_date:
        target = _parse_date(target_date)
        if target is None:
            return fail(BAD_ARGUMENT, f"target_date는 YYYY-MM-DD 형식이어야 한다: {target_date!r}")
    else:
        target = today  # 날짜를 안 주면 오늘 (F3002 기본값)

    if not isinstance(top_n, int) or not 1 <= top_n <= KOBIS_MAX_ITEMS:
        return fail(BAD_ARGUMENT, f"top_n은 1~{KOBIS_MAX_ITEMS} 사이 정수여야 한다: {top_n!r}")

    basis, is_estimated = basis_date(target, today)
    body, err = _call_kobis(
        "boxoffice/searchDailyBoxOfficeList.json",
        {"targetDt": basis.strftime("%Y%m%d"), "itemPerPage": KOBIS_MAX_ITEMS},
    )
    if err:
        return err

    raw = body.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
    if not raw:
        return fail(NO_DATA, f"{basis.isoformat()} 박스오피스 결과가 비어 있다.")

    # 원본을 통째로 넘기지 않고, 다음 Tool과 판단에 필요한 값만 고른다.
    movies = [
        {
            "movie_cd": m.get("movieCd"),  # get_movie_detail의 입력으로 이어진다 (F3007)
            "movie_nm": m.get("movieNm"),
            "rank": int(m["rank"]) if m.get("rank", "").isdigit() else None,
            "open_dt": m.get("openDt") or None,  # 빈 값을 ""로 두지 않는다
        }
        for m in raw[:top_n]
    ]
    return ok({
        "requested_date": target.isoformat(),
        "basis_date": basis.isoformat(),
        "is_estimated": is_estimated,
        "movies": movies,
    })


# ── TODO 3: 상세 응답을 Agent가 믿을 수 있는 값으로 바꾸기 (F3003) ─────
def to_detail(info: dict) -> ToolResult:
    """KOBIS movieInfo(dict) → ok({...}) 또는 fail(NOT_FOUND / NO_RUNTIME).

    M0에서 확인한 사실 (기획서 7.1절):
      - 없는 movieCd를 넣으면 movieInfo가 '비어 있지 않고', 모든 값이 None인 dict로 온다.
        → `if not info:`로는 못 잡는다. 무엇을 보고 판단해야 할까?
      - showTm은 숫자가 아니라 '문자열'이다 ("88"). 비어 있으면 "" 또는 None.

    할 일:
      1) movieCd가 None이면 → fail(NOT_FOUND, ...)
      2) showTm이 숫자 문자열이 아니면("" / None / "abc") → fail(NO_RUNTIME, ...)
         왜 0이나 기본값(예: 120분)으로 채우지 않나?
           → judge가 그 숫자로 "7시 전에 끝난다"를 계산한다. 지어낸 숫자는 틀린 추천이 된다 (F3008 '지어내지 않기').
           → NO_RUNTIME이면 judge는 이 영화를 '판단하지 않고 제외'하고, 제외 사유를 답변에 보여 준다 (F3011).
      3) 정상이면 ok({
             "movie_cd": str, "movie_nm": str, "show_tm": int,
             "watch_grade": str | None,   # audits[0].watchGradeNm (없으면 None)
             "genres": [str],             # genres[].genreNm
         })
    힌트: str.isdigit(), int(), 리스트 컴프리헨션. 출력 키 이름은 기획서 3.3절 ②와 같게.
    순수 함수로 둔 이유: 러닝타임이 빈 영화를 실제 API에서 찾기 어렵다. dict만 넣어 바로 시험할 수 있다.
    """
    raise NotImplementedError("TODO 3")


# ── Tool 함수 2: 영화 상세 ──────────────────────────────
def get_movie_detail(movie_cd: str) -> ToolResult:
    if not isinstance(movie_cd, str) or not movie_cd.strip():
        return fail(BAD_ARGUMENT, "movie_cd가 비어 있다. get_box_office 결과의 movie_cd를 넣는다.")

    body, err = _call_kobis("movie/searchMovieInfo.json", {"movieCd": movie_cd.strip()})
    if err:
        return err
    info: dict[str, Any] = body.get("movieInfoResult", {}).get("movieInfo") or {}
    return to_detail(info)


# ── Tool 명함 (Agent에는 name, description, parameters만 간다) ──────
# description에는 '무엇을 하는지'보다 '언제 부르는지'를 쓴다. LLM은 이 문장을 보고 Tool을 고른다.
BOX_OFFICE_TOOL = Tool(
    name="get_box_office",
    description=(
        "볼 영화를 아직 정하지 않았을 때, 관람 후보 영화 목록(일일 박스오피스 순위)을 얻으려고 가장 먼저 부른다. "
        "결과의 movie_cd를 get_movie_detail에 넘긴다. 미래 날짜는 최근 집계일 기준 추정치다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "target_date": {"type": "string", "description": "관람 날짜 YYYY-MM-DD. 모르면 비운다."},
            "top_n": {"type": "integer", "minimum": 1, "maximum": KOBIS_MAX_ITEMS, "default": config.BOX_TOP_N},
        },
        "required": [],
    },
    func=get_box_office,
)

MOVIE_DETAIL_TOOL = Tool(
    name="get_movie_detail",
    description=(
        "영화코드(movie_cd)로 러닝타임과 관람등급을 확인할 때 부른다. "
        "시간 안에 볼 수 있는지 판단하기 전에 후보마다 필요하다."
    ),
    parameters={
        "type": "object",
        "properties": {"movie_cd": {"type": "string", "description": "get_box_office 결과의 movie_cd"}},
        "required": ["movie_cd"],
    },
    func=get_movie_detail,
)


# ── 스스로 확인 ────────────────────────────────────────
if __name__ == "__main__":
    T = date(2026, 11, 19)
    ALL_NONE = {"movieCd": None, "movieNm": None, "showTm": None, "genres": [], "audits": []}
    GOOD = {"movieCd": "20247221", "movieNm": "폭설", "showTm": "88",
            "genres": [{"genreNm": "미스터리"}, {"genreNm": "드라마"}], "audits": [{"watchGradeNm": "15세이상관람가"}]}

    def code(r: ToolResult) -> str | None:
        return None if r.get("ok") else r["error"]["code"]

    def with_wrong_key() -> ToolResult:
        saved, config.KOBIS_API_KEY = config.KOBIS_API_KEY, "wrong_key_for_test"
        try:
            return BOX_OFFICE_TOOL.run()
        finally:
            config.KOBIS_API_KEY = saved

    checks = [
        ("TODO2 과거 날짜는 그대로",     lambda: basis_date(date(2026, 11, 10), T) == (date(2026, 11, 10), False)),
        ("TODO2 어제는 그대로",          lambda: basis_date(date(2026, 11, 18), T) == (date(2026, 11, 18), False)),
        ("TODO2 오늘 → 어제, 추정",      lambda: basis_date(T, T) == (date(2026, 11, 18), True)),
        ("TODO2 미래 → 어제, 추정",      lambda: basis_date(date(2026, 11, 22), T) == (date(2026, 11, 18), True)),
        ("TODO3 모든 값 None → NOT_FOUND", lambda: code(to_detail(ALL_NONE)) == NOT_FOUND),
        ("TODO3 showTm '' → NO_RUNTIME", lambda: code(to_detail({**GOOD, "showTm": ""})) == NO_RUNTIME),
        ("TODO3 showTm None → NO_RUNTIME", lambda: code(to_detail({**GOOD, "showTm": None})) == NO_RUNTIME),
        ("TODO3 정상 → show_tm=88(int)", lambda: to_detail(GOOD)["data"]["show_tm"] == 88),
        ("TODO3 정상 → 등급·장르",       lambda: to_detail(GOOD)["data"]["watch_grade"] == "15세이상관람가"
                                                and to_detail(GOOD)["data"]["genres"] == ["미스터리", "드라마"]),
        ("TODO1 틀린 키 → INVALID_KEY",  lambda: code(with_wrong_key()) == INVALID_KEY),
        ("뼈대  날짜 형식은 호출 전에 막음",   lambda: code(BOX_OFFICE_TOOL.run(target_date="20261118")) == BAD_ARGUMENT),
        ("실제 호출: 박스오피스",        lambda: BOX_OFFICE_TOOL.run()["ok"]),
        ("실제 호출: 1위 상세",          lambda: MOVIE_DETAIL_TOOL.run(
            movie_cd=BOX_OFFICE_TOOL.run()["data"]["movies"][0]["movie_cd"])["ok"]),
        ("실제 호출: 없는 코드 → NOT_FOUND", lambda: code(MOVIE_DETAIL_TOOL.run(movie_cd="00000000")) == NOT_FOUND),
    ]
    for label, check in checks:
        try:
            result = "PASS" if check() else "FAIL"
        except Exception as e:  # 아직 TODO면 여기로 온다
            result = f"FAIL ({type(e).__name__})"
        print(f"{result:<28} {label}")
