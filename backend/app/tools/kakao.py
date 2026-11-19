"""카카오 로컬 Tool: search_theaters. (M2-3, F3004)

Agent 관점에서 이 파일의 핵심은 두 가지다.
  1) HTTP 상태 코드를 '공통 실패 코드'로 바꾸는 판단 (TODO A)
  2) 카카오 원본(documents)에서 안내에 쓸 값만 골라, 빈 값은 비운 채로 돌려주기 (TODO B)
나머지(HTTP 호출, 입력 검사, Tool 명함)는 완성된 뼈대다.

kobis.py와 다른 점 (M0에서 확인, 기획서 7.1절):
  - KOBIS는 실패해도 HTTP 200 + faultInfo였지만, 카카오는 실패를 'HTTP 상태 코드'로 알린다.
  - 키는 쿼리 파라미터가 아니라 헤더(Authorization: KakaoAK {key})로 보낸다.
  - 401 오류 메시지에 키 값이 그대로 들어 있다 → fail()이 scrub하므로 원문을 넣어도 안전하다.

확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.tools.kakao
"""
from typing import Any

import requests

from app import config
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NO_DATA,
    UPSTREAM_ERROR,
    Tool,
    ToolResult,
    fail,
    ok,
)

KAKAO_MAX_SIZE = 15        # 카카오 키워드 검색 size 상한
THEATER_CATEGORY = "CT1"   # 문화시설. 빼면 '강남 영화관' 검색에 식당 등이 섞인다 (M0 확인)


# ── TODO A: HTTP 상태 코드를 공통 실패 코드로 바꾸기 ──────────────
def classify_error(status: int, body: dict) -> ToolResult | None:
    """카카오 응답의 상태 코드를 보고 실패면 fail(...)을, 성공이면 None을 돌려준다.

    M0에서 확인한 사실:
      - 키 오류      → HTTP 401, body = {"errorType": "AccessDeniedError", "message": "wrong appKey(키값) format"}
      - 앱 설정 꺼짐 → HTTP 403 (카카오 개발자 콘솔에서 카카오맵 사용 설정이 꺼져 있음)
      - 결과 없음    → HTTP 200, documents=[]   ← 오류가 아니다! 여기서 다루지 않는다 (TODO B의 몫)

    할 일:
      - status가 400 미만이면              → None (실패 아님)
      - 401 또는 403                      → fail(INVALID_KEY, ...)
      - 400                               → fail(BAD_ARGUMENT, ...)   (요청 인자가 규격과 다름)
      - 그 밖의 4xx/5xx (429, 500 등)      → fail(UPSTREAM_ERROR, ...)
    메시지: 어느 API의 무슨 문제인지 + body의 "message"(없을 수 있음 → .get). 키가 섞여도 fail이 가린다.

    왜 401/403을 같은 코드로 묶나? 둘 다 '다시 해도 똑같이 실패'다 → Agent가 재계획하지 않는다 (기획서 3.2).
    왜 순수 함수로 뺐나? 403·500 같은 응답은 일부러 만들기 어렵다. 숫자와 dict만 넣어 바로 시험할 수 있다.
    """
    if status < 400:
        return None  # 성공. 결과가 0건이어도 여기서는 실패가 아니다 (TODO B가 NO_DATA로 처리)

    detail = body.get("message", "사유 없음")  # 오류 본문에 message가 없을 수도 있다
    if status in (401, 403):
        # 키가 틀렸거나 앱 설정이 꺼짐 → 다시 해도 똑같이 실패한다
        return fail(INVALID_KEY, f"카카오 API 키 또는 앱 설정을 확인해야 한다(HTTP {status}): {detail}")
    if status == 400:
        return fail(BAD_ARGUMENT, f"카카오가 요청 인자를 거부했다(HTTP 400): {detail}")
    return fail(UPSTREAM_ERROR, f"카카오 서버 응답 오류(HTTP {status}): {detail}")


# ── TODO B: 카카오 documents를 Agent가 쓸 값으로 바꾸기 ─────────────
def to_theaters(docs: list[dict], region: str) -> ToolResult:
    """카카오 documents(list[dict]) → ok({"theaters": [...]}) 또는 fail(NO_DATA).

    카카오 document 한 건의 모양 (필요한 키만):
      {"place_name": "CGV 강남", "road_address_name": "서울 강남구 강남대로 438",
       "address_name": "서울 강남구 역삼동 814-6", "place_url": "http://place.map.kakao.com/...",
       "phone": "1544-1122"}

    할 일:
      1) docs가 비어 있으면 → fail(NO_DATA, ...)   메시지에 region을 넣어 무엇이 없었는지 남긴다.
         (F3004 "결과가 없을 때의 형식을 정한다": 빈 리스트 ok가 아니라 NO_DATA로 통일)
      2) 각 document를 아래 4개 키로 바꾼다.
           "name":    place_name
           "address": road_address_name, 비어 있으면("" 또는 없음) address_name
           "url":     place_url
           "phone":   phone, 비어 있으면 None
      3) ok({"theaters": [...]})
    주의: 빈 문자열("")을 그대로 두지 않는다. 화면이 "값 없음"과 "빈 값"을 구분하지 못한다 (tools_start.py 약속).
          영화와 영화관을 묶지 않는다. 이 Tool은 '어느 영화를 상영하는지' 모른다 (F3004).
    힌트: `a or b` (a가 ""/None이면 b), dict.get, 리스트 컴프리헨션 또는 for + append.
    """
    
    
    # 1) 결과 없음: HTTP 200 + 빈 documents로 온다 (M0 확인). 빈 ok가 아니라 NO_DATA로 통일한다.
    if not docs:
        return fail(NO_DATA, f"'{region}' 지역에서 영화관을 찾지 못했다.")

    # 2) 안내에 쓸 4개 값만 고른다. 빈 문자열은 대체값이나 None으로 바꾼다.
    theaters = [
        {
            "name": d.get("place_name"),
            "address": d.get("road_address_name") or d.get("address_name") or None,  # 도로명 → 지번 → 없음
            "url": d.get("place_url") or None,
            "phone": d.get("phone") or None,
        }
        for d in docs
    ]
    return ok({"theaters": theaters})


# ── 공통 호출 ──────────────────────────────────────────
def _call_kakao(params: dict) -> tuple[dict | None, ToolResult | None]:
    """카카오 로컬을 호출해 (본문, None) 또는 (None, 실패결과)를 돌려준다."""
    key = config.KAKAO_REST_API_KEY
    if not key:
        return None, fail(INVALID_KEY, "카카오 API 키가 설정되지 않았다.")

    try:
        resp = requests.get(
            config.KAKAO_LOCAL_URL,
            headers={"Authorization": f"KakaoAK {key}"},  # 카카오는 키를 헤더로 보낸다
            params=params,
            timeout=config.HTTP_TIMEOUT,
        )
    except requests.RequestException as e:  # 시간 초과, 연결 실패
        return None, fail(UPSTREAM_ERROR, f"카카오 호출 실패: {type(e).__name__}")

    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}  # 오류 응답이 JSON이 아닐 수도 있다. 상태 코드 판단은 그래도 해야 한다.

    err = classify_error(resp.status_code, body)  # ← TODO A
    if err:
        return None, err
    if not body:
        return None, fail(UPSTREAM_ERROR, "카카오 응답이 JSON이 아니다.")
    return body, None


# ── Tool 함수: 영화관 검색 ───────────────────────────────
def search_theaters(region: str, size: int = 5) -> ToolResult:
    if not isinstance(region, str) or not region.strip():
        return fail(BAD_ARGUMENT, "region이 비어 있다. 사용자가 말한 지역(예: 강남)을 넣는다.")
    if not isinstance(size, int) or not 1 <= size <= KAKAO_MAX_SIZE:
        return fail(BAD_ARGUMENT, f"size는 1~{KAKAO_MAX_SIZE} 사이 정수여야 한다: {size!r}")

    region = region.strip()
    body, err = _call_kakao({
        "query": f"{region} 영화관",
        "category_group_code": THEATER_CATEGORY,
        "size": size,
    })
    if err:
        return err
    return to_theaters(body.get("documents") or [], region)  # ← TODO B


# ── Tool 명함 (Agent에는 name, description, parameters만 간다) ──────
THEATERS_TOOL = Tool(
    name="search_theaters",
    description=(
        "사용자가 지역(예: 강남, 홍대)을 말했을 때, 그 지역의 영화관 이름·주소·링크를 함께 안내하려고 부른다. "
        "어느 영화를 상영하는지는 알 수 없다. 지역을 말하지 않았으면 부르지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "region": {"type": "string", "description": "사용자가 말한 지역 이름. 예: 강남"},
            "size": {"type": "integer", "minimum": 1, "maximum": KAKAO_MAX_SIZE, "default": 5},
        },
        "required": ["region"],
    },
    func=search_theaters,
)


# ── 스스로 확인 ────────────────────────────────────────
if __name__ == "__main__":
    DOC = {"place_name": "CGV 강남", "road_address_name": "서울 강남구 강남대로 438",
           "address_name": "서울 강남구 역삼동 814-6", "place_url": "http://place.map.kakao.com/1", "phone": "1544-1122"}
    NO_ROAD = {**DOC, "road_address_name": ""}
    NO_PHONE = {**DOC, "phone": ""}

    def code(r: ToolResult | None) -> str | None:
        if r is None:
            return None
        return None if r.get("ok") else r["error"]["code"]

    def with_wrong_key() -> ToolResult:
        saved, config.KAKAO_REST_API_KEY = config.KAKAO_REST_API_KEY, "wrong_key_for_test"
        try:
            return THEATERS_TOOL.run(region="강남")
        finally:
            config.KAKAO_REST_API_KEY = saved

    checks = [
        ("TODOA 200 → 실패 아님(None)",   lambda: classify_error(200, {"documents": []}) is None),
        ("TODOA 401 → INVALID_KEY",       lambda: code(classify_error(401, {"message": "wrong appKey"})) == INVALID_KEY),
        ("TODOA 403 → INVALID_KEY",       lambda: code(classify_error(403, {})) == INVALID_KEY),
        ("TODOA 400 → BAD_ARGUMENT",      lambda: code(classify_error(400, {})) == BAD_ARGUMENT),
        ("TODOA 500 → UPSTREAM_ERROR",    lambda: code(classify_error(500, {})) == UPSTREAM_ERROR),
        ("TODOB 빈 결과 → NO_DATA",        lambda: code(to_theaters([], "강남")) == NO_DATA),
        ("TODOB 정상 → 4개 키",            lambda: to_theaters([DOC], "강남")["data"]["theaters"][0] == {
            "name": "CGV 강남", "address": "서울 강남구 강남대로 438",
            "url": "http://place.map.kakao.com/1", "phone": "1544-1122"}),
        ("TODOB 도로명 없음 → 지번 주소",   lambda: to_theaters([NO_ROAD], "강남")["data"]["theaters"][0]["address"]
                                                == "서울 강남구 역삼동 814-6"),
        ("TODOB 전화 \"\" → None",          lambda: to_theaters([NO_PHONE], "강남")["data"]["theaters"][0]["phone"] is None),
        ("뼈대  빈 region → BAD_ARGUMENT", lambda: code(THEATERS_TOOL.run(region="  ")) == BAD_ARGUMENT),
        ("실제 호출: 틀린 키 → INVALID_KEY", lambda: code(with_wrong_key()) == INVALID_KEY),
        ("실제 호출: 오류 메시지에 키 없음", lambda: "wrong_key_for_test" not in with_wrong_key()["error"]["message"]),
        ("실제 호출: 강남 영화관",          lambda: len(THEATERS_TOOL.run(region="강남")["data"]["theaters"]) > 0),
        ("실제 호출: 없는 지역 → NO_DATA",  lambda: code(THEATERS_TOOL.run(region="zzzz")) == NO_DATA),
    ]
    for label, check in checks:
        try:
            result = "PASS" if check() else "FAIL"
        except Exception as e:  # 아직 TODO면 여기로 온다
            result = f"FAIL ({type(e).__name__})"
        print(f"{result:<30} {label}")
