"""POST /api/chat. (M8, F3011, F3017, F3019)

한 번의 요청 = run_turn() 한 번 = 그래프 한 번.

흐름
  ① session_id가 없으면 새로 발급한다 (화면은 받은 값을 다음 요청부터 계속 보낸다 → M6 memory가 이어진다)
  ② run_id를 먼저 정하고, 'run_id가 같은 이벤트만 모으는 임시 구독자'를 버스에 붙인다
  ③ run_turn() 실행
  ④ 임시 구독자를 뗀다 (finally: 예외가 나도 반드시)
  ⑤ State에서 화면에 필요한 것만 골라 ChatOut으로 만든다 (cache, results 같은 내부 값은 내보내지 않는다)

왜 events[]를 DB에서 읽지 않고 임시 구독자로 모으나
  DB는 로깅 Agent가 채운다. LOG_FAIL=1처럼 저장이 실패하면 DB에는 아무것도 없다.
  그러면 화면의 실행 과정 패널도 비게 된다 → 로깅이 실패해도 대화(와 화면)는 정상이어야 한다는 F3013에 어긋난다.
  임시 구독자는 로깅 Agent와 나란히 버스를 듣는 '또 하나의 구독자'라 서로 영향을 주지 않는다.

def(동기)로 둔 이유: run_turn 안의 LLM·HTTP 호출은 블로킹이다. FastAPI는 def 엔드포인트를 스레드 풀에서 돌려서
  한 요청이 기다리는 동안 다른 요청(계획 목록 조회 등)이 막히지 않는다. async def로 쓰면 오히려 서버 전체가 멈춘다.
"""
import sys
import uuid

from fastapi import APIRouter

from app.agent.graph import run_turn
from app.events.bus import bus
from app.schemas import ChatIn, ChatOut, Event

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatOut)
def chat(body: ChatIn):
    session_id = body.session_id or uuid.uuid4().hex[:12]
    run_id = uuid.uuid4().hex[:12]
    collected: list[dict] = []

    def collect(msg: dict) -> None:
        if msg["run_id"] == run_id:          # 동시에 다른 사람의 요청이 돌아도 내 이벤트만
            collected.append(msg)

    bus.subscribe(collect)
    try:
        out = run_turn(session_id, body.message, run_id=run_id)
    except Exception as e:  # 그래프가 예상 못 한 이유로 죽어도 500 상세를 내보내지 않는다 (NF3002)
        print(f"[chat] run_turn 실패: {type(e).__name__}", file=sys.stderr)
        return ChatOut(session_id=session_id, run_id=run_id, intent=None,
                       answer="지금은 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
                       notices=["서버에서 요청을 처리하는 중 문제가 생겼습니다."],
                       events=[Event(**_ev(m)) for m in collected])
    finally:
        bus.unsubscribe(collect)

    judged = out.get("judged") or {}
    return ChatOut(
        session_id=session_id,
        run_id=run_id,
        intent=out.get("intent"),
        answer=out.get("answer", ""),
        conditions={k: v for k, v in (out.get("conditions") or {}).items() if v is not None},
        recommendations=out.get("recommendations", []),
        excluded=judged.get("excluded", []),
        theaters=out.get("theaters", []),
        replan_count=out.get("replan_count", 0),
        replan_history=out.get("replan_history", []),
        notices=out.get("notices", []),
        events=[Event(**_ev(m)) for m in collected],
    )


def _ev(msg: dict) -> dict:
    return {"seq": msg["seq"], "event": msg["event"], "ts": msg["ts"], "payload": msg["payload"]}
