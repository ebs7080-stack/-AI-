"use strict";

const $ = (id) => document.getElementById(id);

const REFRESH_MS = 30000; // 수업 중에 켜 두면 알아서 갱신된다.
const MAX_DAYS_SHOWN = 90; // 이보다 길면 막대가 너무 가늘어져서 최근 90일만 그린다.
const MAX_ERROR_BARS = 10; // 나머지는 표에서 본다.
const WEEKDAYS = "일월화수목금토";

let days = 7; // null 이면 전체 기간
let data = null;
let showDailyTable = false;
let showErrorsTable = false;
let loading = false;

// 어느 교실의 대시보드인지는 주소의 ?class= 로 받는다. 없으면 교실 목록으로 돌아간다.
const classId = Number(new URLSearchParams(location.search).get("class"));

// 서버에서 온 값(학생 이름, 오류 이름 등)은 항상 textContent 로 넣는다. innerHTML 은 쓰지 않는다.
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

/* ---------- 표기 ---------- */

function formatMinutes(minutes) {
  if (minutes == null) return "–";
  if (minutes < 1) return "1분 미만";
  if (minutes < 60) return `${Math.round(minutes)}분`;
  if (minutes < 60 * 24) {
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    return m ? `${h}시간 ${m}분` : `${h}시간`;
  }
  const d = Math.floor(minutes / (60 * 24));
  const h = Math.floor((minutes % (60 * 24)) / 60);
  return h ? `${d}일 ${h}시간` : `${d}일`;
}

function formatAgo(iso) {
  if (!iso) return "제출 없음";
  const minutes = (Date.now() - new Date(iso).getTime()) / 60000;
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${Math.floor(minutes)}분 전`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}시간 전`;
  const d = new Date(iso);
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

// "2026-09-20" 을 시간대 변환 없이 그대로 읽는다 (서버가 이미 한국 날짜로 끊어 준 값이다).
function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shortDate(iso) {
  const d = parseDay(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function longDate(iso) {
  const d = parseDay(iso);
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAYS[d.getDay()]})`;
}

/* ---------- 툴팁 (마우스와 키보드 포커스 모두) ---------- */

const tip = $("tooltip");

function hideTip() {
  tip.hidden = true;
}

function showTip(anchor, lines) {
  const [value, ...rest] = lines;
  tip.replaceChildren(
    el("strong", { text: value }),
    ...rest.map((text) => el("div", { class: "sub", text }))
  );
  tip.hidden = false;
  tip.style.left = "0px";
  tip.style.top = "0px";
  const a = anchor.getBoundingClientRect();
  const t = tip.getBoundingClientRect();
  const left = Math.min(Math.max(8, a.left + a.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
  const above = a.top - t.height - 8;
  tip.style.left = `${left}px`;
  tip.style.top = `${above >= 8 ? above : a.bottom + 8}px`;
}

// anchor 는 툴팁을 붙일 요소이거나, 보여 줄 때 요소를 고르는 함수다.
function bindTip(node, anchor, lines) {
  const show = () => showTip(typeof anchor === "function" ? anchor() : anchor, lines);
  node.addEventListener("pointerenter", show);
  node.addEventListener("focus", show);
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("blur", hideTip);
}

/* ---------- 요약 타일 ---------- */

function renderKpis(summary) {
  const t = summary.threads;
  const started = t.open + t.fixed + t.dropped;
  const attempts = summary.median_attempts;
  const tiles = [
    ["제출한 학생", String(summary.students_active), `/ ${summary.students_total}명`, null],
    ["제출 횟수", String(summary.submissions), "번", null],
    ["다 고친 코드", String(t.fixed), `/ ${started}개`, `고치는 중 ${t.open} · 그만둠 ${t.dropped}`],
    [
      "고칠 때까지 제출",
      attempts == null ? "–" : String(attempts),
      attempts == null ? "" : "번",
      "다 고친 코드의 중앙값",
    ],
    ["고치는 데 걸린 시간", formatMinutes(summary.median_fix_minutes), "", "다 고친 코드의 중앙값"],
    ["친구 도움", String(summary.help_sessions), "번", "짝이 이어진 횟수"],
  ];
  $("kpis").replaceChildren(
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

/* ---------- 수업 전에 볼 것 (많이 막힌 오류 몇 개) ---------- */

function renderFocus() {
  const focus = data.focus;
  $("focus-sub").textContent =
    focus.scope === "unit"
      ? `지금 단원 "${focus.unit_title}"에서 학생들이 가장 많이 만난 오류예요. 단원 전체 기준이라 기간 선택과 상관없어요.`
      : "선택한 기간에 학생들이 가장 많이 만난 오류예요. 지금 단원을 정하면 그 단원 기준으로 바뀌어요.";

  if (focus.items.length === 0) {
    $("focus-body").replaceChildren(
      emptyState("✨", focus.scope === "unit" ? "이 단원에서는 아직 발견된 오류가 없어요." : "이 기간에는 발견된 오류가 없어요.")
    );
    return;
  }
  const total = data.summary.students_total;
  const items = focus.items.map((item, i) => {
    const stuck =
      item.stuck_now > 0
        ? el("span", { class: "state fixing", text: `지금 ${item.stuck_now}명 막힘` })
        : el("span", { class: "state idle", text: "지금 막힌 학생 없음" });
    return el(
      "li",
      { class: "focus-item" },
      el("span", { class: "focus-rank", text: String(i + 1), "aria-hidden": "true" }),
      el(
        "div",
        { class: "focus-main" },
        el("b", { class: "focus-label", text: item.label }),
        el("span", {
          class: "muted small",
          text: `${item.students}명${total ? ` / ${total}명` : ""}이 만났어요 · 코드 ${item.threads}개`,
        })
      ),
      stuck
    );
  });
  $("focus-body").replaceChildren(el("ol", { class: "focus-list" }, ...items));
}

/* ---------- 날짜별 제출 (세로 막대) ---------- */

// 눈금이 1, 2, 5 × 10ⁿ 처럼 떨어지게 잡는다. 제출 횟수는 정수라 간격은 1 이상이다.
function niceScale(max) {
  const raw = max / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  const step = Math.max(1, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow);
  const top = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = 0; v <= top; v += step) ticks.push(v);
  return { top, ticks };
}

function emptyState(emoji, text) {
  return el("div", { class: "empty-state" }, el("span", { class: "emoji", text: emoji }), el("p", { text }));
}

function dailyChart(rows, max) {
  const { top, ticks } = niceScale(max);
  const plot = el("div", { class: "plot" });
  for (const value of ticks) {
    plot.append(
      el(
        "div",
        { class: value === 0 ? "gridline base" : "gridline", style: `bottom:${(value / top) * 100}%` },
        el("span", { text: String(value) })
      )
    );
  }

  // 값은 가장 많이 제출한 날 하나에만 적고, 나머지는 눈금과 툴팁, 표가 알려 준다.
  const maxIndex = rows.findIndex((row) => row.submissions === max);
  const columns = el("div", { class: "columns" });
  rows.forEach((row, i) => {
    const pct = (row.submissions / top) * 100;
    const bar = el("div", { class: "bar", style: `height:${pct}%` });
    const col = el("div", {
      class: "col",
      tabindex: "0",
      role: "img",
      "aria-label": `${longDate(row.date)} 제출 ${row.submissions}번, 학생 ${row.students}명`,
    });
    col.append(bar);
    if (i === maxIndex) {
      col.append(el("span", { class: "cap", text: String(row.submissions), style: `bottom:calc(${pct}% + 6px)` }));
    }
    // 막대 꼭대기 위에 툴팁을 띄우되, 제출이 0인 날은 높이가 없으니 칸 전체를 기준으로 삼는다.
    bindTip(col, () => (row.submissions > 0 ? bar : col), [
      `${row.submissions}번 제출`,
      longDate(row.date),
      `학생 ${row.students}명`,
    ]);
    columns.append(col);
  });
  plot.append(columns);

  // 날짜 이름표는 최대 8개 안팎만 붙이고, 가장 최근 날을 기준으로 잡는다.
  const every = Math.ceil(rows.length / 8);
  const xaxis = el(
    "div",
    { class: "xaxis", "aria-hidden": "true" },
    ...rows.map((row, i) => el("span", { text: (rows.length - 1 - i) % every === 0 ? shortDate(row.date) : "" }))
  );
  return el("div", { class: "chart" }, plot, xaxis);
}

function dailyTable(rows) {
  const body = [...rows].reverse().map((row) =>
    el(
      "tr",
      {},
      el("td", { text: longDate(row.date) }),
      el("td", { class: "num", text: String(row.submissions) }),
      el("td", { class: "num", text: String(row.students) })
    )
  );
  return tableWrap(["날짜", "제출 횟수", "제출한 학생"], body, [1, 2]);
}

function tableWrap(headers, rows, numericColumns = []) {
  const head = el(
    "tr",
    {},
    ...headers.map((text, i) => el("th", { class: numericColumns.includes(i) ? "num" : null, text, scope: "col" }))
  );
  return el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, ...rows)));
}

function renderDaily() {
  const all = data.daily;
  const rows = all.slice(-MAX_DAYS_SHOWN);
  $("daily-note").textContent = all.length > rows.length ? `최근 ${MAX_DAYS_SHOWN}일만 보여 줘요.` : "";
  const max = Math.max(0, ...rows.map((row) => row.submissions));

  $("daily-toggle").hidden = max === 0;
  $("daily-toggle").textContent = showDailyTable ? "차트로 보기" : "표로 보기";
  $("daily-toggle").setAttribute("aria-pressed", String(showDailyTable));

  let content;
  if (max === 0) content = emptyState("📭", "이 기간에는 제출된 코드가 없어요.");
  else content = showDailyTable ? dailyTable(rows) : dailyChart(rows, max);
  $("daily-body").replaceChildren(content);
}

/* ---------- 오류 유형 (가로 막대) ---------- */

function errorBars(errors) {
  // 막대 길이는 교실 전체 학생 수 대비 비율이라, 교실 규모와 상관없이 같은 기준으로 읽힌다.
  const scale = Math.max(data.summary.students_total, errors[0].students, 1);
  const shown = errors.slice(0, MAX_ERROR_BARS);
  const list = el("ul", { class: "hbars" });
  for (const error of shown) {
    const row = el(
      "li",
      { class: "hbar", tabindex: "0", "aria-label": `${error.label}: 학생 ${error.students}명, 코드 ${error.threads}개` },
      el("div", { class: "name", text: error.label }),
      el(
        "div",
        { class: "track" },
        el("div", { class: "fill", style: `--ratio:${error.students / scale}` }),
        el("span", { class: "val", text: `${error.students}명` })
      )
    );
    bindTip(row, row, [`${error.students}명`, error.label, `코드 ${error.threads}개`]);
    list.append(row);
  }
  const box = el("div", {}, list);
  if (errors.length > shown.length) {
    box.append(
      el("p", { class: "muted small", style: "margin-top:12px", text: `상위 ${shown.length}개만 보여 줘요. 전체 ${errors.length}개는 표로 볼 수 있어요.` })
    );
  }
  return box;
}

function errorsTable(errors) {
  const rows = errors.map((error) =>
    el(
      "tr",
      {},
      el("td", { class: "wrap", text: error.label }),
      el("td", { class: "num", text: String(error.students) }),
      el("td", { class: "num", text: String(error.threads) }),
      el("td", {}, el("code", { text: error.case_id }))
    )
  );
  return tableWrap(["오류 유형", "만난 학생", "코드", "case id"], rows, [1, 2]);
}

function renderErrors() {
  const errors = data.errors;
  $("errors-toggle").hidden = errors.length === 0;
  $("errors-toggle").textContent = showErrorsTable ? "차트로 보기" : "표로 보기";
  $("errors-toggle").setAttribute("aria-pressed", String(showErrorsTable));

  let content;
  if (errors.length === 0) content = emptyState("🔍", "이 기간에 분석된 오류가 없어요.");
  else content = showErrorsTable ? errorsTable(errors) : errorBars(errors);
  $("errors-body").replaceChildren(content);
}

/* ---------- 단원 진도 ---------- */

const UNIT_STATUS_LABELS = { planned: "예정", active: "수업 중", done: "마침" };

// 되돌리기 어려운 동작은 같은 버튼을 4초 안에 한 번 더 눌러야 실행된다 (학생 화면과 같은 방식).
function armTwice(button, armedLabel, action) {
  const original = button.textContent;
  let timer = null;
  button.addEventListener("click", async () => {
    if (timer === null) {
      button.textContent = armedLabel;
      timer = setTimeout(() => {
        button.textContent = original;
        timer = null;
      }, 4000);
      return;
    }
    clearTimeout(timer);
    timer = null;
    button.textContent = original;
    await action();
  });
}

function unitRow(unit) {
  const pct = unit.done_pct ?? 0;
  const head = el(
    "div",
    { class: "unit-head" },
    el("b", { class: "unit-title", text: unit.title }),
    el("span", { class: `state ${unit.is_current ? "helping" : "idle"}`, text: UNIT_STATUS_LABELS[unit.status] })
  );
  if (unit.is_current) head.append(el("span", { class: "unit-now", text: "지금 단원" }));

  // 막대는 '목표를 채운 학생 비율' 이다. 학생별 진도의 평균은 아래 설명줄에 따로 적는다.
  const bar = el(
    "div",
    { class: "unit-bar" },
    el("span", { class: "muted small", text: "목표 달성" }),
    el(
      "div",
      { class: "track", role: "img", "aria-label": `${unit.title}: 목표를 채운 학생 ${pct}%` },
      el("div", { class: "fill", style: `--ratio:${pct / 100}` }),
      el("span", { class: "val", text: unit.done_pct == null ? "–" : `${pct}%` })
    )
  );

  const counts =
    unit.students_total === 0
      ? "등록한 학생이 없어요."
      : `${unit.students_done}/${unit.students_total}명이 목표를 채웠어요 · 평균 진도 ${unit.avg_progress_pct}%`;
  const detail = el(
    "p",
    { class: "muted small unit-detail", text: `목표 ${unit.target_fixed}개 · ${counts} · 제출된 코드 ${unit.threads}개 (다 고침 ${unit.fixed_threads}개)` }
  );

  const actions = el("div", { class: "unit-actions" });
  if (!unit.is_current) {
    const pick = el("button", { class: "btn btn-secondary btn-sm", type: "button", text: "지금 단원으로" });
    pick.addEventListener("click", () => sendUnit("/current-unit", "PUT", { unit_id: unit.id }));
    actions.append(pick);
  }

  // 목표 개수는 그 자리에서 고친다 (진도의 분모라서 자주 만지게 된다).
  const targetId = `unit-target-${unit.id}`;
  const target = el("input", {
    id: targetId,
    class: "help-time-minutes unit-target",
    type: "number",
    min: "1",
    max: "50",
    value: String(unit.target_fixed),
    inputmode: "numeric",
    "aria-label": `${unit.title} 목표 개수`,
  });
  const save = el("button", { class: "btn btn-secondary btn-sm", type: "button", text: "목표 저장" });
  save.addEventListener("click", () => {
    const value = Number(target.value);
    if (!Number.isInteger(value) || value < 1 || value > 50) {
      $("unit-error").textContent = "목표 개수는 1부터 50까지의 정수로 적어 주세요.";
      return;
    }
    if (value !== unit.target_fixed) sendUnit(`/units/${unit.id}`, "PATCH", { target_fixed: value });
  });
  actions.append(el("span", { class: "unit-target-wrap" }, el("span", { class: "muted small", text: "목표" }), target, el("span", { class: "muted small", text: "개" })), save);

  // 제출이 하나도 없는 단원만 지울 수 있다 (서버도 같은 규칙으로 막는다).
  if (unit.threads === 0) {
    const remove = el("button", { class: "btn btn-secondary btn-sm unit-remove", type: "button", text: "삭제" });
    armTwice(remove, "한 번 더 누르면 지워요", () => sendUnit(`/units/${unit.id}`, "DELETE"));
    actions.append(remove);
  }

  return el("li", { class: "unit-item" }, head, bar, detail, actions);
}

function renderUnits() {
  const units = data.units;
  if (units.length === 0) {
    $("units-body").replaceChildren(
      emptyState("📚", "아직 만든 단원이 없어요. 위에서 단원을 추가하면 그때부터 진도가 쌓여요.")
    );
    return;
  }
  $("units-body").replaceChildren(el("ul", { class: "unit-list" }, ...units.map(unitRow)));
}

// 단원을 고친 뒤에는 대시보드를 다시 불러 학생별 진도까지 함께 맞춘다.
async function sendUnit(path, method, body) {
  $("unit-error").textContent = "";
  try {
    const res = await fetch(`/api/teacher/classes/${classId}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) return void (location.href = "/teacher.html");
    if (!res.ok) {
      // 422 의 detail 은 항목별 목록이라 그대로 보여 주면 읽기 어렵다. 문자열일 때만 쓴다.
      const body = await res.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      throw new Error(detail ?? "단원을 저장하지 못했어요. 적은 내용을 다시 확인해 주세요.");
    }
    await load({ dim: true });
    return true;
  } catch (err) {
    $("unit-error").textContent = err.message;
    return false;
  }
}

/* ---------- 학생별 현황 ---------- */

const STATE_LABELS = { fixing: "🛠 고치는 중", helping: "🤝 도와주는 중", idle: "💤 대기 중" };

function stateCell(student) {
  const cell = el("td", {}, el("span", { class: `state ${student.state}`, text: STATE_LABELS[student.state] }));
  if (student.state === "fixing") {
    const since = student.current_minutes < 1 ? "방금 시작" : `${formatMinutes(student.current_minutes)}째`;
    cell.append(el("span", { class: "state-detail", text: `${student.current_attempts}번 제출 · ${since}` }));
  }
  return cell;
}

// 지금 단원의 진도. 단원을 정해 두지 않은 교실에서는 칸을 비운다.
function progressCell(student) {
  if (student.unit_progress_pct == null) return el("td", { class: "num muted", text: "–" });
  const cell = el(
    "td",
    { class: "unit-cell" },
    el(
      "div",
      { class: "track" },
      el("div", { class: "fill", style: `--ratio:${student.unit_progress_pct / 100}` }),
      el("span", { class: "val", text: `${student.unit_progress_pct}%` })
    )
  );
  cell.append(el("span", { class: "state-detail", text: `${student.unit_fixed}/${student.unit_target}개` }));
  return cell;
}

// 오답률: 분석기가 오류를 찾은 제출의 비율. 제출이 없으면 비운다.
function errorRateCell(student) {
  if (student.error_rate_pct == null) return el("td", { class: "num muted", text: "–" });
  return el(
    "td",
    { class: "num" },
    `${student.error_rate_pct}%`,
    el("span", { class: "state-detail", text: `${student.error_submissions}/${student.submissions}` })
  );
}

function topErrorCell(student) {
  if (!student.top_error) return el("td", { class: "muted", text: "–" });
  return el("td", { class: "wrap", text: student.top_error.label });
}

function renderStudents() {
  const students = data.students;
  const fixing = students.filter((s) => s.state === "fixing").length;
  const helping = students.filter((s) => s.state === "helping").length;
  const current = data.units.find((u) => u.is_current);
  const unitNote = current
    ? ` 단원 진도는 지금 단원 "${current.title}" 기준이고 기간과 상관없어요.`
    : " 지금 단원을 정하면 단원 진도도 함께 보여요.";
  $("students-sub").textContent =
    students.length === 0
      ? ""
      : `지금 ${fixing}명이 고치는 중, ${helping}명이 도와주는 중이에요. 오른쪽 숫자는 선택한 기간 기준이에요. 오답률은 오류가 발견된 제출의 비율이에요(분석기가 확실한 오류만 찾아서 실제보다 낮게 나올 수 있어요).${unitNote}`;

  if (students.length === 0) {
    $("students-body").replaceChildren(
      emptyState("🚪", "아직 등록한 학생이 없어요. 내 교실 화면에서 학생들의 학번을 등록해 주세요.")
    );
    return;
  }
  const rows = students.map((s) =>
    el(
      "tr",
      {},
      el("td", {}, el("code", { text: s.student_no })),
      el("td", { text: s.name === s.student_no ? "–" : s.name }),
      stateCell(s),
      progressCell(s),
      el("td", { class: "num", text: String(s.submissions) }),
      errorRateCell(s),
      topErrorCell(s),
      el("td", { class: "num", text: String(s.fixed) }),
      el("td", { class: "num", text: String(s.dropped) }),
      el("td", { class: "num", text: String(s.helped) }),
      el("td", { class: "num", text: String(s.was_helped) }),
      el("td", { text: formatAgo(s.last_submission_at) })
    )
  );
  $("students-body").replaceChildren(
    tableWrap(
      ["학번", "이름", "지금 상태", "단원 진도", "제출", "오답률", "자주 만난 오류", "다 고침", "그만둠", "도와줌", "도움 받음", "마지막 제출"],
      rows,
      [4, 5, 7, 8, 9, 10]
    )
  );
}

/* ---------- 불러오기 ---------- */

const helpTime = classId ? createHelpTimeControl(classId) : null;
if (helpTime) $("help-time-slot").append(helpTime.node);

function render() {
  $("who").textContent = data.class.name;
  $("class-code").textContent = data.class.join_code;
  $("updated").textContent = `${new Date().toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })} 기준`;
  helpTime.update(data.help_time);
  helpTime.setHideNames(data.class.hide_names);
  renderKpis(data.summary);
  renderFocus();
  renderDaily();
  renderErrors();
  renderUnits();
  renderStudents();
  $("dash-body").hidden = false;
}

async function load({ dim }) {
  if (loading) return;
  loading = true;
  if (dim) $("dash-body").classList.add("loading");
  try {
    let res;
    try {
      res = await fetch(`/api/teacher/classes/${classId}/dashboard${days ? `?days=${days}` : ""}`);
    } catch {
      throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
    }
    if (res.status === 401) {
      location.href = "/teacher.html"; // 로그인이 풀렸으면 다시 로그인한다.
      return;
    }
    if (res.status === 404) {
      location.href = "/classes.html"; // 내 교실이 아니거나 없는 교실이다.
      return;
    }
    if (!res.ok) throw new Error("통계를 불러오지 못했어요. 잠시 뒤에 다시 시도해 주세요.");
    data = await res.json();
    hideTip();
    render();
    $("dash-error").textContent = "";
  } catch (err) {
    $("dash-error").textContent = err.message;
  } finally {
    loading = false;
    $("dash-body").classList.remove("loading");
  }
}

for (const button of document.querySelectorAll(".filters button")) {
  button.addEventListener("click", () => {
    days = button.dataset.days === "all" ? null : Number(button.dataset.days);
    for (const other of document.querySelectorAll(".filters button")) {
      other.setAttribute("aria-pressed", String(other === button));
    }
    load({ dim: true });
  });
}

$("unit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const ok = await sendUnit("/units", "POST", {
    title: $("unit-title").value,
    target_fixed: Number($("unit-target").value) || 1,
  });
  if (ok) $("unit-form").reset();
});

$("daily-toggle").addEventListener("click", () => {
  showDailyTable = !showDailyTable;
  renderDaily();
});

$("errors-toggle").addEventListener("click", () => {
  showErrorsTable = !showErrorsTable;
  renderErrors();
});

$("logout").addEventListener("click", async () => {
  await fetch("/api/teacher/logout", { method: "POST" });
  location.href = "/teacher.html";
});

window.addEventListener("scroll", hideTip, { passive: true });

if (!classId) location.replace("/classes.html");
else load({ dim: false });
setInterval(() => {
  if (!document.hidden && classId) load({ dim: false });
}, REFRESH_MS);
