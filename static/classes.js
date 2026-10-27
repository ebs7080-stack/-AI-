"use strict";

const $ = (id) => document.getElementById(id);

let classes = []; // GET /api/teacher/classes
const rosters = new Map(); // 명단을 펼친 교실 id -> 학생 목록
const helpTimes = new Map(); // 교실 id -> 도움 시간 조절기 (화면을 다시 그려도 같은 것을 쓴다)

function helpTimeControl(cls) {
  if (!helpTimes.has(cls.id)) {
    const control = createHelpTimeControl(cls.id);
    control.update(cls.help_time);
    control.setHideNames(cls.hide_names);
    helpTimes.set(cls.id, control);
  }
  return helpTimes.get(cls.id);
}

// 서버에서 온 값(교실 이름, 학번, 학생 이름)은 항상 textContent 로 넣는다. innerHTML 은 쓰지 않는다.
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

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
  }
  if (res.status === 401) {
    location.href = "/teacher.html"; // 로그인이 풀렸으면 다시 로그인한다.
    throw new Error("로그인이 필요합니다.");
  }
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch {
    // 본문이 JSON 이 아니면 아래 오류 문구를 쓴다.
  }
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "입력을 확인해 주세요.");
  return data;
}

// 한 줄에 한 명. 학번 뒤에 공백·탭·쉼표로 띄우고 이름을 적을 수 있다 (엑셀에서 붙여 넣으면 탭이다).
function parseRoster(text) {
  const students = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const [, studentNo, name] = line.match(/^([^\s,]+)[\s,]*(.*)$/);
    if (studentNo.length > 20 || name.length > 20) {
      throw new Error(`학번과 이름은 20자까지 쓸 수 있어요: "${line}"`);
    }
    students.push({ student_no: studentNo, name });
  }
  return students;
}

function skippedNote(skipped) {
  return skipped.length ? `이미 있는 학번은 건너뛰었어요: ${skipped.join(", ")}` : "";
}

/* ---------- 학생 명단 ---------- */

function rosterTable(cls, students, error) {
  if (students.length === 0) {
    return el("p", { class: "muted", text: "아직 등록한 학생이 없어요. 아래에 학번을 넣어 주세요." });
  }
  const rows = students.map((s) => {
    const remove = el("button", {
      class: "btn btn-secondary btn-sm",
      type: "button",
      text: "삭제",
      "aria-label": `${s.student_no} ${s.name} 삭제`,
      // 이미 제출했거나 친구를 도운 학생은 기록이 남아야 해서 서버도 지우지 않는다.
      disabled: s.has_records ? "" : null,
      title: s.has_records ? "활동 기록이 있는 학생은 지울 수 없어요." : null,
    });
    remove.addEventListener("click", async () => {
      error.textContent = "";
      try {
        await api(`/api/teacher/classes/${cls.id}/students/${s.id}`, { method: "DELETE" });
        await reloadRoster(cls.id);
      } catch (err) {
        error.textContent = err.message;
      }
    });
    return el(
      "tr",
      {},
      el("td", {}, el("code", { text: s.student_no })),
      el("td", { text: s.name === s.student_no ? "–" : s.name }),
      el("td", { class: "num" }, remove)
    );
  });
  const head = el(
    "tr",
    {},
    el("th", { scope: "col", text: "학번" }),
    el("th", { scope: "col", text: "이름" }),
    el("th", { scope: "col", class: "num", text: "" })
  );
  return el(
    "div",
    { class: "table-wrap" },
    el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, ...rows))
  );
}

function rosterPanel(cls) {
  const students = rosters.get(cls.id);
  const error = el("p", { class: "error", role: "alert" });
  const note = el("p", { class: "muted small" });
  const inputId = `roster-add-${cls.id}`;
  const input = el("textarea", {
    id: inputId,
    class: "roster-input",
    placeholder: "10304 박민수\n10305",
    spellcheck: "false",
  });
  const form = el(
    "form",
    {},
    el("label", { for: inputId, text: "학생 추가 (한 줄에 한 명: 학번, 그리고 원하면 이름)" }),
    input,
    el("button", { class: "btn btn-primary btn-sm", type: "submit", text: "명단에 추가" })
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      const added = parseRoster(input.value);
      if (added.length === 0) throw new Error("추가할 학번을 적어 주세요.");
      const result = await api(`/api/teacher/classes/${cls.id}/students`, {
        method: "POST",
        body: { students: added },
      });
      rosters.set(cls.id, result.students);
      cls.student_count = result.students.length;
      render();
      // 다시 그린 뒤의 새 패널에 건너뛴 학번을 알려 준다.
      const fresh = document.querySelector(`[data-class-id="${cls.id}"] .roster-note`);
      if (fresh) fresh.textContent = skippedNote(result.skipped);
    } catch (err) {
      error.textContent = err.message;
    }
  });
  note.classList.add("roster-note");
  return el("div", { class: "roster" }, rosterTable(cls, students, error), form, note, error);
}

async function reloadRoster(classId) {
  const detail = await api(`/api/teacher/classes/${classId}`);
  rosters.set(classId, detail.students);
  const cls = classes.find((c) => c.id === classId);
  if (cls) cls.student_count = detail.students.length;
  render();
}

/* ---------- 교실 목록 ---------- */

function classCard(cls) {
  const open = rosters.has(cls.id);
  const toggle = el("button", {
    class: "btn btn-secondary btn-sm",
    type: "button",
    "aria-expanded": String(open),
    text: open ? "명단 접기" : "학생 명단 관리",
  });
  const error = el("p", { class: "error", role: "alert" });
  toggle.addEventListener("click", async () => {
    error.textContent = "";
    if (rosters.has(cls.id)) {
      rosters.delete(cls.id);
      render();
      return;
    }
    try {
      await reloadRoster(cls.id);
    } catch (err) {
      error.textContent = err.message;
    }
  });

  const card = el(
    "section",
    { class: "card class-card", "data-class-id": String(cls.id) },
    el(
      "div",
      { class: "class-card-head" },
      el(
        "div",
        { class: "grow" },
        el("h2", { text: cls.name }),
        el(
          "p",
          { class: "muted small" },
          "교실 코드 ",
          el("code", { class: "join-code", text: cls.join_code }),
          ` · 학생 ${cls.student_count}명`
        )
      ),
      el(
        "div",
        { class: "class-card-actions" },
        toggle,
        el("a", { class: "btn btn-primary btn-sm", href: `/dashboard.html?class=${cls.id}`, text: "대시보드 →" })
      )
    ),
    helpTimeControl(cls).node,
    error
  );
  if (open) card.append(rosterPanel(cls));
  return card;
}

function render() {
  if (classes.length === 0) {
    $("class-list").replaceChildren(
      el(
        "div",
        { class: "card empty-state" },
        el("span", { class: "emoji", text: "🚪" }),
        el("p", { text: "아직 만든 교실이 없어요. 위에서 첫 교실을 만들어 보세요." })
      )
    );
    return;
  }
  $("class-list").replaceChildren(...classes.map(classCard));
}

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("create-error").textContent = "";
  try {
    const created = await api("/api/teacher/classes", {
      method: "POST",
      body: { name: $("class-name").value, students: parseRoster($("class-roster").value) },
    });
    $("create-form").reset();
    classes.unshift({
      id: created.id,
      name: created.name,
      join_code: created.join_code,
      student_count: created.students.length,
      help_time: null,
      hide_names: created.hide_names,
    });
    rosters.set(created.id, created.students); // 방금 만든 교실은 명단을 펼쳐서 확인하게 한다.
    render();
    $("create-error").textContent = skippedNote(created.skipped);
  } catch (err) {
    $("create-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  await fetch("/api/teacher/logout", { method: "POST" });
  location.href = "/teacher.html";
});

(async function boot() {
  try {
    const me = await api("/api/teacher/me");
    $("who").textContent = `${me.name} 선생님`;
    classes = await api("/api/teacher/classes");
    render();
  } catch (err) {
    $("page-error").textContent = err.message;
  }
})();
