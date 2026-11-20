"""/api/plans CRUD + 겹침 검사. (M8, F3015, F3016)

계획 저장은 Tool이 아니다 (기획서 결정 2026-11-19). LLM이 판단할 일이 없고, 사용자가 [저장] 버튼으로 하는 일이기 때문이다.

겹침 규칙: 같은 watch_date에서  A.start < B.end  AND  B.start < A.end
  - 경계가 닿는 것(앞 계획 16:00 끝, 다음 계획 16:00 시작)은 겹침이 아니다
  - 겹쳐도 저장은 한다. 막지 않고 알리기만 한다 (F3015)
  - 수정할 때는 자기 자신을 빼고 검사한다
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status

from app.db import get_conn
from app.schemas import PlanIn, PlanOut, PlanSaved

router = APIRouter(prefix="/api/plans", tags=["plans"])

OVERLAP_SQL = """
SELECT * FROM plans
 WHERE watch_date = :watch_date
   AND start_time < :end_time      -- 다른 계획이 내 끝보다 먼저 시작하고
   AND :start_time < end_time      -- 내 시작이 다른 계획의 끝보다 앞선다
   AND (:exclude_id IS NULL OR id != :exclude_id)
 ORDER BY start_time
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _out(row) -> PlanOut:
    return PlanOut(**dict(row))


def find_overlaps(conn, p: PlanIn, exclude_id: int | None = None) -> list[PlanOut]:
    """HH:MM이 두 자리로 고정돼 있어 문자열 비교가 곧 시각 비교다 (schemas.HHMM)."""
    rows = conn.execute(OVERLAP_SQL, {"watch_date": p.watch_date.isoformat(), "start_time": p.start_time,
                                      "end_time": p.end_time, "exclude_id": exclude_id})
    return [_out(r) for r in rows]


def _get(conn, plan_id: int):
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"계획 {plan_id}번이 없습니다.")
    return row


@router.get("", response_model=list[PlanOut])
def list_plans():
    with get_conn() as conn:
        return [_out(r) for r in conn.execute("SELECT * FROM plans ORDER BY watch_date, start_time, id")]


@router.post("", response_model=PlanSaved, status_code=status.HTTP_201_CREATED)
def create_plan(p: PlanIn):
    now = _now()
    with get_conn() as conn:
        overlaps = find_overlaps(conn, p)           # 넣기 전에 검사 → 자기 자신은 아직 없다
        cur = conn.execute(
            "INSERT INTO plans (movie_nm, watch_date, start_time, end_time, theater, memo, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (p.movie_nm, p.watch_date.isoformat(), p.start_time, p.end_time, p.theater, p.memo, now, now))
        plan = _out(_get(conn, cur.lastrowid))
    return PlanSaved(plan=plan, overlaps=overlaps)


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int):
    with get_conn() as conn:
        return _out(_get(conn, plan_id))


@router.put("/{plan_id}", response_model=PlanSaved)
def update_plan(plan_id: int, p: PlanIn):
    with get_conn() as conn:
        _get(conn, plan_id)                          # 없으면 404
        conn.execute(
            "UPDATE plans SET movie_nm=?, watch_date=?, start_time=?, end_time=?, theater=?, memo=?, updated_at=?"
            " WHERE id=?",
            (p.movie_nm, p.watch_date.isoformat(), p.start_time, p.end_time, p.theater, p.memo, _now(), plan_id))
        overlaps = find_overlaps(conn, p, exclude_id=plan_id)  # 자기 자신은 빼고
        plan = _out(_get(conn, plan_id))
    return PlanSaved(plan=plan, overlaps=overlaps)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(plan_id: int):
    with get_conn() as conn:
        _get(conn, plan_id)
        conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
