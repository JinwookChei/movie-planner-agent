"""SQLite 연결과 테이블 생성 (기획서 5.2절).

FastAPI는 요청을 여러 스레드에서 처리한다. sqlite3 연결은 기본적으로 만든 스레드에서만 쓸 수 있으므로
'필요할 때마다 연결을 열고 닫는' 방식을 쓴다. (with get_conn() as conn: ...)
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  movie_nm TEXT NOT NULL,
  watch_date TEXT NOT NULL,     -- YYYY-MM-DD
  start_time TEXT NOT NULL,     -- HH:MM
  end_time TEXT NOT NULL,       -- HH:MM
  theater TEXT,
  memo TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event TEXT NOT NULL,
  ts TEXT NOT NULL,
  payload TEXT NOT NULL         -- JSON 문자열
);

CREATE INDEX IF NOT EXISTS idx_logs_session ON event_logs(session_id, id);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """연결을 열고, 예외 없이 끝나면 commit, 예외가 나면 rollback한 뒤 닫는다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # dict(row)로 바꾸기 쉽다
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """data/ 폴더와 테이블을 만든다. 여러 번 불러도 안전하다 (IF NOT EXISTS)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
