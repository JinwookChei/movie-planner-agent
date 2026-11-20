"""Tool 레지스트리: Tool을 한곳에 모으고, LLM에게 보여 줄 '메뉴판'을 만든다. (M2-5, F3001)

흐름 (Agent 한 번 실행 기준)
  ① plan 노드   : catalog()를 프롬프트에 넣는다 → LLM은 '이름·설명·입력 스키마'만 보고
                  어떤 Tool을 어떤 순서로 부를지 계획한다.  예: [{"tool": "get_box_office", "args": {...}}]
  ② execute 노드: 계획의 "tool" 이름으로 TOOLS[이름]을 찾아 .run(**args) 한다.
                  실제 실행은 코드가 한다. LLM은 func를 볼 수도, 부를 수도 없다.
  ③ judge 노드  : Tool 결과(ok/fail)를 받아 시간 판단을 한다.

왜 둘로 나누나 (TOOLS vs catalog)
  - TOOLS   : 코드용. Tool 객체 전체(func, kind 포함). 이름 → 실행.
  - catalog : LLM용. 판단에 필요한 정보만. func(파이썬 함수)는 JSON이 될 수 없고,
              kind(external/local)는 내부 분류라 LLM의 선택에 필요 없다.
  LLM이 없는 이름을 만들어 내면 TOOLS에 없으므로 execute에서 BAD_ARGUMENT로 처리한다.

확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.tools.registry
"""
from app.tools.base import Tool
from app.tools.kakao import THEATERS_TOOL
from app.tools.kobis import BOX_OFFICE_TOOL, MOVIE_DETAIL_TOOL
from app.tools.tmdb import NEW_RELEASES_TOOL

# 이름은 각 Tool의 .name에서 꺼낸다. 문자열을 다시 손으로 적으면 두 곳이 어긋날 수 있다.
TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (BOX_OFFICE_TOOL, MOVIE_DETAIL_TOOL, NEW_RELEASES_TOOL, THEATERS_TOOL)
}


def catalog() -> list[dict]:
    """LLM에게 넘길 Tool 목록. 이름·설명·입력 스키마 3가지만 담는다 (F3001)."""
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in TOOLS.values()
    ]


# ── 스스로 확인 ────────────────────────────────────────
if __name__ == "__main__":
    import json

    print("TOOLS:", {name: t.kind for name, t in TOOLS.items()})
    print("catalog 키:", [sorted(c) for c in catalog()][0])
    print("JSON 직렬화 가능:", bool(json.dumps(catalog(), ensure_ascii=False)))
    print("\n[LLM이 보는 메뉴판]")
    for c in catalog():
        #print(f"- {c['name']}: {c['description'][:50]}...")
        print(f"- {c['name']}: {c['description']}")
