"""TMDB Tool: search_new_releases. (M2-4, F3002 보충, R8 전용)

왜 필요한가:
  KOBIS 박스오피스는 지난 날짜(기준일 = min(목표일, 어제))의 집계만 준다.
  그래서 '기준일 다음 날 ~ 관람일' 사이에 개봉하는 영화는 KOBIS 후보에 없다. 그 빈틈을 채운다.

kakao.py와 같은 점 / 다른 점 (M0에서 확인, 기획서 7.1절):
  - 실패를 HTTP 상태 코드로 알린다: 401(status_code 7) = 키 오류, 404(status_code 34) = 없는 id.
  - 키는 쿼리 파라미터 api_key로 보낸다 → 예외 메시지(URL 포함)에 키가 섞일 수 있지만 fail()이 scrub한다.
  - 상세 runtime은 정수다. 개봉 전 영화는 0으로 온다(None 아님) → 0도 '모름'으로 보고 show_tm=None.

호출 수: discover 1번 + 상세 최대 NEW_RELEASE_MAX(3)번.

확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.tools.tmdb
"""
from datetime import date, timedelta
from typing import Any

import requests

from app import config
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NO_DATA,
    NOT_FOUND,
    UPSTREAM_ERROR,
    Tool,
    ToolResult,
    fail,
    ok,
)

MAX_RANGE_DAYS = 31  # 너무 넓은 기간은 '신작'이 아니다. 잘못 만든 인자를 막는다.


# ── 상태 코드 → 공통 실패 코드 ──────────────────────────────
def classify_error(status: int, body: dict) -> ToolResult | None:
    """TMDB 응답 상태가 실패면 fail(...)을, 성공이면 None을 돌려준다."""
    if status < 400:
        return None
    detail = body.get("status_message", "사유 없음")
    if status in (401, 403):
        return fail(INVALID_KEY, f"TMDB API 키를 확인해야 한다(HTTP {status}): {detail}")
    if status == 404:
        return fail(NOT_FOUND, f"TMDB에서 찾을 수 없다(HTTP 404): {detail}")
    if status in (400, 422):
        return fail(BAD_ARGUMENT, f"TMDB가 요청 인자를 거부했다(HTTP {status}): {detail}")
    return fail(UPSTREAM_ERROR, f"TMDB 서버 응답 오류(HTTP {status}): {detail}")


def to_show_tm(runtime: Any) -> int | None:
    """TMDB runtime → show_tm. 0·None·음수·정수가 아닌 값은 모두 '모름'(None)."""
    if isinstance(runtime, bool) or not isinstance(runtime, int) or runtime <= 0:
        return None
    return runtime


# ── 공통 호출 ──────────────────────────────────────────
def _call_tmdb(path: str, params: dict | None = None) -> tuple[dict | None, ToolResult | None]:
    """TMDB를 호출해 (본문, None) 또는 (None, 실패결과)를 돌려준다."""
    key = config.TMDB_API_KEY
    if not key:
        return None, fail(INVALID_KEY, "TMDB API 키가 설정되지 않았다.")

    try:
        resp = requests.get(
            f"{config.TMDB_BASE_URL}/{path}",
            params={"api_key": key, "language": "ko-KR", **(params or {})},
            timeout=config.HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        return None, fail(UPSTREAM_ERROR, f"TMDB 호출 실패: {type(e).__name__}")

    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}

    err = classify_error(resp.status_code, body)
    if err:
        return None, err
    if not body:
        return None, fail(UPSTREAM_ERROR, "TMDB 응답이 JSON이 아니다.")
    return body, None


def _parse_date(text: Any) -> date | None:
    if not isinstance(text, str):
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


# ── Tool 함수: 신작 보충 ─────────────────────────────────
def search_new_releases(from_date: str, to_date: str) -> ToolResult:
    start, end = _parse_date(from_date), _parse_date(to_date)
    if start is None or end is None:
        return fail(BAD_ARGUMENT, f"from_date, to_date는 YYYY-MM-DD 형식이어야 한다: {from_date!r}, {to_date!r}")
    if start > end:
        return fail(BAD_ARGUMENT, f"from_date({start})가 to_date({end})보다 늦다.")
    if (end - start).days > MAX_RANGE_DAYS:
        return fail(BAD_ARGUMENT, f"기간은 {MAX_RANGE_DAYS}일 이내여야 한다: {start} ~ {end}")

    body, err = _call_tmdb("discover/movie", {
        "region": "KR",
        "release_date.gte": start.isoformat(),
        "release_date.lte": end.isoformat(),
        "with_release_type": "2|3",  # 2=제한 극장 개봉, 3=극장 개봉
        "sort_by": "popularity.desc",
    })
    if err:
        return err

    results = (body.get("results") or [])[: config.NEW_RELEASE_MAX]  # 첫 쪽에서 인기순 앞 3편만
    if not results:
        return fail(NO_DATA, f"{start} ~ {end} 사이 국내 극장 개봉작이 없다.")

    movies = []
    for m in results:
        tmdb_id = m.get("id")
        detail, d_err = _call_tmdb(f"movie/{tmdb_id}")
        # 상세가 실패해도 목록은 살린다. 러닝타임을 모르면 judge가 NO_RUNTIME으로 제외한다 (F3007 계속 진행).
        show_tm = to_show_tm(detail.get("runtime")) if detail else None
        movies.append({
            "tmdb_id": tmdb_id,
            "movie_nm": m.get("title") or None,
            "release_date": m.get("release_date") or None,
            "show_tm": show_tm,
        })
        if d_err and d_err["error"]["code"] == INVALID_KEY:
            return d_err  # 키 문제는 남은 영화도 똑같이 실패한다 → 바로 멈춘다

    return ok({"from_date": start.isoformat(), "to_date": end.isoformat(), "movies": movies})


# ── Tool 명함 ─────────────────────────────────────────
NEW_RELEASES_TOOL = Tool(
    name="search_new_releases",
    description=(
        "관람 날짜가 오늘 이후라서 박스오피스(어제 기준)에 아직 없는 신작을 보충할 때 부른다. "
        "from_date는 박스오피스 basis_date 다음 날, to_date는 관람 날짜다. "
        "러닝타임(show_tm)을 함께 주므로 상세 조회는 따로 하지 않는다. 지난 날짜에는 부르지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "from_date": {"type": "string", "description": "YYYY-MM-DD. 박스오피스 basis_date 다음 날"},
            "to_date": {"type": "string", "description": "YYYY-MM-DD. 관람 날짜"},
        },
        "required": ["from_date", "to_date"],
    },
    func=search_new_releases,
)


# ── 스스로 확인 ────────────────────────────────────────
if __name__ == "__main__":
    today = date.today()
    week = (today.isoformat(), (today + timedelta(days=7)).isoformat())

    def code(r: ToolResult | None) -> str | None:
        if r is None:
            return None
        return None if r.get("ok") else r["error"]["code"]

    def with_key(value: str) -> ToolResult:
        saved, config.TMDB_API_KEY = config.TMDB_API_KEY, value
        try:
            return NEW_RELEASES_TOOL.run(from_date=week[0], to_date=week[1])
        finally:
            config.TMDB_API_KEY = saved

    checks = [
        ("분류 200 → None",                 lambda: classify_error(200, {}) is None),
        ("분류 401 → INVALID_KEY",          lambda: code(classify_error(401, {"status_code": 7})) == INVALID_KEY),
        ("분류 404 → NOT_FOUND",            lambda: code(classify_error(404, {"status_code": 34})) == NOT_FOUND),
        ("분류 500 → UPSTREAM_ERROR",       lambda: code(classify_error(500, {})) == UPSTREAM_ERROR),
        ("runtime 0 → None",                lambda: to_show_tm(0) is None),
        ("runtime None → None",             lambda: to_show_tm(None) is None),
        ("runtime 106 → 106",               lambda: to_show_tm(106) == 106),
        ("날짜 형식 틀림 → BAD_ARGUMENT",    lambda: code(NEW_RELEASES_TOOL.run(from_date="11/20", to_date=week[1])) == BAD_ARGUMENT),
        ("순서 뒤집힘 → BAD_ARGUMENT",       lambda: code(NEW_RELEASES_TOOL.run(from_date=week[1], to_date=week[0])) == BAD_ARGUMENT),
        ("인자 이름 틀림 → BAD_ARGUMENT",    lambda: code(NEW_RELEASES_TOOL.run(start="x")) == BAD_ARGUMENT),
        ("키 없음 → INVALID_KEY",           lambda: code(with_key("")) == INVALID_KEY),
        ("실제 호출: 틀린 키 → INVALID_KEY", lambda: code(with_key("wrong_key_for_test")) == INVALID_KEY),
        ("실제 호출: 오류 메시지에 키 없음", lambda: "wrong_key_for_test" not in with_key("wrong_key_for_test")["error"]["message"]),
        ("실제 호출: 이번 주 신작 1~3편",    lambda: 1 <= len(NEW_RELEASES_TOOL.run(from_date=week[0], to_date=week[1])["data"]["movies"]) <= 3),
        ("실제 호출: 먼 과거 하루 → NO_DATA", lambda: code(NEW_RELEASES_TOOL.run(from_date="1900-01-01", to_date="1900-01-01")) == NO_DATA),
    ]
    for label, check in checks:
        try:
            result = "PASS" if check() else "FAIL"
        except Exception as e:
            result = f"FAIL ({type(e).__name__})"
        print(f"{result:<30} {label}")

    sample = NEW_RELEASES_TOOL.run(from_date=week[0], to_date=week[1])
    print("\n예시 출력:", sample)
