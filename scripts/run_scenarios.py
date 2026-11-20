"""대표 요청 R1~R8 검증 (M10, 명세 4장, 체크리스트 23·24).

진짜 LLM·외부 API를 부른다. 요청마다 기대 동작을 코드로 점검하고, 결과를 파일로 남긴다.
  docs/산출물/5_검증결과/결과.md          : 요청별 최종 응답 · 단계 · Tool 호출 · 재계획 · 처리 시간 · 기대 점검
  docs/산출물/5_검증결과/logs/R*.json     : 요청별 전체 이벤트 (실행 로그 샘플. R1 = 대표 1건, R2·R3 = 재계획 사례)

실행 (루트에서):  .venv\\Scripts\\python scripts\\run_scenarios.py
  데이터가 날짜에 따라 바뀌므로(박스오피스 순위·러닝타임) R2·R3의 시각은 실행일에 맞춰 조정이 필요할 수 있다.
"""
import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config                     # noqa: E402
from app.agent.graph import run_turn       # noqa: E402
from app.events import logger_agent        # noqa: E402
from app.events.bus import bus             # noqa: E402

OUT = ROOT / "docs" / "산출물" / "5_검증결과"
(OUT / "logs").mkdir(parents=True, exist_ok=True)


def tools_called(evs):  return [e["payload"]["tool"] for e in evs if e["event"] == "tool.call"]
def cache_hits(evs):    return [e["payload"]["tool"] for e in evs if e["event"] == "tool.cache_hit"]
def ev(evs, name):      return [e for e in evs if e["event"] == name]
def fits(out):          return [r for r in out.get("recommendations", []) if r.get("fit")]


# (id, 유형, 요청, 기대(기획서 2.5), 세션 키, 사전 조작, [(점검 이름, 함수(out, evs) -> bool)])
SCENARIOS = [
    ("R1", "한 번에 충족", "내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어",
     "박스오피스 → 상세 → 영화관. 맞는 영화와 늦어도 시작해야 하는 시각", "A", None,
     [("의도 plan", lambda o, e: o["intent"] == "plan"),
      ("조건 추출 2026-11-21 · 14:00 · 19:00 · 강남", lambda o, e: o["conditions"].get("start_after") == "14:00" and o["conditions"].get("end_before") == "19:00" and o["conditions"].get("region") == "강남"),
      ("맞는 영화 1편 이상", lambda o, e: len(fits(o)) >= 1),
      ("latest_start 제시", lambda o, e: all(r.get("latest_start") for r in fits(o))),
      ("영화관 안내", lambda o, e: len(o.get("theaters", [])) > 0),
      ("재계획 없음", lambda o, e: o.get("replan_count", 0) == 0),
      ("호출 순서 박스오피스 → 상세 → 영화관", lambda o, e: tools_called(e)[:1] == ["get_box_office"] and tools_called(e)[-1] == "search_theaters")]),
    ("R2", "재계획", "내일 3시 이후에 보고 4시 45분 전에 끝나야 해",
     "상위 5편 0편 → 재계획(10편 확장, 이미 조회한 상세 재사용) → 맞는 영화", "B", None,
     [("재계획 1회 이상", lambda o, e: o.get("replan_count", 0) >= 1),
      ("첫 전략 expand_top_n", lambda o, e: (o.get("replan_history") or [{}])[0].get("strategy") == "expand_top_n"),
      ("상세 cache 재사용", lambda o, e: cache_hits(e).count("get_movie_detail") >= 1),
      ("맞는 영화 1편 이상", lambda o, e: len(fits(o)) >= 1)]),
    ("R3", "상한 도달 / 전략 소진", "오늘 3시 이후에 보고 4시 전에 끝나야 해",
     "재계획 후 멈추고, 가장 짧은 영화의 필요 시간과 가능 시간을 근거로 조건 조정을 되묻는다", "C", None,
     [("재계획 1회 이상", lambda o, e: o.get("replan_count", 0) >= 1),
      ("replan.stop 발생", lambda o, e: len(ev(e, "replan.stop")) == 1),
      ("상한 3회 이하", lambda o, e: o.get("replan_count", 0) <= config.MAX_REPLAN),
      ("맞는 영화 0편", lambda o, e: len(fits(o)) == 0),
      ("답변에 조건 조정 요청", lambda o, e: "앞당기" in o["answer"] or "늦출" in o["answer"] or "늦추" in o["answer"])]),
    ("R3b", "상한 도달 (3회)", "오늘 3시 이후에 액션 영화 보고 4시 전에 끝나야 해",
     "장르 조건까지 있어 전략 3개(확장 → TMDB → 장르 완화)를 모두 쓰고 상한 3회에서 멈춘다", "C2", None,
     [("재계획 3회", lambda o, e: o.get("replan_count", 0) == config.MAX_REPLAN),
      ("전략 순서 확장 → TMDB → 장르 완화", lambda o, e: [h["strategy"] for h in o.get("replan_history", [])] == ["expand_top_n", "add_tmdb", "relax_genre"]),
      ("멈춘 사유 = 상한 도달", lambda o, e: "상한" in ev(e, "replan.stop")[0]["payload"]["reason"]),
      ("무한 반복 없음(plan 진입 4회)", lambda o, e: sum(1 for x in ev(e, "step.enter") if x["payload"]["step"] == "plan") == 4),
      ("답변에 조건 조정 요청", lambda o, e: "앞당기" in o["answer"] or "늦출" in o["answer"] or "늦추" in o["answer"])]),
    ("R4", "Tool 하나 실패", "내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어",
     "카카오 키를 비움 → 영화관만 INVALID_KEY. 영화 추천은 정상, 영화관 정보 못 가져왔다고 안내", "D", "kakao_off",
     [("영화관 Tool 실패 INVALID_KEY", lambda o, e: any(x["payload"]["tool"] == "search_theaters" and x["payload"]["code"] == "INVALID_KEY" for x in ev(e, "tool.result"))),
      ("맞는 영화는 정상", lambda o, e: len(fits(o)) >= 1),
      ("안내(notice) 있음", lambda o, e: len(o.get("notices", [])) >= 1),
      ("재계획하지 않음", lambda o, e: o.get("replan_count", 0) == 0),
      ("세션 정상 종료", lambda o, e: ev(e, "session.end")[0]["payload"].get("ok") is True)]),
    ("R4b", "후보 Tool 치명적 실패", "내일 강남에서 2시 이후에 영화 보고 7시 전에 끝내고 싶어",
     "KOBIS 키를 비움 → INVALID_KEY는 다시 해도 같으므로 재계획하지 않는다. 조건 조정이 아니라 다시 시도를 안내", "D2", "kobis_off",
     [("박스오피스 INVALID_KEY", lambda o, e: any(x["payload"]["tool"] == "get_box_office" and x["payload"]["code"] == "INVALID_KEY" for x in ev(e, "tool.result"))),
      ("재계획하지 않음", lambda o, e: o.get("replan_count", 0) == 0 and "치명적" in ev(e, "replan.stop")[0]["payload"]["reason"]),
      ("다른 Tool(영화관)은 계속 진행", lambda o, e: "search_theaters" in tools_called(e)),
      ("조건 조정 질문 안 함", lambda o, e: "앞당기" not in o["answer"] and "늦출" not in o["answer"]),
      ("답변에 null 없음", lambda o, e: "null" not in o["answer"]),
      ("화면용 안내 있음", lambda o, e: any("박스오피스" in n for n in o.get("notices", [])))]),
    ("R5", "Tool 불필요", "안녕! 뭘 할 수 있어?",
     "chat 의도, Tool 호출 없이 응답", "E", None,
     [("의도 chat", lambda o, e: o["intent"] == "chat"),
      ("Tool 호출 0건", lambda o, e: tools_called(e) == []),
      ("plan 단계 건너뜀", lambda o, e: not any(x["payload"].get("step") == "plan" for x in ev(e, "step.enter")))]),
    ("R6", "되묻기", "7시 전에 끝나는 영화 알려줘",
     "Tool 호출 없이 시작 가능 시각을 되묻는다", "F", None,
     [("agent.ask 발생", lambda o, e: len(ev(e, "agent.ask")) == 1),
      ("Tool 호출 0건", lambda o, e: tools_called(e) == []),
      ("start_after 비어 있음(지어내지 않음)", lambda o, e: not o.get("conditions", {}).get("start_after"))]),
    ("R7", "대화 맥락", "같은 조건으로 홍대에서",
     "R1 다음 턴. region만 바뀌고 날짜·시각 유지. 영화관만 다시 검색", "A", None,
     [("바뀐 조건 region만", lambda o, e: [x["payload"]["changed"] for x in ev(e, "memory.merge")] == [["region"]]),
      ("날짜·시각은 R1 값 그대로", lambda o, e: all(o["conditions"].get(k) == x["payload"]["prev"].get(k)
                                                for x in ev(e, "memory.merge") for k in ("watch_date", "start_after", "end_before"))),
      ("새 호출은 영화관만", lambda o, e: tools_called(e) == ["search_theaters"]),
      ("나머지는 cache 재사용", lambda o, e: len(cache_hits(e)) >= 2)]),
    ("R8", "미래 날짜 + 신작", "이번 주 토요일 2시 이후, 7시 전",
     "박스오피스는 어제 기준 추정이라고 밝힌다. 그 사이 개봉작은 TMDB로 보충(가능하면)", "G", None,
     [("관람일 = 이번 토요일", lambda o, e: o["conditions"].get("watch_date") == "2026-11-21"),
      ("박스오피스 추정 표시(basis_date < 관람일)", lambda o, e: any(x["event"] == "tool.call" and x["payload"]["tool"] == "get_box_office" for x in e)),
      ("답변에 추정·기준일 언급", lambda o, e: any(w in o["answer"] for w in ("추정", "기준", "집계")))]),
]


def main():
    logger_agent.start()          # 실제 app.db에 저장 → 로그 화면(logs.html)에서도 볼 수 있다
    tag = datetime.now().strftime("%m%d%H%M")
    sessions = {}
    rows = []
    for sid_key, (rid, kind, text, expect, skey, pre, checks) in zip(range(99), SCENARIOS):
        sid = sessions.setdefault(skey, f"verify-{tag}-{skey}")
        run_id = uuid.uuid4().hex[:12]
        evs = []
        collect = lambda m, r=run_id: evs.append(m) if m["run_id"] == r else None  # noqa: E731
        bus.subscribe(collect)
        saved = (config.KAKAO_REST_API_KEY, config.KOBIS_API_KEY)
        if pre == "kakao_off":
            config.KAKAO_REST_API_KEY = ""     # Tool은 호출할 때마다 config에서 키를 읽는다 → 이 실행에만 적용된다
        if pre == "kobis_off":
            config.KOBIS_API_KEY = ""
        t0 = time.perf_counter()
        try:
            out = run_turn(sid, text, run_id=run_id)
        finally:
            config.KAKAO_REST_API_KEY, config.KOBIS_API_KEY = saved
            bus.unsubscribe(collect)
        sec = round(time.perf_counter() - t0, 1)
        results = []
        # 공통 점검: 계획 답변의 품질 (M10 1차 실행에서 발견한 문제 3가지가 다시 생기지 않는지)
        if out.get("intent") == "plan" and not out.get("ask") and out.get("recommendations"):
            ans = out.get("answer", "")
            checks = checks + [
                ("답변에 프롬프트 문구 없음", lambda o, e: "반드시 넣을" not in ans and "판단 결과:" not in ans),
                ("답변에 영어 키 이름 없음", lambda o, e: not re.search(r"latest_start|basis_date|expected_end|\bunfit\b|\bfit\b", ans)),
                ("집계 기준일을 날짜로 밝힘", lambda o, e: bool(re.search(r"\d+월 \d+일 집계", ans))),
            ] + ([("맞는 영화가 있으면 조정 질문 안 함", lambda o, e: "앞당기" not in ans and "늦출" not in ans)] if fits(out) else [])
        for name, fn in checks:
            try:
                results.append((name, bool(fn(out, evs))))
            except Exception:
                results.append((name, False))
        steps = [e["payload"]["step"] for e in evs if e["event"] == "step.enter"]
        row = {"id": rid, "kind": kind, "request": text, "expect": expect, "session_id": sid, "run_id": run_id,
               "seconds": sec, "intent": out.get("intent"), "conditions": {k: v for k, v in (out.get("conditions") or {}).items() if v},
               "steps": steps, "tool_calls": tools_called(evs), "cache_hits": cache_hits(evs),
               "replan_count": out.get("replan_count", 0), "replan_history": out.get("replan_history", []),
               "fit": [f"{r['movie_nm']} ({r['show_tm']}분, 늦어도 {r.get('latest_start') or '-'})" for r in fits(out)],
               "unfit": len([r for r in out.get("recommendations", []) if not r.get("fit")]),
               "excluded": [(x.get("movie_nm"), x.get("code")) for x in (out.get("judged") or {}).get("excluded", [])],
               "theaters": len(out.get("theaters", [])), "notices": out.get("notices", []),
               "answer": out.get("answer", ""), "checks": results}
        rows.append(row)
        (OUT / "logs" / f"{rid}.json").write_text(json.dumps(
            {"scenario": {k: row[k] for k in ("id", "kind", "request", "session_id", "run_id", "seconds")}, "events": evs},
            ensure_ascii=False, indent=1), encoding="utf-8")
        ok = sum(c for _, c in results)
        print(f"{rid} {kind:<14} {ok}/{len(results)}  {sec:>5}s  tools={len(row['tool_calls'])} cache={len(row['cache_hits'])} replan={row['replan_count']}")
        for name, c in results:
            if not c:
                print(f"     ✗ {name}")
    logger_agent.stop()
    (OUT / "결과.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(rows)


def write_md(rows):
    L = ["# 대표 요청 검증 결과 (자동 기록)", "",
         f"- 실행: {datetime.now().isoformat(timespec='minutes')} · `scripts/run_scenarios.py` · 실제 GMS LLM과 KOBIS·TMDB·카카오 API 호출",
         f"- 설정: AD_BUFFER={config.AD_BUFFER}분, MAX_REPLAN={config.MAX_REPLAN}, BOX_TOP_N={config.BOX_TOP_N}",
         "- 전체 이벤트는 `docs/산출물/5_검증결과/logs/R*.json`에 있다. 같은 내용이 SQLite `event_logs`에도 저장되어 logs.html에서 볼 수 있다.", "",
         "## 요약", "", "| 번호 | 유형 | 점검 | 처리 시간 | Tool 호출 | cache 재사용 | 재계획 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        ok = sum(c for _, c in r["checks"])
        L.append(f"| {r['id']} | {r['kind']} | {ok}/{len(r['checks'])} | {r['seconds']}초 | {len(r['tool_calls'])} | {len(r['cache_hits'])} | {r['replan_count']} |")
    for r in rows:
        L += ["", f"## {r['id']} · {r['kind']}", "",
              f"- **요청**: \"{r['request']}\"" + (" (R1과 같은 세션의 다음 턴)" if r["id"] == "R7" else "") + (" (카카오 키를 비우고 실행)" if r["id"] == "R4" else ""),
              f"- **기대**: {r['expect']}",
              f"- **추출 조건**: `{json.dumps(r['conditions'], ensure_ascii=False)}` · 의도 `{r['intent']}`",
              f"- **단계**: {' → '.join(r['steps'])}",
              f"- **Tool 호출**: {', '.join(r['tool_calls']) or '없음'}",
              f"- **cache 재사용**: {', '.join(r['cache_hits']) or '없음'}",
              f"- **재계획**: {r['replan_count']}회 " + (" → ".join(h['strategy'] for h in r['replan_history']) if r['replan_history'] else ""),
              f"- **맞는 영화**: {'; '.join(r['fit']) or '없음'} · 안 맞음 {r['unfit']}편 · 제외 {r['excluded'] or '없음'}",
              f"- **영화관**: {r['theaters']}곳 · **안내**: {r['notices'] or '없음'}",
              f"- **처리 시간**: {r['seconds']}초 · 로그: `docs/산출물/5_검증결과/logs/{r['id']}.json` (session `{r['session_id']}`)",
              "- **기대 점검**: " + " · ".join(f"{'✅' if c else '❌'} {n}" for n, c in r["checks"]),
              "", "<details><summary>최종 응답</summary>", "", r["answer"].replace("\n", "  \n"), "", "</details>"]
    (OUT / "결과.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
