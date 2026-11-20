"""노드 5개: analyze → plan → execute → judge → respond. (M4, F3005~F3008, F3011)

LangGraph의 약속
  - 노드 = 함수 하나. State(dict)를 받아서 '바꿀 부분만' dict로 돌려준다.
    LangGraph가 그 dict를 State에 덮어쓴다. 돌려주지 않은 키는 그대로 남는다.
  - 노드끼리는 직접 부르지 않는다. 오직 State를 통해서만 값을 주고받는다.

모든 노드는 emit(event, **payload)를 받는다. 이벤트를 '보내기만' 하고 누가 듣는지는 모른다 (F3012).

누가 하는가
  analyze  LLM  : 의도·조건·Tool 후보 추출 → 그 뒤 코드가 값 검사와 되묻기 판정
  plan     코드 : LLM이 고른 Tool을 걸러 내고, 의존 순서로 줄 세우고, 인자를 채운다
  execute  코드 : 계획대로 Tool 호출. 앞 Tool 출력(movie_cd)을 다음 Tool 입력으로. 실패해도 계속
  judge    코드 : judge.evaluate로 시간 판단
  respond  LLM  : judge 결과만 근거로 문장 작성 (실패하면 코드가 만든 문장으로 대신)
"""
import json
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, TypedDict

from app import config
from app.agent import judge as judge_mod
from app.agent import memory, replan
from app.agent.llm import analyze_with_llm, write_with_llm
from app.tools.registry import TOOLS, catalog

Emit = Callable[..., None]
WEEKDAYS = "월화수목금토일"


class State(TypedDict, total=False):
    session_id: str
    run_id: str
    user: str                  # 이번 사용자 메시지
    memory: dict               # 이전 조건, 추천 요약, 최근 3턴 (M6, run_turn이 넣어 준다)
    intent: str                # plan | fact | chat
    intent_reason: str
    conditions: dict           # watch_date, start_after, end_before, region, genre
    llm_tools: list[str]       # LLM이 고른 Tool (그대로 믿지 않는다)
    ask: str | None            # 되묻기 문구. 있으면 Tool 없이 바로 respond
    options: dict              # 계획 옵션: top_n, use_tmdb (M5 재계획이 바꾼다)
    plan: list[dict]           # [{tool, args}]
    cache: dict                # "tool|args" → 결과 (M5에서 재사용)
    results: dict              # tool 이름 → 결과 (상세는 리스트)
    candidates: list[dict]     # judge에 넣을 영화 목록 (judge.py의 movie 형태)
    judged: dict               # judge.evaluate 결과
    next: str                  # judge가 정한 다음 노드: "plan"(재계획) | "respond"
    replan_count: int
    replan_history: list[dict]
    answer: str
    recommendations: list[dict]
    theaters: list[dict]
    notices: list[str]         # 화면에 띄울 안내 (Tool 실패, 키 누락 등. 오류 상세는 넣지 않는다)


# Tool 실패 코드 → 사용자에게 보여 줄 쉬운 말 (NF3002: 오류 상세는 노출하지 않는다)
FRIENDLY = {
    "INVALID_KEY": "API 키가 없거나 올바르지 않음",
    "UPSTREAM_ERROR": "외부 서비스 응답 지연 또는 오류",
    "NO_DATA": "조회 결과 없음",
    "BAD_ARGUMENT": "요청 값 오류",
    "NOT_FOUND": "대상을 찾지 못함",
}
REPLAN_KO = {"expand_top_n": "후보를 10편으로 넓히기", "add_tmdb": "TMDB 신작 보충", "relax_genre": "장르 조건 풀기"}
TOOL_LABEL = {
    "get_box_office": "박스오피스",
    "get_movie_detail": "영화 상세",
    "search_new_releases": "신작(TMDB)",
    "search_theaters": "영화관",
}


def _valid_time(t: Any) -> str | None:
    """LLM이 준 시각이 HH:MM으로 읽히면 'HH:MM'으로, 아니면 None. LLM 출력은 검사하고 쓴다."""
    try:
        return judge_mod.to_hhmm(judge_mod.to_min(t)) if t else None
    except (ValueError, AttributeError):
        return None


def _valid_date(d: Any) -> str | None:
    try:
        return date.fromisoformat(d).isoformat() if d else None
    except (ValueError, TypeError):
        return None

def _week_table(today: date) -> str:
    """'오늘 / 내일 / 이번 주 토요일' 해석을 돕는 날짜표. 예: '11-20(금,오늘) 11-21(토) …'"""
    out = []
    for i in range(7):
        d = today + timedelta(days=i)
        tag = ",오늘" if i == 0 else ",내일" if i == 1 else ""
        out.append(f"{d.isoformat()}({WEEKDAYS[d.weekday()]}{tag})")
    return " ".join(out)


def _fix_afternoon(cond: dict) -> list[str]:
    """끝 시각이 시작 시각보다 이른데 오전 시각이면(예: 14:00 이후 ~ 07:00 전) 끝 시각을 오후로 본다.

    M10 검증에서 LLM이 같은 문장의 '7시 전'을 19:00으로도, 07:00으로도 읽는 것을 확인했다.
    '2시 이후 7시 전'을 '오후 2시 ~ 다음 날 오전 7시'로 읽을 사람은 없으므로 규칙으로 고친다(코드의 판단).
    고친 사실은 llm.decision 이벤트의 code_fixes로 남긴다.
    """
    s, e = cond.get("start_after"), cond.get("end_before")
    if not (s and e):
        return []
    sm, em = judge_mod.to_min(s), judge_mod.to_min(e)
    if em <= sm and em < 12 * 60 and em + 12 * 60 > sm:
        cond["end_before"] = judge_mod.to_hhmm(em + 12 * 60)
        return [f"end_before {e} → {cond['end_before']} (시작 {s}보다 이른 오전 시각이라 오후로 해석)"]
    return []


# ══ ① analyze (LLM) ═══════════════════════════════════
def analyze(state: State, emit: Emit) -> dict:
    today = date.today()
    mem = state.get("memory") or {}
    prev = mem.get("conditions") or {}
    prompt = (
        "너는 영화 관람 계획 도우미의 '요청 분석' 담당이다. 사용자 말에서 의도와 조건을 뽑아 양식을 채운다.\n"
        f"- 오늘: {today.isoformat()} ({WEEKDAYS[today.weekday()]}요일)\n"
        # M10: '이번 주 토요일'을 LLM이 날짜로 못 바꾼 사례가 있어, 앞으로 7일의 날짜를 코드가 계산해 준다
        f"- 날짜 참고(오늘부터 7일): {_week_table(today)}\n"
        "- 사용자가 '이번 메시지에서' 말하지 않은 값은 반드시 null로 둔다. 추측해서 채우지 않는다.\n"
        # M10: 예시가 '3시 → 15:00' 하나였을 때 '2시 이후'를 15:00으로 뽑은 사례가 있어 예시를 여러 개로 바꿨다
        "- 시각은 24시간 HH:MM. 영화 관람 맥락에서 1~11시는 보통 오후다. 숫자는 그대로 두고 12만 더한다"
        "(예: '2시 이후' → 14:00, '4시 45분 전' → 16:45, '7시 전' → 19:00). '이후'·'전'이 붙어도 시각 자체를 바꾸지 않는다.\n"
        "- tools에는 아래 Tool 목록에 있는 이름만 쓴다.\n"
        # M6: 이전 맥락. '같은 날', '거기서'를 해석하려면 LLM도 이전 조건을 알아야 한다.
        #     단, 이전 값을 다시 채우는 일(병합)은 LLM이 아니라 아래 코드가 한다.
        "- 이전 조건이 있고 사용자가 '같은 조건으로', '거기서'처럼 이어 말하면 intent는 이전과 같게 보고, "
        "바뀐 값만 채운다(이전 값을 다시 적지 않는다).\n"
        f"이전 조건: {json.dumps(prev, ensure_ascii=False) if prev else '없음'}\n"
        f"최근 대화: {json.dumps(mem.get('turns', [])[-2:], ensure_ascii=False)[:800]}\n"
        f"Tool 목록: {json.dumps(catalog(), ensure_ascii=False)}\n"
        f"사용자: {state['user']}"
    )
    try:
        a = analyze_with_llm(prompt)
    except Exception as e:  # LLM 키 누락, 네트워크 오류 → 서비스는 멈추지 않는다 (NF3003)
        emit("llm.error", step="analyze", error=type(e).__name__)
        return {"intent": "chat", "intent_reason": "LLM 호출 실패", "conditions": {},
                "ask": "지금은 요청을 분석할 수 없어요(LLM 연결 실패). 잠시 후 다시 시도해 주세요.",
                "notices": ["LLM(GMS) 연결에 실패했습니다. GMS_API_KEY를 확인하세요."]}

    # LLM 출력은 그대로 믿지 않는다: 형식이 틀린 값은 None으로 (빈 값은 채우지 않는다, F3005)
    conditions = {
        "watch_date": _valid_date(a.watch_date),
        "start_after": _valid_time(a.start_after),
        "end_before": _valid_time(a.end_before),
        "region": a.region or None,
        "genre": a.genre or None,
        "movie_query": a.movie_query or None,
    }
    fixes = _fix_afternoon(conditions)
    emit("llm.decision", step="analyze", intent=a.intent, reason=a.reason,
         conditions=conditions, tools=a.tools, **({"code_fixes": fixes} if fixes else {}))

    # M6: 이전 조건과 병합 (코드). 이번에 말한 값만 덮어쓴다. 잡담(chat)이면 조건을 건드리지 않는다.
    if a.intent in ("plan", "fact") and prev:
        merged = {**memory.merge_conditions(prev, conditions), "movie_query": conditions["movie_query"]}
        changed = [k for k in memory.COND_KEYS if conditions.get(k) is not None and conditions[k] != prev.get(k)]
        emit("memory.merge", prev=prev, new=conditions, merged=merged, changed=changed)
        conditions = merged

    # 되묻기 규칙 (코드가 판정): Tool을 부르기 전에 막는다 (명세 2장)
    ask = None
    if a.intent == "plan":
        s, e = conditions["start_after"], conditions["end_before"]
        if e and not s:
            ask = f"{e} 전에 끝나야 한다고 하셨는데, 몇 시부터 볼 수 있으세요?"
        elif s and e and judge_mod.to_min(s) >= judge_mod.to_min(e):
            ask = f"시작 가능 시각({s})이 끝나야 하는 시각({e})보다 늦어요. 시각을 다시 알려 주세요."
    if ask:
        emit("agent.ask", reason="조건 부족", question=ask)

    return {"intent": a.intent, "intent_reason": a.reason, "conditions": conditions,
            "llm_tools": a.tools, "ask": ask}


# ══ ② plan (코드) ════════════════════════════════════
def plan(state: State, emit: Emit) -> dict:
    cond = state["conditions"]
    opts = {"top_n": config.BOX_TOP_N, "use_tmdb": None, **state.get("options", {})}
    chosen = [t for t in state.get("llm_tools", []) if t in TOOLS]       # 없는 이름은 버린다
    dropped = [t for t in state.get("llm_tools", []) if t not in TOOLS]

    watch = cond.get("watch_date") or date.today().isoformat()
    future = date.fromisoformat(watch) >= date.today()  # 박스오피스(어제 기준)에 없는 날
    if opts["use_tmdb"] is None:                        # 처음 계획: LLM이 골랐고 미래 날짜일 때만
        opts["use_tmdb"] = future and "search_new_releases" in chosen

    # 의존 순서를 코드가 정한다. 후보(박스오피스) → 상세(박스오피스의 movie_cd 필요) → 신작 → 영화관
    steps = [
        {"tool": "get_box_office", "args": {"target_date": watch, "top_n": opts["top_n"]}},
        {"tool": "get_movie_detail", "args": {"movie_cd": "<get_box_office.movies[].movie_cd>"}},
    ]
    if opts["use_tmdb"]:
        steps.append({"tool": "search_new_releases",
                      "args": {"from_date": "<get_box_office.basis_date + 1일>", "to_date": watch}})
    if cond.get("region"):                              # 지역을 말했을 때만 (LLM이 골랐든 안 골랐든)
        steps.append({"tool": "search_theaters", "args": {"region": cond["region"]}})

    emit("plan.created", steps=[s["tool"] for s in steps], dropped=dropped,
         options=opts, replan_count=state.get("replan_count", 0))
    return {"plan": steps, "options": opts}


# ══ ③ execute (코드) ═════════════════════════════════
def execute(state: State, emit: Emit) -> dict:
    cache = dict(state.get("cache", {}))
    results: dict[str, Any] = {}

    def call(name: str, **args: Any) -> dict:
        """Tool 한 번 호출. 같은 Tool+같은 인자는 cache에서 꺼낸다 (F3009)."""
        key = f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if key in cache:
            emit("tool.cache_hit", tool=name, args=args)
            return cache[key]
        emit("tool.call", tool=name, args=args)
        t0 = time.perf_counter()
        out = TOOLS[name].run(**args)
        emit("tool.result", tool=name, ok=out["ok"],
             code=None if out["ok"] else out["error"]["code"],
             elapsed_ms=round((time.perf_counter() - t0) * 1000))
        cache[key] = out
        return out

    candidates: list[dict] = []
    for step in state["plan"]:
        name, args = step["tool"], step["args"]

        if name == "get_box_office":
            results[name] = call(name, **args)

        elif name == "get_movie_detail":
            # 앞 Tool의 출력(movie_cd)을 이 Tool의 입력으로 (F3007). 영화마다 반복.
            box = results.get("get_box_office", {})
            if not box.get("ok"):
                continue  # 후보가 없으니 상세도 없다. 실패 사유는 박스오피스 결과에 남아 있다.
            details = []
            for m in box["data"]["movies"]:
                d = call(name, movie_cd=m["movie_cd"])
                details.append(d)
                candidates.append({
                    "movie_nm": d["data"]["movie_nm"] if d["ok"] else m["movie_nm"],
                    "show_tm": d["data"]["show_tm"] if d["ok"] else None,
                    "genres": d["data"]["genres"] if d["ok"] else [],
                    "watch_grade": d["data"].get("watch_grade") if d["ok"] else None,
                    "rank": m.get("rank"),
                    "source": "KOBIS",
                    "error": None if d["ok"] else d["error"],  # 실패해도 사유를 남기고 계속 (F3007)
                })
            results[name] = details

        elif name == "search_new_releases":
            box = results.get("get_box_office", {})
            if box.get("ok"):
                start = date.fromisoformat(box["data"]["basis_date"]) + timedelta(days=1)
            else:
                start = date.today()
            results[name] = call(name, from_date=start.isoformat(), to_date=args["to_date"])
            if results[name]["ok"]:
                for m in results[name]["data"]["movies"]:
                    candidates.append({"movie_nm": m["movie_nm"], "show_tm": m["show_tm"], "genres": [],
                                       "source": "TMDB", "release_date": m["release_date"], "error": None})

        else:  # search_theaters 등 인자가 이미 다 채워진 Tool
            results[name] = call(name, **args)

    # 같은 영화 합치기: 공백을 지운 제목이 같으면 하나로. 먼저 들어온 KOBIS를 남긴다 (기획서 3.1)
    seen, merged = set(), []
    for c in candidates:
        k = (c["movie_nm"] or "").replace(" ", "")
        if k and k in seen:
            continue
        seen.add(k)
        merged.append(c)

    return {"results": results, "candidates": merged, "cache": cache}


# ══ ④ judge (코드) ═══════════════════════════════════
def judge(state: State, emit: Emit) -> dict:
    cond = dict(state["conditions"])
    if state.get("options", {}).get("relax_genre"):
        cond["genre"] = None  # 재계획 3: 선호 조건 완화. 제약 조건(시각)은 절대 풀지 않는다
    judged = judge_mod.evaluate(state.get("candidates", []), cond)
    if cond.get("genre") is None and state["conditions"].get("genre"):
        judged["notes"].append(f"'{state['conditions']['genre']}' 장르 조건을 풀고 다시 찾았다.")
    emit("judge.result", fit=[m["movie_nm"] for m in judged["fit"]],
         unfit=len(judged["unfit"]), excluded=len(judged["excluded"]),
         window_min=judged["window_min"], shortest_need=judged["shortest_need"])

    # ── 재계획 결정 (M5). 분기 함수는 State를 바꿀 수 없으므로 '결정과 기록'은 노드에서 한다 ──
    count = state.get("replan_count", 0)
    history = list(state.get("replan_history", []))
    stop = replan.stop_reason({**state, "judged": judged})
    strategy = None if stop else replan.next_strategy(state)

    if strategy:
        name, patch = strategy
        history.append({"count": count + 1, "reason": "맞는 영화 0편", "strategy": name})
        emit("replan", count=count + 1, strategy=name, reason="맞는 영화 0편")
        return {"judged": judged, "next": "plan", "replan_count": count + 1,
                "replan_history": history, "options": {**state.get("options", {}), **patch}}

    # 멈춘다. 맞는 영화가 없이 멈추면 조건 조정을 되묻기 위한 근거 숫자를 남긴다 (F3009)
    if not judged["fit"]:
        why = stop or "더 시도할 재계획 전략 없음"
        emit("replan.stop", reason=why, replan_count=count)
        if judged["shortest_need"] is not None and judged["window_min"] is not None:
            judged["notes"].append(
                f"{why}. 가장 짧은 영화도 광고 포함 {judged['shortest_need']}분이 필요한데, "
                f"가능한 시간은 {judged['window_min']}분이다. 시작을 앞당기거나 끝 시각을 늦춰 달라고 되묻는다.")
    return {"judged": judged, "next": "respond"}


# ══ ⑤ respond (LLM) ══════════════════════════════════
def _summarize(state: State) -> dict:
    """respond에 넘길 근거를 코드가 정리한다. Tool 원본이 아니라 판단 결과와 안내만."""
    results = state.get("results", {})
    notices = list(state.get("notices", []))
    for name, r in results.items():
        if isinstance(r, dict) and not r["ok"]:
            notices.append(f"{TOOL_LABEL.get(name, name)} 정보를 가져오지 못했습니다"
                           f"({FRIENDLY.get(r['error']['code'], '알 수 없는 오류')}).")
    box = results.get("get_box_office", {})
    theaters = results.get("search_theaters", {}).get("data", {}).get("theaters", []) \
        if results.get("search_theaters", {}).get("ok") else []
    judged = state.get("judged", {})
    recs = [{**m, "fit": True} for m in judged.get("fit", [])] + \
           [{**m, "fit": False} for m in judged.get("unfit", [])]
    return {
        "basis": {"basis_date": box["data"]["basis_date"], "is_estimated": box["data"]["is_estimated"]}
                 if box.get("ok") else None,
        "theaters": theaters,
        "notices": notices,
        "recommendations": recs,
    }


def _fallback_answer(state: State, s: dict) -> str:
    """LLM이 없어도 judge 결과만으로 답을 만든다 (NF3003)."""
    j = state.get("judged", {})
    lines = []
    for m in j.get("fit", []):
        when = f"늦어도 {m['latest_start']}에 시작" if m.get("latest_start") else f"예상 종료 {m.get('expected_end') or '-'}"
        lines.append(f"- {m['movie_nm']} ({m['show_tm']}분, 필요 {m['need_min']}분): {when}")
    if not lines:
        lines.append("조건에 맞는 영화를 찾지 못했습니다.")
    for m in j.get("excluded", []):
        lines.append(f"- 제외: {m['movie_nm']} — {m['reason']}")
    lines += [f"※ {n}" for n in j.get("notes", []) + s["notices"]]
    return "\n".join(lines)


COND_KO = {"watch_date": "관람일", "start_after": "이후 시작", "end_before": "전에 끝", "region": "지역",
           "genre": "장르", "movie_query": "찾는 영화"}
_KEY_WORDS = [("latest_start", "늦어도 시작할 시각"), ("expected_end", "끝나는 시각"), ("basis_date", "집계 기준일"),
              ("start_after", "시작 가능 시각"), ("end_before", "끝나야 하는 시각"), ("need_min", "필요 시간")]


def _no_keys(text: str) -> str:
    """LLM이 그래도 영어 키 이름을 쓰면 코드가 마지막에 바꾼다 (프롬프트는 부탁, 코드는 보장)."""
    for k, v in _KEY_WORDS:
        text = text.replace(f"**{k}**", v).replace(k, v)
    # 프롬프트의 구조(지시 문구)가 답변에 새어 나온 줄은 지운다
    return "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith(("반드시 넣을 문장", "판단 결과:", "규칙:")))


def respond(state: State, emit: Emit) -> dict:
    # 되묻기: LLM 없이 코드가 정한 문구를 그대로 (Tool을 부르지 않았다)
    if state.get("ask"):
        return {"answer": state["ask"], "recommendations": [], "theaters": [],
                "notices": state.get("notices", [])}

    if state.get("intent") == "chat":
        try:
            answer = write_with_llm(
                "너는 영화 관람 계획 도우미다. 짧고 친절하게 답한다. 영화 정보(순위·러닝타임·상영 시각)는 "
                f"지어내지 말고, 필요하면 시간과 지역을 말해 달라고 안내한다.\n사용자: {state['user']}")
        except Exception as e:
            emit("llm.error", step="respond", error=type(e).__name__)
            answer = "안녕하세요! 관람 날짜, 시간, 지역을 알려 주시면 볼 영화를 찾아 드릴게요."
        return {"answer": answer, "recommendations": [], "theaters": [], "notices": state.get("notices", [])}

    s = _summarize(state)
    judged = state.get("judged", {})
    # LLM에 넘기는 근거는 한국어 키로 바꾼다. 영어 키(latest_start …)를 주면 답변에 그대로 따라 쓴다 (M10에서 확인)
    def ko(m: dict) -> dict:
        row = {"영화": m.get("movie_nm"), "러닝타임(분)": m.get("show_tm"), "광고 포함 필요 시간(분)": m.get("need_min"),
               "늦어도 시작할 시각": m.get("latest_start"), "끝나는 시각": m.get("expected_end"), "이유": m.get("reason")}
        return {k: v for k, v in row.items() if v is not None}
    evidence = {
        "조건": {COND_KO.get(k, k): v for k, v in state["conditions"].items() if v is not None},
        "맞는 영화": [ko(m) for m in judged.get("fit", [])],
        "맞지 않는 영화": [ko(m) for m in judged.get("unfit", [])],
        "판단에서 뺀 영화": [ko(m) for m in judged.get("excluded", [])],
        "가능한 시간(분)": judged.get("window_min"),
        "가장 짧은 필요 시간(분)": judged.get("shortest_need"),
        "참고": judged.get("notes", []),
        "영화관": [t.get("name") for t in s["theaters"]],
        "안내": s["notices"],
    }
    # 말해야 할 문장은 코드가 먼저 만든다 (M10 검증에서 LLM이 키 이름을 그대로 쓰고, 날짜를 빼먹는 것을 확인함)
    must = []
    b = s["basis"]
    if b and b["is_estimated"]:
        d = date.fromisoformat(b["basis_date"])
        must.append(f"박스오피스 순위는 {d.month}월 {d.day}일 집계 기준이라 관람일의 순위와 다를 수 있습니다(추정).")
    if state.get("replan_history"):
        must.append("처음 조건으로 맞는 영화가 없어 " +
                    " → ".join(REPLAN_KO.get(h["strategy"], h["strategy"]) for h in state["replan_history"]) +
                    " 순서로 다시 찾았습니다.")
    evidence = {k: v for k, v in evidence.items() if v not in (None, [], {})}  # 빈 값은 넘기지 않는다("null분" 방지)
    # 마지막 문단의 방향은 규칙으로 정해진다 → 코드가 판단해 LLM에게 지시한다
    box = state.get("results", {}).get("get_box_office", {})
    if box and not box.get("ok"):
        # 후보 목록 자체를 못 받았다. 조건을 바꿔도 해결되지 않으므로 조정 질문을 하면 사용자를 헷갈리게 한다 (M10 R8에서 확인)
        ending = ("7. 박스오피스 조회에 실패해 영화를 판단하지 못했다. 맞는 영화가 없다고 단정하지 말고, "
                  "조건을 바꾸라고 하지도 말고, 잠시 후 다시 시도해 달라고 안내한다.\n")
    elif not judged.get("fit"):
        ending = ("7. 맞는 영화가 없다. '가장 짧은 필요 시간'과 '가능한 시간' 숫자를 근거로 대고, "
                  "마지막 문장에서 '시작을 앞당기거나 끝 시각을 늦출 수 있는지' 질문한다.\n")
    else:
        ending = "7. 맞는 영화가 있으므로 조건을 바꾸라는 질문은 하지 않는다.\n"
    prompt = (
        "너는 영화 관람 계획 도우미다. 아래 '판단 결과'만 근거로 한국어로 답한다.\n"
        "규칙:\n"
        "1. 숫자와 시각은 판단 결과에 있는 값만 그대로 쓴다. 계산하거나 바꾸지 않는다.\n"
        "2. 상영 시각을 지어내지 않는다. 맞는 영화마다 '늦어도 HH:MM에는 시작하는 회차를 고르세요'처럼 안내한다.\n"
        "3. 영화와 영화관을 묶지 않는다('○○관에서 △△ 상영', '가능한 영화(지역)' 같은 제목 금지). 영화관은 따로 안내한다.\n"
        "4. 맞지 않는 영화(unfit)는 reason의 숫자로, 제외 영화(excluded)는 reason으로 이유를 짧게 밝힌다.\n"
        f"5. 다음 문장들은 뜻을 바꾸지 말고 본문 안에 자연스럽게 넣는다. 따로 제목이나 목록 머리말을 달지 않는다: "
        f"{json.dumps(must, ensure_ascii=False)}\n"
        "6. '안내'가 있으면 마지막 부분에 쉬운 말로 알린다.\n"
        + ending
        + "8. 영어 키 이름(latest_start, basis_date, fit, unfit 등)을 답변에 쓰지 않는다. 러닝타임에는 '분'을 붙인다.\n"
        f"사용자 요청: {state['user']}\n"
        f"판단 결과: {json.dumps(evidence, ensure_ascii=False)}"
    )
    try:
        answer = _no_keys(write_with_llm(prompt))
        # 0편이면 조건 조정을 되묻는 것이 명세(F3009). LLM이 빠뜨려도 코드가 보장한다 (M10 R3b에서 빠뜨린 사례 확인)
        if box.get("ok") and not judged.get("fit") and not any(w in answer for w in ("앞당기", "늦출", "늦추")):
            answer += "\n\n시작 시각을 앞당기거나 끝나야 하는 시각을 늦출 수 있을까요? 조건을 알려 주시면 다시 찾아 드릴게요."
    except Exception as e:
        emit("llm.error", step="respond", error=type(e).__name__)
        answer = _fallback_answer(state, s)
    return {"answer": answer, "recommendations": s["recommendations"],
            "theaters": s["theaters"], "notices": s["notices"]}
