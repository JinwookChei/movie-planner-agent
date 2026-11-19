"""M0 기획 검증: 외부 API를 1회씩 호출해 실제 응답 구조를 확인한다.

Tool 코드가 아니라 1회용 확인 스크립트다. (실행: python scripts/check_apis.py)
지금은 KOBIS만 있다. TMDB, 카카오, GMS는 M0-2 ~ M0-4에서 추가한다.
"""
import os
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()  # 반드시 os.getenv보다 먼저 호출한다.

KOBIS_KEY = os.getenv("KOBIS_API_KEY")
KOBIS_BASE = "http://www.kobis.or.kr/kobisopenapi/webservice/rest"
TIMEOUT = 10


def kobis_get(path: str, params: dict) -> dict:
    """KOBIS 호출. 키는 params에 넣어 보내고, URL은 출력하지 않는다(키가 들어 있으므로)."""
    resp = requests.get(f"{KOBIS_BASE}/{path}", params={"key": KOBIS_KEY, **params}, timeout=TIMEOUT)
    print(f"  HTTP {resp.status_code}")
    body = resp.json()
    # KOBIS는 키가 틀려도 HTTP 200을 주고 본문에 faultInfo를 넣는다.
    if "faultInfo" in body:
        print(f"  faultInfo: {body['faultInfo'].get('message')}")
    return body


def check_box_office(target: date) -> list[dict]:
    target_dt = target.strftime("%Y%m%d")  # KOBIS 날짜 형식: YYYYMMDD (대시 없음)
    print(f"\n[KOBIS 박스오피스] targetDt={target_dt}")
    body = kobis_get("boxoffice/searchDailyBoxOfficeList.json", {"targetDt": target_dt})

    movies = body.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
    print(f"  건수: {len(movies)}")
    for m in movies[:5]:
        print(f"  {m['rank']:>2} {m['movieCd']} {m['movieNm']} (개봉 {m['openDt'] or '없음'})")
    return movies


def check_movie_detail(movie_cd: str) -> None:
    print(f"\n[KOBIS 영화 상세] movieCd={movie_cd}")
    body = kobis_get("movie/searchMovieInfo.json", {"movieCd": movie_cd})

    info = body.get("movieInfoResult", {}).get("movieInfo", {})
    if not info:
        print("  movieInfo 없음")
        return
    genres = [g["genreNm"] for g in info.get("genres", [])]
    grades = [a["watchGradeNm"] for a in info.get("audits", [])]
    # showTm은 문자열("112")이고, 비어 있으면 ""가 온다. → Tool에서는 NO_RUNTIME으로 처리할 값
    print(f"  {info.get('movieNm')} / showTm={info.get('showTm')!r} / {genres} / {grades}")


# ── TMDB ──────────────────────────────────────────────
TMDB_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE = "https://api.themoviedb.org/3"


def tmdb_get(path: str, params: dict | None = None) -> tuple[int, dict]:
    """TMDB 호출. KOBIS와 달리 키가 틀리면 HTTP 401을 준다 → 상태 코드를 함께 돌려준다."""
    resp = requests.get(
        f"{TMDB_BASE}/{path}",
        params={"api_key": TMDB_KEY, "language": "ko-KR", **(params or {})},
        timeout=TIMEOUT,
    )
    print(f"  HTTP {resp.status_code}")
    return resp.status_code, resp.json()


def check_new_releases(days: int = 7) -> list[dict]:
    start = date.today()
    end = start + timedelta(days=days)
    # TMDB 날짜 형식: YYYY-MM-DD (KOBIS와 다르다)
    print(f"\n[TMDB discover] {start.isoformat()} ~ {end.isoformat()} (KR 극장 개봉)")
    status, body = tmdb_get("discover/movie", {
        "region": "KR",
        "release_date.gte": start.isoformat(),
        "release_date.lte": end.isoformat(),
        "with_release_type": "2|3",  # 2=제한 극장 개봉, 3=극장 개봉 ('|'는 OR)
        "sort_by": "popularity.desc",
    })
    if status != 200:
        print(f"  오류: {body.get('status_message')}")
        return []

    movies = body.get("results", [])
    print(f"  전체 {body.get('total_results')}건 중 상위 3건")
    for m in movies[:3]:
        print(f"  {m['id']:>8} {m['title']} (개봉 {m.get('release_date') or '없음'})")
    return movies


def check_tmdb_runtime(tmdb_id: int) -> None:
    print(f"\n[TMDB 상세] movie/{tmdb_id}")
    status, body = tmdb_get(f"movie/{tmdb_id}")
    if status != 200:
        print(f"  오류: {body.get('status_message')}")
        return
    # runtime은 정수. 개봉 전 영화는 0 또는 None일 수 있다 → Tool에서는 show_tm=None 처리
    print(f"  {body.get('title')} / runtime={body.get('runtime')!r}")


# ── 카카오 로컬 ───────────────────────────────────────
KAKAO_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def check_theaters(region: str, size: int = 5) -> None:
    query = f"{region} 영화관"
    print(f"\n[카카오 키워드 검색] query={query!r}")
    resp = requests.get(
        KAKAO_URL,
        # 카카오는 키를 파라미터가 아니라 '헤더'로 보낸다.
        headers={"Authorization": f"KakaoAK {KAKAO_KEY}"},
        params={"query": query, "category_group_code": "CT1", "size": size},  # CT1 = 문화시설
        timeout=TIMEOUT,
    )
    print(f"  HTTP {resp.status_code}")
    body = resp.json()
    if resp.status_code != 200:
        # 401: 키 오류 / 403: 앱에 카카오맵 사용 설정이 꺼져 있음
        print(f"  오류: {body.get('errorType')} {body.get('message')}")
        return

    docs = body.get("documents", [])
    print(f"  total_count={body.get('meta', {}).get('total_count')} / 받은 건수 {len(docs)}")
    for d in docs:
        # 도로명 주소가 비어 있을 수 있어 지번 주소(address_name)를 대신 보여 준다.
        addr = d.get("road_address_name") or d.get("address_name")
        print(f"  {d['place_name']} | {addr} | {d.get('phone') or '전화 없음'} | {d['place_url']}")


# ── GMS (LLM) ─────────────────────────────────────────
GMS_KEY = os.getenv("GMS_API_KEY")
GMS_BASE = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"


def check_gms() -> None:
    from langchain_openai import ChatOpenAI  # 필요한 곳에서만 import (설치 안 됐으면 여기서만 실패)

    llm = ChatOpenAI(model="gpt-5.4-nano", base_url=GMS_BASE, api_key=GMS_KEY, timeout=30)

    print("\n[GMS] 연결 확인")
    print(f"  {llm.invoke('한 문장으로 인사해 줘.').content}")

    # Tool 없이 LLM만 쓰면 무엇이 틀리는가: 위 KOBIS 결과(어제 1위)와 비교한다.
    print("\n[GMS] Tool 없이 질문: 어제 한국 박스오피스 1위")
    question = f"{date.today() - timedelta(days=1)} 한국 일일 박스오피스 1위 영화 제목과 러닝타임을 알려 줘."
    print(f"  {llm.invoke(question).content}")


# ── M0-5 실패 응답 확인 ───────────────────────────────
# 실행: python scripts/check_apis.py fail
# 목적: 실패할 때 '실제로' 어떤 HTTP 상태와 본문이 오는지 보고 Tool 실패 코드를 정한다.
WRONG = "wrong_key_for_test"


def show(label: str, resp: requests.Response) -> dict:
    """상태 코드와 본문 앞부분만 출력한다. (본문에 키가 들어 있을 수 있어 길이를 자른다)"""
    try:
        body = resp.json()
    except ValueError:
        body = {}
        print(f"\n[{label}] HTTP {resp.status_code} / JSON 아님: {resp.text[:120]!r}")
        return body
    print(f"\n[{label}] HTTP {resp.status_code}")
    print(f"  {str(body)[:200]}")
    return body


def check_failures() -> None:
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")

    # 1) KOBIS 키 오류 → 예상: HTTP 200 + faultInfo
    show("KOBIS 키 오류", requests.get(f"{KOBIS_BASE}/boxoffice/searchDailyBoxOfficeList.json",
                                     params={"key": WRONG, "targetDt": yesterday}, timeout=TIMEOUT))

    # 2) KOBIS 날짜 형식 오류(대시 포함) → BAD_ARGUMENT를 Tool에서 미리 막아야 하는지 확인
    show("KOBIS 날짜 형식 오류", requests.get(f"{KOBIS_BASE}/boxoffice/searchDailyBoxOfficeList.json",
                                       params={"key": KOBIS_KEY, "targetDt": "2026-11-18"}, timeout=TIMEOUT))

    # 3) KOBIS 없는 movieCd → NOT_FOUND
    show("KOBIS 없는 movieCd", requests.get(f"{KOBIS_BASE}/movie/searchMovieInfo.json",
                                         params={"key": KOBIS_KEY, "movieCd": "00000000"}, timeout=TIMEOUT))

    # 4) TMDB 키 오류 → 예상: HTTP 401
    show("TMDB 키 오류", requests.get(f"{TMDB_BASE}/discover/movie",
                                    params={"api_key": WRONG}, timeout=TIMEOUT))

    # 5) TMDB 없는 영화 id → NOT_FOUND
    show("TMDB 없는 id", requests.get(f"{TMDB_BASE}/movie/999999999",
                                    params={"api_key": TMDB_KEY}, timeout=TIMEOUT))

    # 6) 카카오 키 오류 → 예상: HTTP 401
    show("카카오 키 오류", requests.get(KAKAO_URL, headers={"Authorization": f"KakaoAK {WRONG}"},
                                   params={"query": "강남 영화관"}, timeout=TIMEOUT))

    # 7) 카카오 결과 없음 → NO_DATA (오류가 아니라 빈 documents로 오는지)
    show("카카오 결과 없음", requests.get(KAKAO_URL, headers={"Authorization": f"KakaoAK {KAKAO_KEY}"},
                                    params={"query": "zzzz 영화관", "category_group_code": "CT1"},
                                    timeout=TIMEOUT))

    # 8) TMDB 먼 미래 신작 → runtime이 0/None으로 오는지 (NO_RUNTIME 근거)
    start, end = date.today() + timedelta(days=60), date.today() + timedelta(days=120)
    print(f"\n[TMDB 먼 미래 신작 runtime] {start} ~ {end}")
    status, body = tmdb_get("discover/movie", {
        "region": "KR", "release_date.gte": start.isoformat(), "release_date.lte": end.isoformat(),
        "with_release_type": "2|3",
    })
    for m in body.get("results", [])[:8]:
        _, detail = tmdb_get(f"movie/{m['id']}")
        print(f"    {m['title']} ({m.get('release_date')}) runtime={detail.get('runtime')!r}")

    # 9) GMS 키 오류 → 예외 종류와 메시지 확인 (Tool이 아니라 LLM 노드에서 처리할 실패)
    print("\n[GMS 키 오류]")
    from langchain_openai import ChatOpenAI
    try:
        ChatOpenAI(model="gpt-5.4-nano", base_url=GMS_BASE, api_key=WRONG, timeout=30,
                   max_retries=0).invoke("hi")
    except Exception as e:  # 확인용이라 넓게 잡는다
        print(f"  {type(e).__name__}: {str(e)[:150]}")


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["fail"]:
        check_failures()
        raise SystemExit(0)

    if not KOBIS_KEY:
        raise SystemExit(".env에 KOBIS_API_KEY가 없습니다.")

    # 1) 정상: 어제 날짜
    movies = check_box_office(date.today() - timedelta(days=1))

    # 2) 1위 영화 상세
    if movies:
        check_movie_detail(movies[0]["movieCd"])

    # 3) 오늘 날짜: 아직 집계 전이라 어떤 응답이 오는지 확인 (F3002 '추정' 처리 근거)
    check_box_office(date.today())

    # ── TMDB ──
    if not TMDB_KEY:
        raise SystemExit(".env에 TMDB_API_KEY가 없습니다.")

    # 4) 앞으로 7일 안에 개봉하는 국내 극장 개봉작
    releases = check_new_releases(7)

    # 5) 신작 러닝타임 (0/None이 오는지 확인)
    for m in releases[:3]:
        check_tmdb_runtime(m["id"])

    # ── 카카오 ──
    if not KAKAO_KEY:
        raise SystemExit(".env에 KAKAO_REST_API_KEY가 없습니다.")

    # 6) 정상: 강남 영화관
    check_theaters("강남")

    # ── GMS ──
    if not GMS_KEY:
        raise SystemExit(".env에 GMS_API_KEY가 없습니다.")

    # 7) LLM 1회 호출 + Tool 없이 답했을 때 틀리는지 비교
    check_gms()
