"""로깅 Agent: EventBus를 구독해 event_logs에 저장한다. LLM 없음. (M7, F3013)

지켜야 할 것
  1. LLM을 쓰지 않는다         : 받은 메시지를 '판단 없이' 그대로 적는다. 요약하거나 고치지 않는다.
  2. 세션 단위, 발생 순서대로   : session_id·run_id·seq를 함께 저장 → 조회는 session_id로, 정렬은 id 순.
  3. 원본을 그대로             : payload 전체를 JSON 문자열로 저장한다. (키는 Tool의 scrub이 이미 가렸다)
  4. 실패해도 대화는 정상       : 저장 예외를 여기서 삼킨다. 버스도 한 번 더 삼킨다(이중 안전장치).
  5. 메인 Agent는 이 파일을 모른다 : 구독 연결은 main.py(M8)에서 start() 한 줄로만 한다.

확인 (backend/ 폴더에서)
  ..\\.venv\\Scripts\\python -m app.events.logger_agent          # 가짜 이벤트로 자가 확인 (LLM·API 호출 없음)
  ..\\.venv\\Scripts\\python -m app.events.logger_agent --live   # 진짜 대화 2세션 → app.db에 저장된 것 확인
"""
import json
import sys

from app import config, db
from app.events.bus import EventBus, Message, bus

INSERT_SQL = """INSERT INTO event_logs (session_id, run_id, seq, event, ts, payload)
                VALUES (?, ?, ?, ?, ?, ?)"""

failures = 0  # 저장 실패 수 (시험용)


def handle(msg: Message) -> None:
    """메시지 하나 = INSERT 한 줄."""
    global failures
    try:
        if config.LOG_FAIL:
            raise RuntimeError("저장 실패 시험 (LOG_FAIL=1)")
        payload = json.dumps(msg.get("payload", {}), ensure_ascii=False, default=str)  # 한글 그대로
        with db.get_conn() as conn:
            conn.execute(INSERT_SQL, (msg["session_id"], msg["run_id"], msg["seq"],
                                      msg["event"], msg["ts"], payload))
    except Exception as e:
        failures += 1
        print(f"[logger] 저장 실패({type(e).__name__}) — 대화는 계속한다", file=sys.stderr)


def start(target: EventBus = bus) -> None:
    """앱 시작 시 한 번: 테이블을 준비하고 구독한다."""
    db.init_db()
    target.subscribe(handle)


def stop(target: EventBus = bus) -> None:
    target.unsubscribe(handle)


# ── 스스로 확인 ──────────────────────────────────────────
def _self_check() -> None:
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db.DB_PATH = tmp  # 진짜 app.db를 더럽히지 않게 임시 DB로 바꾼다 (get_conn은 db.DB_PATH를 읽는다)
    results = []

    def check(name: str, cond: bool) -> None:
        results.append(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    def rows(sid: str | None = None) -> list[dict]:
        with db.get_conn() as conn:
            q = "SELECT * FROM event_logs" + (" WHERE session_id=?" if sid else "") + " ORDER BY id"
            return [dict(r) for r in conn.execute(q, (sid,) if sid else ())]

    b = EventBus()
    start(b)
    check("1 start() 후 구독됨", handle in b._subs)

    # 두 세션이 번갈아 이벤트를 낸다
    e1, e2 = b.emitter("S1", "r1"), b.emitter("S2", "r2")
    e1("session.start", message="내일 강남")
    e2("session.start", message="안녕")
    e1("tool.call", tool="get_box_office", args={"target_date": "2026-11-21"})
    e2("session.end", intent="chat")
    e1("session.end", intent="plan")
    s1, s2 = rows("S1"), rows("S2")
    check("2 세션별로 섞이지 않음 (S1 3건, S2 2건)", len(s1) == 3 and len(s2) == 2)
    check("3 발생 순서 seq 1,2,3", [r["seq"] for r in s1] == [1, 2, 3])
    check("4 이벤트 이름 순서 보존",
          [r["event"] for r in s1] == ["session.start", "tool.call", "session.end"])
    check("5 payload 원본 그대로 (한글·중첩 dict)",
          json.loads(s1[1]["payload"]) == {"tool": "get_box_office", "args": {"target_date": "2026-11-21"}}
          and json.loads(s1[0]["payload"])["message"] == "내일 강남")

    # 저장 실패 시험: 발행자는 예외를 못 느끼고, 다른 구독자는 계속 받는다
    got = []
    b.subscribe(got.append)
    config.LOG_FAIL = True
    before, f0 = len(rows()), failures
    try:
        e1("tool.call", tool="search_theaters", args={"region": "강남"})
        raised = False
    except Exception:
        raised = True
    config.LOG_FAIL = False
    check("6 저장 실패해도 emit은 예외 없음", not raised)
    check("7 실패는 기록만 하고 행은 늘지 않음", failures == f0 + 1 and len(rows()) == before)
    check("8 다른 구독자는 정상 수신", len(got) == 1 and got[0]["event"] == "tool.call")

    # 구독자가 예외를 던져도 버스가 삼킨다
    def broken(_msg: Message) -> None:
        raise ValueError("고장 난 구독자")
    b.subscribe(broken)
    e1("judge.result", fit=[])
    check("9 고장 난 구독자도 버스가 삼킴 (errors 1)", b.errors == 1 and len(got) == 2)
    b.unsubscribe(broken)

    # 구독을 빼면 저장되지 않는다 (= 메인 Agent는 로깅과 무관하게 동작)
    stop(b)
    n = len(rows())
    e1("session.end", intent="plan")
    check("10 stop() 후에는 저장 안 함", len(rows()) == n)

    # 의존 방향: 로깅 Agent는 Agent·LLM을 모르고, Agent 코드는 로깅 Agent를 모른다
    check("11 로깅 Agent가 Agent·LLM 모듈을 불러오지 않음",
          not any(m.startswith(("app.agent", "langchain")) for m in sys.modules))
    import re
    agent_dir = Path(__file__).resolve().parent.parent / "agent"
    imp = re.compile(r"^\s*(from|import)\s[^#\n]*logger_agent", re.M)  # import 문만, # 뒤 주석은 빼고
    check("12 Agent 코드에 logger_agent import 없음",
          not any(imp.search(p.read_text(encoding="utf-8")) for p in agent_dir.glob("*.py")))

    print(f"\n자가 확인 {sum(results)}/{len(results)}")


def _live_check() -> None:
    """진짜 대화 2세션을 돌려 app.db에 세션별로 쌓이는지 본다 (LLM·API 호출 있음)."""
    from app.agent.graph import run_turn  # 시험 코드에서만 Agent를 부른다

    start()
    sids = []
    for sid, text in [("live-A", "내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어"), ("live-B", "안녕")]:
        sid = f"{sid}-{__import__('uuid').uuid4().hex[:4]}"
        sids.append(sid)
        out = run_turn(sid, text)
        print(f"▶ {sid}: {text}\n  → {out['answer'][:60]}…")
    with db.get_conn() as conn:
        for sid in sids:
            evs = [dict(r) for r in conn.execute(
                "SELECT seq, event, payload FROM event_logs WHERE session_id=? ORDER BY id", (sid,))]
            print(f"\n[{sid}] {len(evs)}건")
            for e in evs:
                print(f"  {e['seq']:>3} {e['event']:<16} {e['payload'][:90]}")


if __name__ == "__main__":
    _live_check() if "--live" in sys.argv else _self_check()
