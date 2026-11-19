"""Tool 공통 약속: ok / fail / Tool / scrub. (M2-1, F3001, NF3002, NF3003)

모든 Tool은 이 파일의 형식으로만 대답한다.
  성공: {"ok": True,  "data": {...}}
  실패: {"ok": False, "error": {"code": "...", "message": "..."}}

TODO 표시가 있는 곳을 직접 채운다. 나머지는 완성된 뼈대다.
확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.tools.base
"""
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.config import GMS_API_KEY, KAKAO_REST_API_KEY, KOBIS_API_KEY, TMDB_API_KEY

# 실패 코드 (기획서 3.2절). 문자열 오타를 막으려고 상수로 둔다.
BAD_ARGUMENT = "BAD_ARGUMENT"
INVALID_KEY = "INVALID_KEY"
UPSTREAM_ERROR = "UPSTREAM_ERROR"
NOT_FOUND = "NOT_FOUND"
NO_DATA = "NO_DATA"
NO_RUNTIME = "NO_RUNTIME"

ToolResult = dict[str, Any]

# 가릴 키 값 목록. 빈 문자열(키가 없는 경우)도 들어 있을 수 있다는 점에 주의.
_SECRETS = [KOBIS_API_KEY, TMDB_API_KEY, KAKAO_REST_API_KEY, GMS_API_KEY]

# 이름 패턴: 값으로 못 가린 키(틀린 키, 다른 키)도 형태로 가린다.
#   "?key=abc" / "&api_key=abc" / "apikey=abc" / "token=abc"  →  이름=***
#   "KakaoAK abc" / "wrong appKey(abc)"                        →  KakaoAK *** / appKey(***)
# 샘플의 r"\b(key|...)=" 는 api_key= 를 못 잡는다('_'도 단어 문자라 \b 경계가 안 생김).
# 그래서 \b 대신 "앞이 영문자/숫자가 아님"을 직접 조건으로 쓴다.
_PARAM_PATTERNS: list[re.Pattern] = [
    # group(1) = 남길 앞부분(이름), group(2) = 가릴 값
    re.compile(r"(?<![A-Za-z0-9])((?:(?:api_?)?key|token)=)([^&\s'\"]+)", re.IGNORECASE),
    re.compile(r"(KakaoAK\s+)(\S+)"),
    re.compile(r"(appKey\()([^)]*)", re.IGNORECASE),  # 카카오 401 메시지: wrong appKey(키) format
]


def scrub(text: Any) -> str:
    """오류 메시지 등에서 키를 가린다. 어떤 값이 들어와도 str을 돌려준다."""
    text = str(text)
    # 1) 값으로 가리기. 빈 키는 건너뛴다 ("abc".replace("", "*") == "*a*b*c*")
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "***")
    # 2) 이름 패턴으로 가리기. group(1)=이름 부분은 살리고 값만 ***로
    for pattern in _PARAM_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}***", text)
    return text


def ok(data: dict) -> ToolResult:
    return {"ok": True, "data": data}


def fail(code: str, message: str) -> ToolResult:
    # 메시지는 항상 scrub을 거친다. Tool 작성자가 깜빡해도 키가 새지 않게.
    return {"ok": False, "error": {"code": code, "message": scrub(message)}}


@dataclass
class Tool:
    """Agent에게 보여 줄 Tool 명함 + 실행 함수."""

    name: str
    description: str          # "언제 부르는지"를 쓴다 (LLM이 읽는다)
    parameters: dict          # JSON Schema: {"type": "object", "properties": {...}, "required": [...]}
    func: Callable[..., ToolResult]
    kind: Literal["external", "local"] = "external"  # 기본값 있는 필드는 뒤에 (F3001)

    def run(self, **kwargs: Any) -> ToolResult:
        """Tool을 실행한다. 어떤 경우에도 예외를 밖으로 내보내지 않는다 (NF3003)."""
        # ── TODO 4: 예외를 실패 코드로 바꾸기 ─────────────────
        # - 정상: self.func(**kwargs)의 결과를 그대로 돌려준다.
        # - TypeError(인자 이름이 틀리거나 빠짐) → fail(BAD_ARGUMENT, "...")
        # - 그 밖의 모든 Exception              → fail(UPSTREAM_ERROR, "...")
        # 함정: except 순서. TypeError도 Exception의 자식이다.
        # 생각할 것: 내 코드의 KeyError 버그도 UPSTREAM_ERROR로 숨는다. 개발 중엔 어떻게 알아챌까?
        #            (힌트: traceback.print_exc() 또는 logging — 단, 화면/응답에는 내보내지 않는다)

        try:
            return self.func(**kwargs)  # 받을 때 묶은 kwargs를 다시 풀어서 넘긴다
        except TypeError:
            # 좁은 예외 먼저. LLM이 인자 이름을 틀리게 만들었을 때 주로 생긴다.
            traceback.print_exc()  # 서버 콘솔에만 원인을 남긴다 (응답에는 넣지 않음)
            return fail(BAD_ARGUMENT, f"{self.name}: 입력 인자가 규격과 다르다.")
        except Exception:
            # 넓은 예외는 마지막. 네트워크 오류, 응답 구조 변화, 그리고 내 코드 버그도 여기로 온다.
            traceback.print_exc()
            return fail(UPSTREAM_ERROR, f"{self.name}: 실행 중 오류가 생겼다.")


# ── 스스로 확인 ────────────────────────────────────────
if __name__ == "__main__":
    def _boom() -> ToolResult:
        return {"x": 1 / 0}

    def _echo(word: str) -> ToolResult:
        return ok({"word": word})

    checks = [
        ("TODO1·3 scrub key=",      lambda: "abc" not in scrub("u?key=abc&x=1")),
        ("TODO1·3 scrub api_key=",  lambda: "abc" not in scrub("u?x=1&api_key=abc")),
        ("TODO1·3 scrub KakaoAK",   lambda: "abc" not in scrub("Authorization: KakaoAK abc")),
        ("TODO2   빈 키에도 안전",    lambda: scrub("hello") == "hello"),
        ("TODO2   실제 키 값 가림",   lambda: not KOBIS_API_KEY or KOBIS_API_KEY not in scrub(f"msg {KOBIS_API_KEY}")),
        ("        fail도 scrub",     lambda: "abc" not in fail("X", "key=abc")["error"]["message"]),
        ("TODO4   run 정상",         lambda: Tool("e", "d", {}, _echo).run(word="hi") == ok({"word": "hi"})),
        ("TODO4   예외→UPSTREAM",    lambda: Tool("b", "d", {}, _boom).run()["error"]["code"] == UPSTREAM_ERROR),
        ("TODO4   인자 틀림→BAD_ARG", lambda: Tool("e", "d", {}, _echo).run(foo=1)["error"]["code"] == BAD_ARGUMENT),
    ]
    for label, check in checks:
        try:
            result = "PASS" if check() else "FAIL"
        except Exception as e:  # 확인용: 아직 TODO면 여기로 온다
            result = f"FAIL ({type(e).__name__})"
        print(f"{result:<28} {label}")
