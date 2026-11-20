"""/api/logs 목록·상세·삭제. 수정 API는 두지 않는다. (M8, F3014)

왜 수정(PUT/PATCH)이 없나: 로그는 '무슨 일이 있었는지'의 증거다. 고칠 수 있으면 증거가 아니다.
이 파일은 읽기만 한다. 쓰기는 로깅 Agent(events/logger_agent.py) 한 곳에서만 일어난다.
"""
import json

from fastapi import APIRouter, HTTPException, Response, status

from app.db import get_conn
from app.schemas import LogEvent, LogSummary

router = APIRouter(prefix="/api/logs", tags=["logs"])

# 세션 하나 = 한 줄. 첫 메시지는 그 세션의 첫 session.start 이벤트 payload에서 꺼낸다 (SQLite JSON 함수)
LIST_SQL = """
SELECT e.session_id,
       MIN(e.ts)  AS started_at,
       COUNT(*)   AS event_count,
       (SELECT json_extract(s.payload, '$.message') FROM event_logs s
         WHERE s.session_id = e.session_id AND s.event = 'session.start'
         ORDER BY s.id LIMIT 1) AS first_message
  FROM event_logs e
 GROUP BY e.session_id
 ORDER BY started_at DESC
"""


@router.get("", response_model=list[LogSummary])
def list_logs():
    with get_conn() as conn:
        return [LogSummary(**dict(r)) for r in conn.execute(LIST_SQL)]


@router.get("/{session_id}", response_model=list[LogEvent])
def get_log(session_id: str):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM event_logs WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 세션의 로그가 없습니다.")
    return [LogEvent(**{**dict(r), "payload": json.loads(r["payload"])}) for r in rows]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_log(session_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM event_logs WHERE session_id = ?", (session_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
