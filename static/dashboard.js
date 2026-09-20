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
  // 막대 길이는 학급 전체 학생 수 대비 비율이라, 학급 규모와 상관없이 같은 기준으로 읽힌다.
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

function renderStudents() {
  const students = data.students;
  const fixing = students.filter((s) => s.state === "fixing").length;
  const helping = students.filter((s) => s.state === "helping").length;
  $("students-sub").textContent =
    students.length === 0
      ? ""
      : `지금 ${fixing}명이 고치는 중, ${helping}명이 도와주는 중이에요. 오른쪽 숫자는 선택한 기간 기준이에요.`;

  if (students.length === 0) {
    $("students-body").replaceChildren(
      emptyState("🚪", `아직 입장한 학생이 없어요. 학급 코드 ${data.class.join_code} 를 학생들에게 알려 주세요.`)
    );
    return;
  }
  const rows = students.map((s) =>
    el(
      "tr",
      {},
      el("td", { text: s.nickname }),
      stateCell(s),
      el("td", { class: "num", text: String(s.submissions) }),
      el("td", { class: "num", text: String(s.fixed) }),
      el("td", { class: "num", text: String(s.dropped) }),
      el("td", { class: "num", text: String(s.helped) }),
      el("td", { class: "num", text: String(s.was_helped) }),
      el("td", { text: formatAgo(s.last_submission_at) })
    )
  );
  $("students-body").replaceChildren(
    tableWrap(["닉네임", "지금 상태", "제출", "다 고침", "그만둠", "도와줌", "도움 받음", "마지막 제출"], rows, [2, 3, 4, 5, 6])
  );
}

/* ---------- 불러오기 ---------- */

function render() {
  $("who").textContent = data.class.name;
  $("class-code").textContent = data.class.join_code;
  $("updated").textContent = `${new Date().toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })} 기준`;
  renderKpis(data.summary);
  renderDaily();
  renderErrors();
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
      res = await fetch(`/api/teacher/dashboard${days ? `?days=${days}` : ""}`);
    } catch {
      throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
    }
    if (res.status === 401) {
      location.href = "/teacher.html"; // 로그인이 풀렸으면 다시 로그인한다.
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

load({ dim: false });
setInterval(() => {
  if (!document.hidden) load({ dim: false });
}, REFRESH_MS);
