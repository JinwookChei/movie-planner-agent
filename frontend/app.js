// 공통 fetch 헬퍼와 화면 부품 (M9). 세 화면(index, plans, logs)이 함께 쓴다.
//
// 지키는 약속
//   - 서버에서 받은 문자열은 textContent로만 넣는다 (innerHTML에 그대로 넣으면 XSS). el()이 그 역할을 한다.
//   - 오류 상세(예외 메시지, 키)는 화면에 내지 않는다. 실패 코드는 쉬운 말로 바꿔 보여 준다 (NF3002).
"use strict";

// 백엔드 주소. 프론트(5500)와 같은 호스트의 8000 포트 → CORS 허용 목록(localhost / 127.0.0.1:5500)과 맞는다
const API = `${location.protocol === "https:" ? "https" : "http"}://${location.hostname || "127.0.0.1"}:8000`;

/** fetch 한 번. 네트워크 오류도 예외 대신 {ok:false, status:0}으로 돌려준다 (Tool의 ok/fail 약속과 같은 생각). */
async function api(path, { method = "GET", body } = {}) {
  try {
    const res = await fetch(API + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null };
  }
}

/** 422 같은 실패를 사람이 읽을 문장으로. 서버 detail 원문은 보여 주지 않는다. */
function friendlyError(r) {
  if (r.status === 0) return "백엔드 서버에 연결할 수 없습니다. uvicorn이 8000 포트에서 실행 중인지 확인하세요.";
  if (r.status === 404) return "대상을 찾을 수 없습니다. 이미 삭제되었을 수 있습니다.";
  if (r.status === 422) {
    const fields = (r.data?.detail || []).map(d => (d.loc || []).slice(-1)[0]).filter(Boolean);
    const NAMES = { start_time: "시작 시각", end_time: "끝 시각", watch_date: "날짜", movie_nm: "영화 이름", message: "메시지" };
    if (fields.length) return `입력을 확인하세요: ${[...new Set(fields)].map(f => NAMES[f] || f).join(", ")} (시각은 HH:MM, 끝이 시작보다 늦어야 합니다)`;
    return "입력 형식을 확인하세요. 시각은 HH:MM이고, 끝 시각이 시작 시각보다 늦어야 합니다.";
  }
  return "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

/** 안전한 DOM 만들기. 문자열 자식은 모두 텍스트 노드가 된다. */
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) n.append(c instanceof Node ? c : String(c));
  return n;
}

/** LLM 답변의 **굵게**만 살린다. 먼저 텍스트로 만든 뒤 나누므로 HTML이 끼어들 수 없다. */
function richText(s) {
  const frag = document.createDocumentFragment();
  String(s || "").split(/(\*\*[^*]+\*\*)/g).forEach(part => {
    if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(el("b", {}, part.slice(2, -2)));
    else frag.append(part.replace(/^#{1,6}\s*/gm, ""));
  });
  return frag;
}

function toast(msg) {
  const t = el("div", { class: "toast", role: "status" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 2600);
}

const toMin = hhmm => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const toHHMM = m => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const todayISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const WEEK = ["일", "월", "화", "수", "목", "금", "토"];
const fmtDate = iso => { const d = new Date(iso + "T00:00:00"); return `${d.getMonth() + 1}/${d.getDate()} (${WEEK[d.getDay()]})`; };

// ── 상단 바와 배너 ───────────────────────────────────
function renderTop(current) {
  const top = el("header", { class: "top" },
    el("span", { class: "brand" }, el("i"), "MoviePlanner"),
    el("nav", { class: "nav", "aria-label": "화면" },
      [["index.html", "대화"], ["plans.html", "계획 관리"], ["logs.html", "실행 로그"]]
        .map(([href, label]) => el("a", { href, "aria-current": href === current ? "page" : null }, label))),
    el("span", { class: "grow" }),
    el("span", { class: "sid", id: "sidLabel" }));
  const banners = el("div", { class: "banners", id: "banners", "aria-live": "polite" });
  document.body.prepend(top, banners);
  // 데이터 출처 표기 (NF3007). TMDB는 이용 약관상 아래 문구를 앱 안에 표시해야 한다
  document.body.append(el("footer", { class: "credits" },
    "데이터 출처: 영화진흥위원회 KOBIS 오픈API · TMDB · 카카오 로컬. ",
    "This product uses the TMDB API but is not endorsed or certified by TMDB."));
  checkHealth();
}

const KEY_IMPACT = {
  gms: "LLM(GMS) 키가 없어 요청을 분석하고 답변을 만들 수 없습니다.",
  kobis: "KOBIS 키가 없어 박스오피스와 러닝타임을 조회할 수 없습니다.",
  tmdb: "TMDB 키가 없어 신작 보충을 하지 못합니다. 나머지는 동작합니다.",
  kakao: "카카오 키가 없어 영화관 안내가 빠집니다. 영화 판단은 동작합니다.",
};

/** /api/health로 키 누락을 배너로 알린다 (F3019). 키 값은 서버가 애초에 보내지 않는다. */
async function checkHealth() {
  const box = document.getElementById("banners");
  box.replaceChildren();
  const r = await api("/api/health");
  if (!r.ok) {
    box.append(el("div", { class: "banner bad" }, el("b", {}, "서버 연결 안 됨"),
      "백엔드에 연결할 수 없습니다. backend 폴더에서 ..\\.venv\\Scripts\\python -m uvicorn app.main:app --port 8000 을 실행하세요."));
    return;
  }
  const missing = Object.entries(r.data.keys).filter(([, v]) => !v).map(([k]) => k);
  if (missing.length) {
    box.append(el("div", { class: missing.some(k => k === "gms" || k === "kobis") ? "banner bad" : "banner warn" },
      el("b", {}, "API 키 누락"), missing.map(k => KEY_IMPACT[k]).join(" "), el("span", { class: "muted" }, "루트 .env 파일을 확인하세요.")));
  }
  if (r.data.log_fail_test) {
    box.append(el("div", { class: "banner info" }, el("b", {}, "LOG_FAIL=1 시험 중"),
      "로그 저장을 일부러 실패시키는 중입니다. 대화와 실행 과정 패널은 그대로 동작해야 합니다(체크리스트 17)."));
  }
}

// ── 실행 과정 타임라인 (F3019) ─────────────────────────
const STEP_INFO = {
  analyze: ["llm", "LLM · 의도와 조건"], plan: ["code", "코드 · 호출 순서"], execute: ["code", "코드 · Tool 호출"],
  judge: ["code", "코드 · 시간 판단"], respond: ["llm", "LLM · 답변 문장"],
};
const TOOL_KO = { get_box_office: "박스오피스", get_movie_detail: "영화 상세", search_new_releases: "신작(TMDB)", search_theaters: "영화관" };
const CODE_KO = {
  INVALID_KEY: "키 확인 필요", UPSTREAM_ERROR: "응답 지연·서버 오류", NO_DATA: "결과 없음",
  NOT_FOUND: "찾을 수 없음", BAD_ARGUMENT: "입력 형식 오류", NO_RUNTIME: "러닝타임 없음",
};
const STRATEGY_KO = { expand_top_n: "후보 5편 → 10편 확장", add_tmdb: "TMDB 신작 보충", relax_genre: "장르 조건 완화" };
const argText = a => Object.entries(a || {}).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");

/** 이벤트 한 건 → 한 줄. 화면에 보일 말만 고른다. 반환 null이면 그리지 않는다. */
function eventRow(e) {
  const p = e.payload || {};
  const row = (cls, k, ...v) => el("div", { class: "ev " + cls }, el("span", { class: "k" }, k), el("span", { class: "v" }, ...v));
  switch (e.event) {
    case "llm.decision":
      return row("decision", "LLM 판단", el("b", {}, p.intent), " · ", p.reason || "");
    case "llm.error":
      return row("fail", "LLM 실패", "LLM(GMS) 연결에 실패했습니다.");
    case "memory.merge":
      return row("", "이전 조건", p.changed?.length ? `바뀐 조건만 갱신: ${p.changed.join(", ")}` : "이전 조건 그대로 사용");
    case "agent.ask":
      return row("decision", "되묻기", p.question || "");
    case "plan.created":
      return row("", "계획", (p.steps || []).map(s => TOOL_KO[s] || s).join(" → "),
        p.options?.top_n ? el("span", { class: "args" }, `  (후보 ${p.options.top_n}편)`) : null);
    case "tool.call":
      return row("", TOOL_KO[p.tool] || p.tool, el("span", { class: "args" }, argText(p.args)));
    case "tool.cache_hit":
      return row("cache", "cache 재사용", `${TOOL_KO[p.tool] || p.tool} `, el("span", { class: "args" }, argText(p.args)));
    case "tool.result":
      return p.ok ? row("", "  └ 결과", el("span", { class: "chip ok" }, "ok"), ` ${p.elapsed_ms}ms`)
                  : row("fail", "  └ 실패", el("span", { class: "chip bad" }, CODE_KO[p.code] || p.code), ` ${p.elapsed_ms}ms · 사유를 남기고 계속 진행`);
    case "judge.result":
      return row("", "판단", `맞음 ${(p.fit || []).length} · 안 맞음 ${p.unfit} · 제외 ${p.excluded}`,
        p.window_min != null ? el("span", { class: "args" }, `  (가능 ${p.window_min}분${p.shortest_need != null ? `, 가장 짧은 필요 ${p.shortest_need}분` : ""})`) : null);
    case "replan":
      return row("replan", `재계획 ${p.count}회`, STRATEGY_KO[p.strategy] || p.strategy, " · ", p.reason || "");
    case "replan.stop":
      return row("replan", "재계획 멈춤", p.reason || "");
    case "error":
      return row("fail", "오류", "처리 중 문제가 생겼습니다.");
    default:
      return null;
  }
}

/**
 * events[] → 단계 카드 목록. step.enter가 카드를 열고 step.exit가 소요 시간을 붙인다.
 * replan 이벤트 다음에 오는 plan 카드는 빨간색 "재계획 N회차"로 구분한다 (F3019).
 */
function renderTimeline(events, { showRunHeader = false } = {}) {
  const wrap = el("div", { class: "tl" });
  let card = null, body = null, pendingReplan = null, lastRun = null, toolCount = 0, cacheCount = 0;
  for (const e of events) {
    if (showRunHeader && e.run_id && e.run_id !== lastRun) {
      lastRun = e.run_id;
      wrap.append(el("div", { class: "tl-turn" }, `턴 run_id=${e.run_id}`));
    }
    if (e.event === "session.start") {
      wrap.append(el("div", { class: "tl-session" }, el("span", { class: "chip acc" }, "요청"), e.payload.message || ""));
      continue;
    }
    if (e.event === "session.end") {
      const p = e.payload;
      wrap.append(el("div", { class: "tl-session" }, el("span", { class: p.ok === false ? "chip bad" : "chip ok" }, p.ok === false ? "실패" : "완료"),
        p.elapsed_ms != null ? `${(p.elapsed_ms / 1000).toFixed(1)}초` : "",
        p.replan_count ? ` · 재계획 ${p.replan_count}회` : ""));
      continue;
    }
    if (e.event === "step.enter") {
      const [kind, label] = STEP_INFO[e.payload.step] || ["code", ""];
      const isReplan = e.payload.step === "plan" && pendingReplan;
      card = el("div", { class: `step ${kind}${isReplan ? " replan" : ""}` });
      card.append(el("div", { class: "step-h" },
        el("b", {}, e.payload.step), el("span", { class: `chip ${kind}` }, label),
        isReplan ? el("span", { class: "chip bad" }, `재계획 ${pendingReplan}회차`) : null,
        el("span", { class: "ms" })));
      body = card; pendingReplan = null;
      wrap.append(card);
      continue;
    }
    if (e.event === "step.exit") {
      if (card) card.querySelector(".ms").textContent = `${e.payload.elapsed_ms}ms`;
      continue;
    }
    if (e.event === "replan") pendingReplan = e.payload.count;
    if (e.event === "tool.call") toolCount++;
    if (e.event === "tool.cache_hit") cacheCount++;
    const r = eventRow(e);
    if (r) (body || wrap).append(r);
  }
  wrap.dataset.tools = toolCount; wrap.dataset.cache = cacheCount;
  return wrap;
}

function timelineLegend() {
  return el("div", { class: "tl-legend" },
    el("span", { class: "chip llm" }, "LLM 단계"), el("span", { class: "chip code" }, "코드 단계"),
    el("span", { class: "chip bad" }, "재계획 · 실패"), el("span", { class: "chip" }, "cache 재사용"));
}
