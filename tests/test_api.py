"""M8 API 시험. LLM·외부 API를 부르지 않는다 (run_turn을 가짜로 바꾼다).

실행 (루트에서):  .venv\\Scripts\\python -m pytest -q
"""
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.api import chat as chat_api
from app.events import logger_agent
from app.events.bus import bus
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")  # 진짜 app.db를 건드리지 않는다

    def fake_run_turn(session_id, message, run_id=None):
        emit = bus.emitter(session_id, run_id)
        emit("session.start", message=message)
        emit("llm.decision", step="analyze", intent="plan", reason="시험")
        emit("session.end", ok=True)
        return {"intent": "plan", "answer": "폭설은 늦어도 17:12에 시작하세요.",
                "conditions": {"watch_date": "2026-11-21", "region": "강남", "genre": None},
                "recommendations": [{"movie_nm": "폭설", "show_tm": 88, "need_min": 108, "latest_start": "17:12",
                                     "expected_end": None, "source": "KOBIS", "fit": True}],
                "judged": {"excluded": [{"movie_nm": "영화 D", "code": "NO_RUNTIME", "reason": "러닝타임 정보가 없어 판단하지 않았다.", "source": "KOBIS"}]},
                "theaters": [{"name": "CGV 강남", "address": "서울", "url": "http://x", "phone": None}],
                "replan_count": 0, "notices": [], "cache": {"비밀": "내부 값"}}

    monkeypatch.setattr(chat_api, "run_turn", fake_run_turn)
    with TestClient(app) as c:   # with → lifespan 실행 (init_db + logger_agent.start)
        yield c


PLAN = {"movie_nm": "폭설", "watch_date": "2026-11-21", "start_time": "14:00", "end_time": "15:50"}


# ── plans ──
def test_plan_crud_and_404(client):
    r = client.post("/api/plans", json=PLAN)
    assert r.status_code == 201 and r.json()["overlaps"] == []
    pid = r.json()["plan"]["id"]
    assert client.get(f"/api/plans/{pid}").json()["movie_nm"] == "폭설"
    r = client.put(f"/api/plans/{pid}", json={**PLAN, "memo": "팝콘"})
    assert r.status_code == 200 and r.json()["plan"]["memo"] == "팝콘"
    assert client.delete(f"/api/plans/{pid}").status_code == 204
    assert client.get(f"/api/plans/{pid}").status_code == 404
    assert client.delete(f"/api/plans/{pid}").status_code == 404


def test_overlap_saved_but_reported(client):
    a = client.post("/api/plans", json=PLAN).json()["plan"]
    r = client.post("/api/plans", json={**PLAN, "movie_nm": "B", "start_time": "15:00", "end_time": "17:00"})
    assert r.status_code == 201                                   # 겹쳐도 저장은 한다
    assert [p["id"] for p in r.json()["overlaps"]] == [a["id"]]   # 겹친 계획을 알린다
    assert len(client.get("/api/plans").json()) == 2


def test_touching_boundary_and_other_day_not_overlap(client):
    client.post("/api/plans", json=PLAN)
    r1 = client.post("/api/plans", json={**PLAN, "start_time": "15:50", "end_time": "17:00"})  # 끝=시작
    r2 = client.post("/api/plans", json={**PLAN, "watch_date": "2026-11-22"})                   # 다른 날
    assert r1.json()["overlaps"] == [] and r2.json()["overlaps"] == []


def test_update_excludes_itself(client):
    pid = client.post("/api/plans", json=PLAN).json()["plan"]["id"]
    r = client.put(f"/api/plans/{pid}", json={**PLAN, "end_time": "16:00"})
    assert r.json()["overlaps"] == []


def test_list_sorted(client):
    client.post("/api/plans", json={**PLAN, "watch_date": "2026-11-22"})
    client.post("/api/plans", json={**PLAN, "start_time": "18:00", "end_time": "20:00"})
    client.post("/api/plans", json=PLAN)
    got = [(p["watch_date"], p["start_time"]) for p in client.get("/api/plans").json()]
    assert got == sorted(got)


@pytest.mark.parametrize("bad", [{"start_time": "7시"}, {"start_time": "24:00"}, {"end_time": "13:00"},
                                 {"watch_date": "2026/11/21"}, {"movie_nm": ""}])
def test_plan_validation_422(client, bad):
    assert client.post("/api/plans", json={**PLAN, **bad}).status_code == 422


# ── chat ──
def test_chat_issues_session_and_filters_internal_state(client):
    r = client.post("/api/chat", json={"message": "내일 강남"})
    assert r.status_code == 200
    j = r.json()
    assert j["session_id"] and j["run_id"]
    assert [e["event"] for e in j["events"]] == ["session.start", "llm.decision", "session.end"]
    assert j["recommendations"][0]["latest_start"] == "17:12"
    assert j["excluded"][0]["code"] == "NO_RUNTIME"
    assert "cache" not in j and "genre" not in j["conditions"]   # 내부 값·빈 조건은 내보내지 않는다
    r2 = client.post("/api/chat", json={"session_id": j["session_id"], "message": "같은 조건으로"})
    assert r2.json()["session_id"] == j["session_id"]             # 받은 세션을 이어 쓴다


def test_chat_events_survive_log_failure(client, monkeypatch):
    """체크리스트 17: 저장이 실패해도 대화와 화면용 events[]는 정상."""
    monkeypatch.setattr(config, "LOG_FAIL", True)
    j = client.post("/api/chat", json={"message": "x"}).json()
    assert j["answer"] and len(j["events"]) == 3
    assert client.get(f"/api/logs/{j['session_id']}").status_code == 404   # DB에는 안 남았다


def test_chat_works_without_logger(client):
    """체크리스트 15: 로깅 Agent 구독을 빼도 응답은 정상."""
    logger_agent.stop()
    j = client.post("/api/chat", json={"message": "x"}).json()
    assert j["answer"] and len(j["events"]) == 3
    logger_agent.start()


def test_chat_graph_crash_is_friendly(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail sk-123")
    monkeypatch.setattr(chat_api, "run_turn", boom)
    r = client.post("/api/chat", json={"message": "x"})
    assert r.status_code == 200 and "sk-123" not in r.text and r.json()["notices"]


def test_chat_validation(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


# ── logs ──
def test_logs_list_detail_delete_and_no_update(client):
    s1 = client.post("/api/chat", json={"message": "첫 세션"}).json()["session_id"]
    s2 = client.post("/api/chat", json={"message": "둘째 세션"}).json()["session_id"]
    rows = {x["session_id"]: x for x in client.get("/api/logs").json()}
    assert rows[s1]["first_message"] == "첫 세션" and rows[s1]["event_count"] == 3
    detail = client.get(f"/api/logs/{s2}").json()
    assert {e["session_id"] for e in detail} == {s2}               # 체크리스트 14: 섞이지 않는다
    assert [e["seq"] for e in detail] == [1, 2, 3]
    assert client.put(f"/api/logs/{s1}", json={}).status_code == 405   # 수정 API 없음 (F3014)
    assert client.delete(f"/api/logs/{s1}").status_code == 204
    assert client.get(f"/api/logs/{s1}").status_code == 404
    assert client.get(f"/api/logs/{s2}").status_code == 200


# ── health ──
def test_health_bools_only(client):
    j = client.get("/api/health").json()
    assert set(j["keys"]) == {"gms", "kobis", "tmdb", "kakao"}
    assert all(isinstance(v, bool) for v in j["keys"].values())
    assert config.KOBIS_API_KEY == "" or config.KOBIS_API_KEY not in client.get("/api/health").text


def test_cors_allows_frontend(client):
    r = client.options("/api/plans", headers={"Origin": "http://localhost:5500", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5500"
