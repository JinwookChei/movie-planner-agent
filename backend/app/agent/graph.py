"""make_graph: analyze → plan → execute → judge → respond 연결. (M4)

그래프 = 노드(작업대) + 엣지(컨베이어 벨트).
  - StateGraph(State)           : 빈 그래프를 만든다. 모든 노드가 같은 State(쪽지) 하나를 돌려 쓴다
  - add_node(이름, 함수)         : 작업대를 놓는다. 함수는 state를 받아 '바꿀 키만' dict로 돌려준다
                                  → LangGraph가 그 dict를 State에 덮어쓴다 (돌려주지 않은 키는 그대로)
                                  이름은 엣지와 분기 함수가 가리킬 때 쓰는 문자열이다
  - add_edge(a, b)              : a가 끝나면 항상 b로
  - add_conditional_edges(a, f) : a가 끝나면 f(state)가 돌려준 이름의 노드로 (분기)

모양 (M5 재계획 포함)
  START → analyze ─┬─(되묻기 / chat)──────────────────────▶ respond → END
                   └─(plan / fact)→ plan → execute → judge ─┬─(맞는 영화 있음 / 멈춤)─▶ respond
                                     ▲                       │
                                     └──────(재계획)──────────┘

확인: backend/ 폴더에서  ..\\.venv\\Scripts\\python -m app.agent.graph
"""
import time
import uuid
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.agent import memory, nodes
from app.agent.nodes import State

Emit = Callable[..., None]


def _noop(event: str, **payload) -> None:
    pass


def _traced(name: str, fn: Callable, emit: Emit) -> Callable[[State], dict]:
    """노드 앞뒤로 step.enter / step.exit 이벤트를 보낸다 (F3012). 노드 함수는 이걸 몰라도 된다."""
    def run(state: State) -> dict:
        emit("step.enter", step=name)
        t0 = time.perf_counter()
        out = fn(state, emit)
        emit("step.exit", step=name, elapsed_ms=round((time.perf_counter() - t0) * 1000))
        return out
    return run


def route_after_analyze(state: State) -> str:
    """되묻기나 잡담이면 Tool 없이 바로 respond (F3006: Tool이 필요 없으면 계획을 건너뛴다)."""
    if state.get("ask") or state.get("intent") == "chat":
        return "respond"
    return "plan"


def route_after_judge(state: State) -> str:
    """재계획 여부는 judge 노드가 이미 정해 State["next"]에 적어 두었다. 여기서는 읽기만 한다.
    (분기 함수가 돌려준 State 변경은 버려진다 → replan_count를 올리는 일은 노드에서 해야 한다)"""
    return state.get("next", "respond")


def make_graph(emit: Emit = _noop):
    g = StateGraph(State)
    # 노드 5개 등록. 풀어 쓰면 g.add_node("analyze", _traced("analyze", nodes.analyze, emit)) × 5
    #   getattr(nodes, "analyze") == nodes.analyze  (이름 문자열로 모듈 안의 함수를 꺼낸다)
    #   _traced(...)로 한 번 감싸는 이유: 노드 함수는 (state, emit) 두 개를 받지만,
    #   LangGraph는 노드를 state 하나만 넣어 부른다 → emit을 미리 쥐여 주고 앞뒤 이벤트도 붙인다
    for name in ("analyze", "plan", "execute", "judge", "respond"):
        g.add_node(name, _traced(name, getattr(nodes, name), emit))

    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", route_after_analyze, ["plan", "respond"])
    g.add_edge("plan", "execute")
    g.add_edge("execute", "judge")
    # M5: judge 다음은 분기. "plan"이면 그래프가 뒤로 돌아간다(루프) → 재계획
    g.add_conditional_edges("judge", route_after_judge, ["plan", "respond"])
    g.add_edge("respond", END)
    return g.compile()


# ── 대화 한 턴 (M6). /api/chat(M8)도 이 함수를 부른다 ─────────────
def run_turn(session_id: str, message: str, emit: Emit = _noop) -> dict:
    """기억을 꺼내 그래프에 넣고 → 실행 → 결과로 기억을 갱신한다.

    그래프는 매번 빈 State로 시작한다. '대화가 이어지는 것'은 그래프가 아니라 이 함수가 만든다.
      ① memory.get      : 이전 조건·추천 요약·최근 3턴  → State["memory"]
      ② memory.get_cache: 같은 세션에서 이미 받은 Tool 결과 → State["cache"] (같은 조회 재사용)
      ③ invoke          : analyze가 이전 조건과 병합 → … → respond
      ④ memory.update   : 이번 조건·추천 요약·대화를 저장 (Tool 원본은 저장하지 않는다)
    """
    run_id = uuid.uuid4().hex[:12]
    emit("session.start", session_id=session_id, run_id=run_id, message=message)
    out = make_graph(emit).invoke(
        {"session_id": session_id, "run_id": run_id, "user": message,
         "memory": memory.get(session_id), "cache": memory.get_cache(session_id), "replan_count": 0},
        config={"recursion_limit": 25},  # 재계획 상한과 별개인 LangGraph 안전장치
    )
    memory.update(session_id, out.get("conditions", {}), out.get("recommendations", []),
                  message, out.get("answer", ""))
    memory.set_cache(session_id, out.get("cache", {}))
    emit("session.end", session_id=session_id, run_id=run_id,
         intent=out.get("intent"), replan_count=out.get("replan_count", 0))
    return {**out, "run_id": run_id}


# ── 스스로 확인: 대표 요청을 실제로 돌려 본다 ──────────────────
if __name__ == "__main__":
    import json
    import sys

    def printer(event: str, **payload) -> None:
        if event in ("step.exit",):
            return
        print(f"  [{event}] {json.dumps(payload, ensure_ascii=False)[:160]}")

    if "--mermaid" in sys.argv:
        print(make_graph().get_graph().draw_mermaid())
        raise SystemExit

    if "--chat" in sys.argv:  # M6: 같은 세션에서 R1 → R7
        turns = ["내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어", "같은 조건으로 홍대에서"]
    else:
        turns = sys.argv[1:] or [
            "내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어",   # R1
            "안녕",                                                      # R5
            "내일 7시 전에 끝나야 해",                                    # R6 되묻기
        ]
    sid = "demo-" + uuid.uuid4().hex[:6] if "--chat" in sys.argv else None
    for text in turns:
        print(f"\n▶ {text}")
        out = run_turn(sid or uuid.uuid4().hex[:8], text, printer)  # --chat이면 같은 세션 유지
        print("\n" + out["answer"])
