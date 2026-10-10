"use strict";

const $ = (id) => document.getElementById(id);

const classId = Number(new URLSearchParams(location.search).get("class")) || null;
let dash = null; // 학급 상세 데이터
let openStudentId = null;
let openUnitId = null;

// 서버에서 온 값(닉네임, 오류 이름 등)은 항상 textContent 로 넣는다. innerHTML 은 쓰지 않는다.
function el(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(opts)) {
    if (value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// 서버가 꺼져 있으면 fetch 는 영어 오류로 실패한다. 무엇을 해야 하는지 알려 준다.
async function api(method, url, body) {
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
  }
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* 본문이 없는 응답 */
  }
  if (!res.ok) {
    // 서버가 한국어 문구를 주는 경우(401/404/409)만 그대로 보여 주고, 입력 형식 오류는 일반 문구로 안내한다.
    const detail = typeof data?.detail === "string" ? data.detail : null;
    throw new ApiError(res.status, detail || "입력한 내용을 확인해 주세요.");
  }
  return data;
}

/* ---------- 표기 ---------- */

const pct = (value) => (value == null ? "–" : `${Number.isInteger(value) ? value : value.toFixed(1)}%`);

function formatAgo(iso) {
  if (!iso) return "제출 없음";
  // 서버 시각은 UTC 'YYYY-MM-DD HH:MM:SS' 이다.
  const then = new Date(iso.replace(" ", "T") + "Z").getTime();
  const minutes = (Date.now() - then) / 60000;
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${Math.floor(minutes)}분 전`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}시간 전`;
  const d = new Date(then);
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

const UNIT_STATUS = { planned: "예정", active: "진행 중", done: "끝남" };

function emptyState(emoji, text) {
  return el("div", { class: "empty-state" }, el("span", { class: "emoji", text: emoji }), el("p", { text }));
}

function tableWrap(headers, rows, numericColumns = []) {
  const head = el(
    "tr",
    {},
    ...headers.map((text, i) => el("th", { class: numericColumns.includes(i) ? "num" : null, text, scope: "col" }))
  );
  return el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, ...rows)));
}

// 값 막대. kind 가 'error' 이면 높을수록 주의, 'progress' 이면 높을수록 좋은 값이다 (색만으로 구분하지 않고 숫자도 적는다).
function meter(value, kind, label) {
  if (value == null) return el("span", { class: "muted", text: "–" });
  return el(
    "span",
    { class: `meter ${kind}`, role: "img", "aria-label": `${label} ${pct(value)}` },
    el("span", { class: "track" }, el("span", { class: "fill", style: `width:${Math.max(0, Math.min(100, value))}%` })),
    el("span", { class: "val", text: pct(value) })
  );
}

/* ---------- 로그인 ---------- */

function showTab(which) {
  const login = which === "login";
  $("tab-login").setAttribute("aria-selected", String(login));
  $("tab-register").setAttribute("aria-selected", String(!login));
  $("login-form").hidden = !login;
  $("register-form").hidden = login;
}
$("tab-login").addEventListener("click", () => showTab("login"));
$("tab-register").addEventListener("click", () => showTab("register"));

async function submitAuth(event, errorId, url, body) {
  event.preventDefault();
  $(errorId).textContent = "";
  try {
    await api("POST", url, body);
    location.reload();
  } catch (err) {
    $(errorId).textContent = err.message;
  }
}

$("login-form").addEventListener("submit", (e) =>
  submitAuth(e, "login-error", "/api/teacher/account/login", {
    login_id: $("login-id").value,
    password: $("login-pw").value,
  })
);
$("register-form").addEventListener("submit", (e) =>
  submitAuth(e, "register-error", "/api/teacher/account/register", {
    name: $("reg-name").value,
    login_id: $("reg-id").value,
    password: $("reg-pw").value,
  })
);

$("logout").addEventListener("click", async () => {
  await fetch("/api/teacher/account/logout", { method: "POST" });
  location.href = "/classes.html";
});

/* ---------- 학급 목록 ---------- */

function classCard(c) {
  const link = el(
    "a",
    { class: "class-card card", href: `/classes.html?class=${c.id}` },
    el("h2", { text: c.name }),
    el("p", { class: "muted small" }, "학급 코드 ", el("code", { text: c.join_code })),
    el(
      "dl",
      { class: "class-stats" },
      el("div", {}, el("dt", { text: "학생" }), el("dd", { text: `${c.students_active} / ${c.students_total}명 제출` })),
      el("div", {}, el("dt", { text: "오답률" }), el("dd", {}, meter(c.error_rate_pct, "warn", "오답률"))),
      el(
        "div",
        {},
        el("dt", { text: c.current_unit_title ? `현재 단원 · ${c.current_unit_title}` : "현재 단원" }),
        el("dd", {}, c.current_unit_title ? meter(c.current_unit_completion_pct, "progress", "단원 완료율") : el("span", { class: "muted", text: "아직 없음" }))
      )
    )
  );
  return link;
}

async function loadList() {
  $("list-view").hidden = false;
  try {
    const { classes } = await api("GET", "/api/teacher/classes");
    $("class-grid").replaceChildren(
      ...(classes.length
        ? classes.map(classCard)
        : [emptyState("🏫", "아직 학급이 없어요. 아래에서 새 학급을 만들거나, 이미 만든 학급을 가져와 주세요.")])
    );
  } catch (err) {
    $("list-error").textContent = err.message;
  }
}

$("new-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("new-error").textContent = "";
  try {
    const c = await api("POST", "/api/teacher/classes", { name: $("new-name").value });
    $("new-name").value = "";
    $("new-result").replaceChildren(
      el("p", {}, el("strong", { text: `${c.name} 학급을 만들었어요. ` }), "학생들에게 알려 줄 학급 코드는 ", el("code", { text: c.join_code }), " 예요."),
      el("p", {}, "학생 입장에는 학급 코드만 쓰고, 교사 키는 이 계정으로 로그인하면 따로 필요하지 않아요. 교사 키: ", el("code", { text: c.teacher_key }))
    );
    $("new-result").hidden = false;
    await loadList();
  } catch (err) {
    $("new-error").textContent = err.message;
  }
});

$("claim-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("claim-error").textContent = "";
  try {
    await api("POST", "/api/teacher/classes/claim", { join_code: $("claim-code").value, teacher_key: $("claim-key").value });
    $("claim-code").value = "";
    $("claim-key").value = "";
    await loadList();
  } catch (err) {
    $("claim-error").textContent = err.message;
  }
});

/* ---------- 학급 상세: 요약 ---------- */

function renderKpis(c) {
  const tiles = [
    ["제출한 학생", String(c.students_active), `/ ${c.students_total}명`, null],
    ["제출 횟수", String(c.submissions), "번", null],
    ["학급 오답률", c.error_rate_pct == null ? "–" : pct(c.error_rate_pct), "", `의심 지점이 나온 제출 ${c.error_submissions}번`],
    [
      c.current_unit_title ? `${c.current_unit_title} 완료` : "현재 단원 완료",
      c.current_unit_title ? pct(c.current_unit_completion_pct) : "–",
      "",
      c.current_unit_title ? "목표를 채운 학생 비율" : "진도 관리에서 단원을 만들어 주세요",
    ],
  ];
  $("d-kpis").replaceChildren(
    ...tiles.map(([label, value, unit, note]) =>
      el(
        "div",
        { class: "kpi" },
        el("span", { class: "label", text: label }),
        el("span", { class: "value" }, value, unit ? el("small", { text: unit }) : ""),
        note ? el("span", { class: "note", text: note }) : ""
      )
    )
  );
}

/* ---------- 학급 상세: 학생별 현황 ---------- */

const SORTERS = {
  error: (a, b) => (b.error_rate_pct ?? -1) - (a.error_rate_pct ?? -1) || a.nickname.localeCompare(b.nickname, "ko"),
  progress: (a, b) => (a.current_unit_progress_pct ?? 101) - (b.current_unit_progress_pct ?? 101) || a.nickname.localeCompare(b.nickname, "ko"),
  name: (a, b) => a.nickname.localeCompare(b.nickname, "ko"),
  recent: (a, b) => (b.last_submission_at || "").localeCompare(a.last_submission_at || "") || a.nickname.localeCompare(b.nickname, "ko"),
};

function renderStudents() {
  const students = [...dash.students].sort(SORTERS[$("sort").value]);
  if (students.length === 0) {
    $("students-body").replaceChildren(
      emptyState("🚪", `아직 입장한 학생이 없어요. 학급 코드 ${dash.class.join_code} 를 학생들에게 알려 주세요.`)
    );
    return;
  }
  const hasUnit = dash.class.current_unit_id != null;
  const rows = students.map((s) => {
    const name = el("button", { class: "link-btn", type: "button", "aria-expanded": String(openStudentId === s.id), text: s.nickname });
    name.addEventListener("click", () => toggleStudent(s.id));
    return el(
      "tr",
      { class: openStudentId === s.id ? "selected" : null },
      el("td", {}, name),
      el("td", { class: "num", text: String(s.submissions) }),
      el("td", {}, meter(s.error_rate_pct, "warn", `${s.nickname} 오답률`)),
      el("td", { class: "num", text: `${s.fixed_threads} / ${s.open_threads} / ${s.dropped_threads}` }),
      el("td", { class: "wrap", text: s.top_case_title || "–" }),
      el("td", {}, hasUnit ? meter(s.current_unit_progress_pct, "progress", `${s.nickname} 단원 진도`) : el("span", { class: "muted", text: "–" })),
      el("td", { text: formatAgo(s.last_submission_at) })
    );
  });
  const unitHead = hasUnit ? `${dash.class.current_unit_title} 진도` : "현재 단원 진도";
  $("students-body").replaceChildren(
    tableWrap(["이름", "제출", "오답률", "고침 / 고치는 중 / 그만둠", "가장 많이 틀린 유형", unitHead, "마지막 제출"], rows, [1, 3])
  );
}

$("sort").addEventListener("change", renderStudents);

async function toggleStudent(id) {
  openStudentId = openStudentId === id ? null : id;
  renderStudents();
  await renderStudentDetail();
}

async function renderStudentDetail() {
  const box = $("student-detail");
  if (openStudentId == null) {
    box.hidden = true;
    return;
  }
  try {
    const d = await api("GET", `/api/teacher/classes/${classId}/students/${openStudentId}`);
    const s = d.student;
    const ranking = d.error_ranking.length
      ? ol(d.error_ranking.map((r) => [`${r.rank}위`, r.title, r.share_pct / 100, `코드 ${r.threads}개 · ${pct(r.share_pct)}`]))
      : emptyState("🔍", "아직 분석된 오류가 없어요.");
    const progress = d.progress.length
      ? tableWrap(
          ["단원", "상태", "고친 코드 / 목표", "진도"],
          d.progress.map((p) =>
            el(
              "tr",
              {},
              el("td", { class: "wrap", text: p.title }),
              el("td", { text: UNIT_STATUS[p.status] }),
              el("td", { class: "num", text: `${p.fixed_threads} / ${p.target_fixed}` }),
              el("td", {}, meter(p.progress_pct, "progress", `${p.title} 진도`), p.is_done ? el("span", { class: "tag ok", text: "완료" }) : "")
            )
          ),
          [2]
        )
      : emptyState("📚", "아직 단원이 없어요.");
    box.replaceChildren(
      el(
        "div",
        { class: "panel-head" },
        el("div", { class: "grow" }, el("h2", { text: `${s.nickname} 학생 자세히` }), el("p", { class: "sub", text: `제출 ${s.submissions}번 · 오답률 ${pct(s.error_rate_pct)} · 마지막 제출 ${formatAgo(s.last_submission_at)}` })),
        el("button", { class: "btn btn-secondary btn-sm", type: "button", text: "닫기" })
      ),
      el("div", { class: "two-col flat" }, el("div", {}, el("h3", { text: "많이 틀린 유형 순위" }), ranking), el("div", {}, el("h3", { text: "단원별 진도" }), progress))
    );
    box.querySelector("button").addEventListener("click", () => toggleStudent(openStudentId));
    box.hidden = false;
  } catch (err) {
    box.replaceChildren(el("p", { class: "error", text: err.message }));
    box.hidden = false;
  }
}

// 순위 목록. rows: [순위 문구, 이름, 막대 비율(0~1), 값 문구]
function ol(rows) {
  return el(
    "ol",
    { class: "hbars ranked" },
    ...rows.map(([rank, name, ratio, value]) =>
      el(
        "li",
        { class: "hbar" },
        el("div", { class: "name" }, el("span", { class: "rank", text: rank }), name),
        el("div", { class: "track" }, el("div", { class: "fill", style: `--ratio:${Math.max(0, Math.min(1, ratio))}` }), el("span", { class: "val", text: value }))
      )
    )
  );
}

/* ---------- 학급 상세: 오답 유형 순위 ---------- */

function renderRanking() {
  const total = Math.max(1, dash.class.students_total);
  const rows = dash.error_ranking.slice(0, 10);
  if (rows.length === 0) {
    $("ranking-body").replaceChildren(emptyState("🔍", "아직 분석된 오류가 없어요."));
    return;
  }
  const box = el("div", {}, ol(rows.map((r) => [`${r.rank}위`, r.title, r.students / total, `${r.students}명 · 코드 ${r.threads}개`])));
  if (dash.error_ranking.length > rows.length) {
    box.append(el("p", { class: "muted small", style: "margin-top:12px", text: `상위 ${rows.length}개만 보여 줘요.` }));
  }
  $("ranking-body").replaceChildren(box);
}

/* ---------- 학급 상세: 진도 관리 ---------- */

async function mutate(action) {
  $("unit-error").textContent = "";
  try {
    await action();
    await loadDetail();
  } catch (err) {
    $("unit-error").textContent = err.message;
    await loadDetail();
  }
}

function renderUnits() {
  if (dash.units.length === 0) {
    $("units-body").replaceChildren(emptyState("📚", "아직 단원이 없어요. 아래에서 첫 단원을 추가하면 바로 ‘현재 단원’이 돼요."));
    return;
  }
  const rows = dash.units.map((u) => {
    const isCurrent = dash.class.current_unit_id === u.id;
    const status = el("select", { "aria-label": `${u.title} 상태` }, ...Object.entries(UNIT_STATUS).map(([v, t]) => el("option", { value: v, text: t })));
    status.value = u.status;
    status.addEventListener("change", () => mutate(() => api("PATCH", `/api/teacher/classes/${classId}/units/${u.id}`, { status: status.value })));

    const target = el("input", { type: "number", min: "1", max: "50", value: String(u.target_fixed), "aria-label": `${u.title} 목표 개수`, class: "narrow-input" });
    target.addEventListener("change", () => {
      const n = Number(target.value);
      if (!Number.isInteger(n) || n < 1 || n > 50) {
        target.value = String(u.target_fixed);
        return;
      }
      mutate(() => api("PATCH", `/api/teacher/classes/${classId}/units/${u.id}`, { target_fixed: n }));
    });

    const makeCurrent = el("button", { class: "btn btn-secondary btn-sm", type: "button", text: "현재 단원으로" });
    makeCurrent.addEventListener("click", () => mutate(() => api("PUT", `/api/teacher/classes/${classId}/current-unit`, { unit_id: u.id })));
    const viewStudents = el("button", { class: "btn btn-secondary btn-sm", type: "button", "aria-expanded": String(openUnitId === u.id), text: "학생별 보기" });
    viewStudents.addEventListener("click", () => toggleUnit(u.id));

    return el(
      "tr",
      { class: isCurrent ? "selected" : null },
      el("td", { class: "wrap" }, u.title, isCurrent ? el("span", { class: "tag latest", text: "현재 단원" }) : ""),
      el("td", {}, status),
      el("td", {}, target),
      el("td", { class: "num", text: u.students_total ? `${u.students_done} / ${u.students_total}명` : "–" }),
      el("td", {}, meter(u.avg_progress_pct, "progress", `${u.title} 평균 진도`)),
      el("td", { class: "actions" }, el("div", { class: "row-actions" }, isCurrent ? "" : makeCurrent, viewStudents))
    );
  });
  $("units-body").replaceChildren(tableWrap(["단원", "상태", "목표(고친 코드)", "완료 학생", "평균 진도", ""], rows, [3]));
}

async function toggleUnit(id) {
  openUnitId = openUnitId === id ? null : id;
  renderUnits();
  await renderUnitStudents();
}

async function renderUnitStudents() {
  const box = $("unit-students");
  if (openUnitId == null) {
    box.replaceChildren();
    return;
  }
  try {
    const { students } = await api("GET", `/api/teacher/classes/${classId}/units/${openUnitId}/students`);
    const unit = dash.units.find((u) => u.id === openUnitId);
    box.replaceChildren(
      el("h3", { class: "sub-title", text: `${unit ? unit.title : "단원"} · 학생별 진도 (느린 학생이 위)` }),
      students.length
        ? tableWrap(
            ["이름", "시작한 코드", "고친 코드 / 목표", "진도"],
            students.map((s) =>
              el(
                "tr",
                {},
                el("td", { text: s.nickname }),
                el("td", { class: "num", text: String(s.started_threads) }),
                el("td", { class: "num", text: `${s.fixed_threads} / ${s.target_fixed}` }),
                el("td", {}, meter(s.progress_pct, "progress", `${s.nickname} 진도`), s.is_done ? el("span", { class: "tag ok", text: "완료" }) : "")
              )
            ),
            [1, 2]
          )
        : emptyState("🚪", "아직 입장한 학생이 없어요.")
    );
  } catch (err) {
    box.replaceChildren(el("p", { class: "error", text: err.message }));
  }
}

$("unit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("unit-title").value;
  const target = Number($("unit-target").value);
  await mutate(async () => {
    await api("POST", `/api/teacher/classes/${classId}/units`, { title, target_fixed: target });
    $("unit-title").value = "";
  });
});

/* ---------- 학급 상세: 불러오기 ---------- */

async function loadDetail() {
  $("detail-view").hidden = false;
  try {
    dash = await api("GET", `/api/teacher/classes/${classId}/dashboard`);
  } catch (err) {
    if (err.status === 404) {
      $("d-error").textContent = "이 학급을 찾을 수 없어요. 내 학급 목록에서 다시 골라 주세요.";
    } else {
      $("d-error").textContent = err.message;
    }
    return;
  }
  $("d-error").textContent = "";
  document.title = `${dash.class.name} - 코드 웹 분석 도구`;
  $("d-name").textContent = dash.class.name;
  $("d-code").textContent = dash.class.join_code;
  $("d-updated").textContent = `${new Date().toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })} 기준`;
  renderKpis(dash.class);
  renderStudents();
  renderRanking();
  renderUnits();
  $("d-body").hidden = false;
  await Promise.all([renderStudentDetail(), renderUnitStudents()]);
}

$("d-refresh").addEventListener("click", loadDetail);

/* ---------- 시작 ---------- */

async function start() {
  let me = null;
  try {
    me = await api("GET", "/api/teacher/account/me");
  } catch (err) {
    if (err.status !== 401) {
      $("auth-view").hidden = false;
      $("login-error").textContent = err.message;
      return;
    }
  }
  if (!me) {
    $("auth-view").hidden = false;
    return;
  }
  $("who").textContent = me.name;
  $("who-chip").hidden = false;
  $("logout").hidden = false;
  $("to-teacher").hidden = true;
  $("app").hidden = false;
  if (classId) await loadDetail();
  else await loadList();
}

start();
