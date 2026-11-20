"""judge.evaluate 경계값 테스트. (M3, 구현가이드 M3 ③ 표)

실행 (루트에서): .venv\\Scripts\\python -m pytest tests/test_judge.py -v
루트의 pytest.ini가 backend/를 import 경로에 넣어 준다(pythonpath = backend).
"""
from app.agent.judge import EXCLUDED, FIT, UNFIT, evaluate

BUF = 20  # 테스트는 설정값이 바뀌어도 흔들리지 않게 광고 시간을 직접 준다


def mv(nm, show_tm, genres=None, source="KOBIS", error=None):
    return {"movie_nm": nm, "show_tm": show_tm, "genres": genres or [], "source": source, "error": error}


def run(movie, start, end, genre=None):
    return evaluate([movie], {"start_after": start, "end_before": end, "genre": genre}, BUF)


# ── 구현가이드 표의 4개 ─────────────────────────────────
def test_fit_returns_latest_start():
    j = run(mv("A", 132), "14:00", "19:00")
    assert len(j[FIT]) == 1
    assert j[FIT][0]["latest_start"] == "16:28"   # 19:00 − (132 + 20)


def test_unfit_reason_has_numbers():
    j = run(mv("B", 141), "15:00", "17:30")
    assert len(j[UNFIT]) == 1
    reason = j[UNFIT][0]["reason"]
    assert "161" in reason and "150" in reason      # 필요 161분 > 가능 150분


def test_no_end_is_fit_with_expected_end():
    j = run(mv("C", 120), "15:00", None)
    assert j[FIT][0]["expected_end"] == "17:20"


def test_no_runtime_is_excluded():
    j = run(mv("E", None), "15:00", "19:00")
    assert j[EXCLUDED][0]["code"] == "NO_RUNTIME"
    assert j[FIT] == [] and j[UNFIT] == []


# ── 경계와 함정 ──────────────────────────────────────
# 위 4개는 '평범한' 경우다. 버그는 경계에서 난다.
def test_boundary_exact_fit_and_one_minute_over():
    # 15:00~17:00 = 120분. 100 + 20 = 120 → 딱 맞음(fit), 101 + 20 = 121 → 1분 모자람(unfit)
    assert len(run(mv("D", 100), "15:00", "17:00")[FIT]) == 1
    assert len(run(mv("D", 101), "15:00", "17:00")[UNFIT]) == 1


def test_unknown_genre_is_not_rejected():
    # TMDB 신작은 genres=[] (모름). 장르 조건이 있어도 시간만으로 판단하고, notes로 밝힌다.
    j = run(mv("T", 100, genres=[], source="TMDB"), "15:00", "19:00", genre="드라마")
    assert len(j[FIT]) == 1
    assert any("장르" in n for n in j["notes"])


def test_known_different_genre_is_unfit():
    j = run(mv("G", 90, genres=["코미디"]), "15:00", "19:00", genre="드라마")
    assert j[UNFIT][0]["reason"].startswith("장르")


def test_not_found_keeps_its_code():
    # error를 먼저 봐야 NOT_FOUND가 NO_RUNTIME으로 뭉개지지 않는다
    j = run(mv("F", None, error={"code": "NOT_FOUND", "message": "x"}), "15:00", "19:00")
    assert j[EXCLUDED][0]["code"] == "NOT_FOUND"


def test_summary_for_replan_limit_message():
    # R3: "가장 짧은 영화가 N분이라 X분 범위에는 맞는 영화가 없다"
    j = evaluate([mv("A", 132), mv("B", 141), mv("E", None)],
                 {"start_after": "15:00", "end_before": "16:00"}, BUF)
    assert j[FIT] == []
    assert j["window_min"] == 60
    assert j["shortest_need"] == 152
