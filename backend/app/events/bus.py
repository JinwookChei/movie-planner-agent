"""EventBus: subscribe / publish. (M7, F3012)

왜 버스를 두는가
  메인 Agent가 로그 저장 코드를 직접 부르면(logger.save(...)) 둘이 묶인다.
  저장이 느려지면 대화도 느려지고, 저장이 터지면 대화도 터진다.
  그래서 가운데에 '게시판'을 둔다.
    - 발행자(메인 Agent) : emit("tool.call", ...) 으로 게시판에 붙이기만 한다. 누가 읽는지 모른다.
    - 구독자(로깅 Agent, 화면용 수집기 …) : 게시판을 구독해 두면 새 글이 올 때마다 불린다.
  구독자를 빼거나 더해도 메인 Agent 코드는 한 줄도 바뀌지 않는다 (체크리스트 15).

메시지 공통 형식 (기획서 5.1)
  {event, session_id, run_id, seq, ts, payload}
    - session_id : 대화 하나 (여러 턴)          → 로그를 세션 단위로 묶는 열쇠 (체크리스트 14)
    - run_id     : 그 대화의 한 턴 (그래프 1회 실행)
    - seq        : 한 턴 안에서 1, 2, 3 … 발생 순서
    - ts         : 발생 시각 (밀리초까지)
    - payload    : 이벤트마다 다른 내용 (tool, args, elapsed_ms …)

노드들은 emit(event, **payload)만 안다. session_id·run_id·seq·ts는
emitter()가 만든 emit 함수가 대신 채워 준다 → 노드 코드는 M4 그대로 둔다.
"""
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from itertools import count
from typing import Any

Message = dict[str, Any]
Subscriber = Callable[[Message], None]
Emit = Callable[..., None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()  # FastAPI는 요청을 여러 스레드에서 처리한다 → 목록을 고칠 때 잠근다
        self.errors = 0                # 구독자가 던진 예외 수 (시험용)

    def subscribe(self, fn: Subscriber) -> Subscriber:
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)
        return fn

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def publish(self, msg: Message) -> None:
        """모든 구독자에게 전달한다. 구독자 하나가 실패해도 다른 구독자와 발행자는 계속 간다."""
        with self._lock:
            subs = list(self._subs)  # 복사본으로 돈다: 전달 중에 누가 구독을 풀어도 안전
        for fn in subs:
            try:
                fn(msg)
            except Exception as e:  # 삼킨다 (F3013: 저장이 실패해도 대화는 정상)
                self.errors += 1
                print(f"[bus] 구독자 {getattr(fn, '__name__', fn)} 실패: {type(e).__name__}", file=sys.stderr)

    def emitter(self, session_id: str, run_id: str) -> Emit:
        """한 턴 전용 emit 함수를 만든다. 노드는 이 함수를 받아 emit("이름", 키=값)만 부른다."""
        seq = count(1)

        def emit(event: str, **payload: Any) -> None:
            self.publish({
                "event": event,
                "session_id": session_id,
                "run_id": run_id,
                "seq": next(seq),
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "payload": payload,
            })
        return emit


# 앱 전체가 함께 쓰는 버스 하나. 발행자와 구독자는 서로를 모르고 이것만 안다.
bus = EventBus()
